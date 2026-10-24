import os
import json
import sqlite3
import uuid
import secrets
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(Path(__file__).resolve().parent / ".env")
load_dotenv(BASE_DIR / ".env")

DB = os.getenv("DB_PATH", "journey.db")

app = FastAPI(title="User Journey Analyzer")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

def db():
    c = sqlite3.connect(DB)
    # Tabela de sites cadastrados com chave secreta
    c.execute("""CREATE TABLE IF NOT EXISTS sites (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        site_id TEXT UNIQUE,
        name TEXT,
        url TEXT,
        secret_key TEXT UNIQUE,
        created_at TEXT
    )""")

    # Tabela de eventos
    c.execute("""CREATE TABLE IF NOT EXISTS events(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        site_id TEXT DEFAULT 'default',
        session_id TEXT, url TEXT, ts INTEGER, type TEXT, payload TEXT, created TEXT)""")
    
    # Garante que a coluna site_id exista caso o banco tenha sido criado em versão anterior
    try:
        c.execute("ALTER TABLE events ADD COLUMN site_id TEXT DEFAULT 'default'")
    except Exception:
        pass

    c.execute("CREATE INDEX IF NOT EXISTS idx_events_site ON events(site_id, ts)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_id, ts)")
    return c

class Ingest(BaseModel):
    sessionId: str
    url: str
    events: List[dict]
    siteId: Optional[str] = "default"

class CreateSite(BaseModel):
    url: str
    name: Optional[str] = None

class AuthKey(BaseModel):
    secret_key: str

@app.post("/api/sites")
def create_site(data: CreateSite):
    clean_url = data.url.strip()
    if not clean_url.startswith("http://") and not clean_url.startswith("https://"):
        clean_url = "https://" + clean_url
        
    domain = clean_url.split("//")[-1].split("/")[0]
    site_name = (data.name or "").strip() or domain

    # Gera identificador do site e chave de acesso privada
    site_id = f"site_{uuid.uuid4().hex[:8]}"
    secret_key = f"sec_{secrets.token_urlsafe(16)}"
    created_at = datetime.utcnow().isoformat()

    c = db()
    c.execute(
        "INSERT INTO sites (site_id, name, url, secret_key, created_at) VALUES (?, ?, ?, ?, ?)",
        (site_id, site_name, clean_url, secret_key, created_at)
    )
    c.commit()
    c.close()

    tracker_code = f'<script src="/tracker.js" data-site="{site_id}" async></script>'

    return {
        "ok": True,
        "site_id": site_id,
        "name": site_name,
        "url": clean_url,
        "secret_key": secret_key,
        "tracker_code": tracker_code
    }

@app.post("/api/sites/auth")
def auth_site(data: AuthKey):
    key = data.secret_key.strip()
    c = db()
    row = c.execute("SELECT site_id, name, url, created_at FROM sites WHERE secret_key=?", (key,)).fetchone()
    c.close()
    if not row:
        return {"ok": False, "error": "Chave secreta inválida. Verifique o código e tente novamente."}
    return {
        "ok": True,
        "site": {
            "site_id": row[0],
            "name": row[1],
            "url": row[2],
            "created_at": row[3],
            "secret_key": key
        }
    }

@app.post("/api/events")
async def ingest(body: Ingest):
    c = db()
    site_id = body.siteId or "default"
    for e in body.events:
        c.execute(
            "INSERT INTO events(site_id,session_id,url,ts,type,payload,created) VALUES(?,?,?,?,?,?,?)",
            (site_id, body.sessionId, e.get("url", body.url), e.get("ts"), e.get("type"), json.dumps(e), datetime.utcnow().isoformat())
        )
    c.commit()
    c.close()
    return {"ok": True, "count": len(body.events)}

@app.get("/api/sessions")
def sessions(secret_key: Optional[str] = Query(None)):
    c = db()
    target_site_id = "default"

    # Se uma chave secreta for fornecida, busca apenas as sessões daquele site
    if secret_key and secret_key.strip() != "default":
        row = c.execute("SELECT site_id FROM sites WHERE secret_key=?", (secret_key.strip(),)).fetchone()
        if row:
            target_site_id = row[0]
        else:
            c.close()
            return []  # Chave inválida não visualiza nada

    rows = c.execute("""
        SELECT session_id, COUNT(*), MIN(ts), MAX(ts), MAX(url)
        FROM events 
        WHERE site_id=?
        GROUP BY session_id 
        ORDER BY MAX(ts) DESC 
        LIMIT 100
    """, (target_site_id,)).fetchall()
    c.close()

    return [
        {
            "session_id": r[0],
            "events": r[1],
            "started_at": r[2],
            "last_active": r[3],
            "last_url": r[4]
        }
        for r in rows
    ]

@app.get("/api/sessions/{sid}")
def session_detail(sid: str):
    c = db()
    rows = c.execute("SELECT ts,type,payload FROM events WHERE session_id=? ORDER BY ts", (sid,)).fetchall()
    c.close()
    return [{"ts": r[0], "type": r[1], "data": json.loads(r[2])} for r in rows]

@app.post("/api/analyze/{sid}")
async def analyze(sid: str, provider: Optional[str] = Query(None)):
    from analyzer import analyze_session
    result = await analyze_session(sid, provider=provider)
    return result

# Rota para disponibilizar o script do tracker para os sites dos clientes
@app.get("/tracker.js")
def get_tracker_script():
    tracker_path = BASE_DIR / "tracker" / "tracker.js"
    return FileResponse(tracker_path, media_type="application/javascript")

# Servir a interface web (Dashboard) e página de demonstração
@app.get("/", response_class=HTMLResponse)
def serve_dashboard():
    dashboard_path = BASE_DIR / "frontend" / "index.html"
    if dashboard_path.exists():
        return FileResponse(dashboard_path)
    return HTMLResponse("<h1>User Journey Analyzer</h1><p>Frontend não encontrado.</p>")

@app.get("/demo", response_class=HTMLResponse)
def serve_demo():
    demo_path = BASE_DIR / "frontend" / "demo.html"
    return FileResponse(demo_path)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
