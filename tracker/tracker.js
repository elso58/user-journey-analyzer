(function () {
  const currentScript = document.currentScript || (function() {
    const scripts = document.getElementsByTagName("script");
    return scripts[scripts.length - 1];
  })();

  let defaultEndpoint = "/api/events";
  if (currentScript && currentScript.src) {
    try {
      const scriptUrl = new URL(currentScript.src);
      defaultEndpoint = scriptUrl.origin + "/api/events";
    } catch(e) {}
  }

  const ENDPOINT = window.__JOURNEY_ENDPOINT__ || defaultEndpoint;
  const siteId = window.__JOURNEY_SITE_ID__ || (currentScript && currentScript.getAttribute("data-site")) || "default";

  const BATCH_SIZE = 15;
  const FLUSH_MS = 4000;
  let queue = [];
  let sessionId = sessionStorage.getItem("jid") || (crypto.randomUUID && crypto.randomUUID()) || (Date.now() + "-" + Math.random());
  sessionStorage.setItem("jid", sessionId);

  function send(events) {
    if (!events.length) return;
    const body = JSON.stringify({ siteId, sessionId, url: location.href, events });
    
    if (navigator.sendBeacon) {
      navigator.sendBeacon(ENDPOINT, new Blob([body], { type: "text/plain" }));
    } else {
      fetch(ENDPOINT, { method: "POST", headers: { "Content-Type": "text/plain" }, body, keepalive: true }).catch(function() {});
    }
  }

  function enqueue(e) {
    queue.push(Object.assign({}, e, { ts: Date.now(), url: location.href }));
    if (queue.length >= BATCH_SIZE) flush();
  }

  function flush() { 
    if (queue.length > 0) {
      const b = queue.splice(0); 
      send(b); 
    }
  }

  enqueue({ type: "pageview", title: document.title });

  document.addEventListener("click", function (ev) {
    const t = ev.target;
    enqueue({ type: "click", tag: t.tagName, id: t.id || null, class: t.className || null, text: (t.innerText || "").slice(0, 80) });
  }, true);

  let maxScroll = 0;
  window.addEventListener("scroll", function () {
    const h = document.documentElement;
    const pct = Math.round((window.scrollY + window.innerHeight) / h.scrollHeight * 100);
    if (pct > maxScroll) { maxScroll = pct; enqueue({ type: "scroll", depth: pct }); }
  }, { passive: true });

  document.addEventListener("focusin", function (ev) {
    if (ev.target.matches && ev.target.matches("input,textarea,select")) {
      enqueue({ type: "form_focus", field: ev.target.name || ev.target.id || ev.target.type });
    }
  }, true);

  document.addEventListener("submit", function (ev) {
    enqueue({ type: "form_submit", form: ev.target.id || ev.target.name || "form" });
  }, true);

  window.addEventListener("pagehide", function () {
    enqueue({ type: "page_leave", maxScroll: maxScroll });
    flush();
  });

  window.addEventListener("error", function (e) {
    enqueue({ type: "js_error", message: (e.message || "").slice(0, 200), source: e.filename });
  });

  setInterval(flush, FLUSH_MS);
})();
