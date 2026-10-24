import os
import json
import sqlite3
from pathlib import Path
from typing import Optional
from dotenv import load_dotenv

# Carrega .env local ou da raiz
load_dotenv(Path(__file__).resolve().parent / ".env")
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

DB = os.getenv("DB_PATH", "journey.db")

SYSTEM = """Você é um especialista em vendas e experiência do cliente conversando de forma bem informal, simples e descontraída com o dono de um site (que é leigo e não entende nada de termos técnicos de programação).

Analise os eventos de navegação e monte um parecer em JSON com estes campos:
- comportamento_observado: Um resumo contando a história do que o visitante fez, em tom de conversa de amigos (Ex: 'A pessoa entrou direto na sua página de oferta, rolou rapidinho até o preço, clicou no botão de comprar, mas ficou pensando por mais de 1 minuto e foi embora sem finalizar').
- possiveis_causas: Lista curta com hipóteses bem simples do porquê ela não comprou (Ex: 'Talvez achou o frete ou o preço salgado', 'Pode ter ficado com dúvida sobre como recebe o ebook', 'O botão pode não ter levado para o checkout na hora').
- evidencias: O que nos faz pensar isso em palavras simples (Ex: 'Ele clicou no botão de comprar e depois rolou procurando mais detalhes', 'Passou mais de 1 minuto na página e não concluiu').
- nivel_confianca: baixo/medio/alto
- recomendacoes: Dicas práticas e fáceis de vendas para o dono aplicar (Ex: 'Coloque depoimentos de clientes perto do botão de compra', 'Deixe bem claro se o ebook chega no e-mail ou WhatsApp').

Regras de ouro:
- PROIBIDO usar termos técnicos como DOM, SVG, âncora, elemento, payload, tag, classe CSS, preflight, etc.
- Fale sempre como se estivesse explicando para o seu tio que tem uma loja.
- Diferencie o que é certeza do que é só palpite."""

async def analyze_with_gemini(timeline: list, api_key: str, preferred_model: str = "gemini-3.5-flash-lite"):
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=api_key)
    prompt = f"Eventos da sessao:\n{json.dumps(timeline, ensure_ascii=False, indent=2)}"
    
    # Lista de modelos com fallback automático caso um modelo esteja sobrecarregado (503)
    candidate_models = [
        preferred_model,
        "gemini-3.5-flash-lite",
        "gemini-3.8-flash",
    ]
    seen = set()
    candidate_models = [m for m in candidate_models if not (m in seen or seen.add(m))]

    last_error = None
    for model_name in candidate_models:
        try:
            response = client.models.generate_content(
                model=model_name,
                contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction=SYSTEM,
                    response_mime_type="application/json"
                )
            )
            return json.loads(response.text), model_name
        except Exception as e:
            last_error = e
            continue

    raise last_error

async def analyze_with_openai(timeline: list, api_key: str, model_name: str = "gpt-4o-mini"):
    from openai import AsyncOpenAI

    client = AsyncOpenAI(api_key=api_key)
    resp = await client.chat.completions.create(
        model=model_name,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": "Eventos da sessao:\n" + json.dumps(timeline, ensure_ascii=False)},
        ],
    )
    return json.loads(resp.choices[0].message.content)

async def analyze_session(sid: str, provider: Optional[str] = None):
    c = sqlite3.connect(DB)
    rows = c.execute("SELECT ts,type,payload FROM events WHERE session_id=? ORDER BY ts", (sid,)).fetchall()
    c.close()
    
    if not rows:
        return {
            "error": "Sessão não encontrada ou sem eventos gravados.",
            "comportamento_observado": "Nenhum evento registrado para esta sessão.",
            "possiveis_causas": ["A sessão foi encerrada antes do envio dos eventos ou o ID é inválido."],
            "evidencias": [],
            "nivel_confianca": "baixo",
            "recomendacoes": ["Verifique se o tracker.js está configurado com o endpoint correto."]
        }

    timeline = [{"ts": r[0], "type": r[1], "data": json.loads(r[2])} for r in rows]

    gemini_key = os.getenv("GEMINI_API_KEY")
    openai_key = os.getenv("OPENAI_API_KEY")

    selected_provider = (provider or os.getenv("LLM_PROVIDER") or "").lower()
    
    if not selected_provider:
        if gemini_key:
            selected_provider = "gemini"
        elif openai_key:
            selected_provider = "openai"
        else:
            selected_provider = "gemini"

    try:
        if selected_provider == "gemini":
            if not gemini_key:
                return {
                    "error": "GEMINI_API_KEY não configurada. Defina GEMINI_API_KEY no arquivo .env ou no ambiente.",
                    "provider": "gemini"
                }
            model_name = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")
            result, used_model = await analyze_with_gemini(timeline, gemini_key, model_name)
            result["provider_used"] = f"Google Gemini ({used_model})"
            return result

        elif selected_provider == "openai":
            if not openai_key:
                return {
                    "error": "OPENAI_API_KEY não configurada. Defina OPENAI_API_KEY no arquivo .env ou no ambiente.",
                    "provider": "openai"
                }
            model_name = os.getenv("MODEL", "gpt-4o-mini")
            result = await analyze_with_openai(timeline, openai_key, model_name)
            result["provider_used"] = f"OpenAI ({model_name})"
            return result

        else:
            return {"error": f"Provedor desconhecido: '{selected_provider}'. Use 'gemini' ou 'openai'."}

    except Exception as e:
        return {
            "error": f"Erro durante a análise com {selected_provider}: {str(e)}",
            "provider": selected_provider
        }
