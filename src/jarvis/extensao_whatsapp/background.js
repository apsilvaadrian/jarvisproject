// Ponte entre o JARVIS (servidor local em 127.0.0.1) e a aba do WhatsApp Web deste navegador.
// O JARVIS manda "buscar" / "enviar_para"; quem mexe na página é o content.js, dentro da aba.
const PORTAS = [8000, 8001, 8002, 8003, 8004, 8005];
let ws = null, tentativa = 0;

function conectar() {
  if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) return;
  const porta = PORTAS[tentativa++ % PORTAS.length];
  try { ws = new WebSocket(`ws://127.0.0.1:${porta}/ws/whatsapp`); } catch (e) { ws = null; return; }
  ws.onopen = () => {
    tentativa = 0;
    ws.send(JSON.stringify({ type: "ola", versao: chrome.runtime.getManifest().version, navegador: navigator.userAgent }));
  };
  ws.onmessage = async (ev) => {
    let m;
    try { m = JSON.parse(ev.data); } catch (e) { return; }
    if (m.type === "ping") { ws.send(JSON.stringify({ type: "pong" })); return; }
    const r = await naAba(m);
    try { ws.send(JSON.stringify({ ...r, type: "resultado", id: m.id })); } catch (e) { /* JARVIS fechou */ }
  };
  ws.onclose = () => { ws = null; setTimeout(conectar, 4000); };
  ws.onerror = () => { /* o onclose religa */ };
}

async function naAba(m) {
  const abas = await chrome.tabs.query({ url: "https://web.whatsapp.com/*" });
  if (!abas.length) return { ok: false, motivo: "sem_aba" };
  try {
    return await chrome.tabs.sendMessage(abas[0].id, m);
  } catch (e) {
    return { ok: false, motivo: "sem_script", erro: String(e) };     // aba aberta antes de instalar: recarregar
  }
}

// O service worker "dorme" sem atividade; estes eventos o acordam e religam a conexão
chrome.runtime.onStartup.addListener(conectar);
chrome.runtime.onInstalled.addListener(conectar);
chrome.runtime.onMessage.addListener((m) => { if (m && m.type === "acordar") conectar(); });
chrome.alarms.create("jarvis-vivo", { periodInMinutes: 0.5 });
chrome.alarms.onAlarm.addListener(conectar);
conectar();
