// Roda dentro da aba do WhatsApp Web. Só age quando o JARVIS pede (e o JARVIS só pede depois do seu "sim").
(() => {
  const espera = (ms) => new Promise((r) => setTimeout(r, ms));
  const norm = (t) => (t || "").toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "").replace(/\s+/g, " ").trim();

  function estado() {
    // A lista de conversas na tela = logado (o texto da tela inicial pode lembrar o da tela de login)
    if (document.querySelector("#pane-side, #side")) return "pronto";
    const txt = document.body ? document.body.innerText : "";
    if (document.querySelector('canvas[aria-label*="QR"], div[data-ref] canvas') ||
        /Escaneie o QR|Scan the QR|Use o WhatsApp no seu computador|Use WhatsApp on your computer/i.test(txt)) return "login";
    return "carregando";
  }

  // Sem a formatação do WhatsApp (*negrito*, _itálico_): é assim que o texto aparece na conversa
  const semFormato = (t) => norm(String(t || "").replace(/[*_~`]/g, ""));
  const textoDe = (el) => (el.tagName === "INPUT" ? el.value : el.innerText) || "";

  // O editor do WhatsApp (Lexical) atualiza a caixa com um pequeno atraso: espera o texto aparecer
  async function esperarTexto(el, texto, ms = 1500) {
    for (let t = 0; t < ms; t += 100) {
      if (semFormato(textoDe(el)) === semFormato(texto)) return true;
      await espera(100);
    }
    return semFormato(textoDe(el)) === semFormato(texto);
  }

  async function apagar(el) {
    el.focus();
    document.execCommand("selectAll", false, null);
    document.execCommand("delete", false, null);
    await esperarTexto(el, "", 800);
  }

  // Escreve num campo do WhatsApp (editor Lexical ou <input>). Usa UM jeito por vez — antes, "digitar" e
  // depois "colar" de reserva duplicava o texto ("Oi" virava "OiOi") porque a caixa demora a atualizar.
  async function escrever(el, texto) {
    el.focus();
    if (el.tagName === "INPUT") {
      const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set;
      setter.call(el, texto);
      el.dispatchEvent(new Event("input", { bubbles: true }));
      return true;
    }
    await apagar(el);
    if (!texto) return true;
    const varias = texto.includes("\n");                    // relatório: colar mantém as linhas
    if (!varias) {
      document.execCommand("insertText", false, texto);
      if (await esperarTexto(el, texto)) return true;
      await apagar(el);                                     // não entrou direito: limpa antes da reserva
    }
    const dt = new DataTransfer();
    dt.setData("text/plain", texto);
    el.dispatchEvent(new ClipboardEvent("paste", { clipboardData: dt, bubbles: true, cancelable: true }));
    if (await esperarTexto(el, texto, 2500)) return true;
    if (varias && !semFormato(textoDe(el))) {                // a colagem não pegou: digita (com as quebras)
      document.execCommand("insertText", false, texto);
      return esperarTexto(el, texto);
    }
    return false;
  }

  // O WhatsApp escuta o clique num elemento INTERNO da linha (o evento sobe, nunca desce): então clicamos no
  // elemento que está de fato no centro da área, com coordenadas, como um clique de mouse de verdade.
  function clicar(el) {
    el.scrollIntoView({ block: "center" });
    const r = el.getBoundingClientRect();
    const x = r.left + Math.min(r.width / 2, 120), y = r.top + r.height / 2;
    const alvo = document.elementFromPoint(x, y) || el;
    const base = { bubbles: true, cancelable: true, composed: true, view: window, clientX: x, clientY: y, button: 0 };
    const ponteiro = { ...base, pointerId: 1, pointerType: "mouse", isPrimary: true };
    alvo.dispatchEvent(new PointerEvent("pointerover", ponteiro));
    alvo.dispatchEvent(new PointerEvent("pointerdown", { ...ponteiro, buttons: 1 }));
    alvo.dispatchEvent(new MouseEvent("mousedown", { ...base, buttons: 1, detail: 1 }));
    alvo.dispatchEvent(new PointerEvent("pointerup", ponteiro));
    alvo.dispatchEvent(new MouseEvent("mouseup", { ...base, detail: 1 }));
    alvo.dispatchEvent(new MouseEvent("click", { ...base, detail: 1 }));
  }

  async function limparBusca() {
    const busca = caixaBusca();
    if (busca && (busca.value || busca.innerText || "").trim()) await escrever(busca, "");
  }

  const caixaBusca = () => document.querySelector('#side [contenteditable="true"]') || document.querySelector("#side input[type=text]") ||
    document.querySelector('[aria-label*="esquis"][contenteditable="true"], input[aria-label*="esquis"]');
  const linhas = () => [...document.querySelectorAll('#pane-side [role="listitem"], #pane-side [role="row"]')];
  const tituloDe = (row) => { const s = row.querySelector("span[title]"); return s ? s.getAttribute("title") : (row.innerText || "").split("\n")[0]; };
  const cabecalho = () => { const h = document.querySelector("#main header"); if (!h) return ""; const s = h.querySelector('span[title], span[dir="auto"]');
    return (s ? (s.getAttribute("title") || s.innerText) : h.innerText.split("\n")[0]).trim(); };
  const compositor = () => document.querySelector('#main footer [contenteditable="true"]');

  async function buscar(nome) {
    const est = estado();
    if (est !== "pronto") return { ok: false, motivo: est };
    const busca = caixaBusca();
    if (!busca) return { ok: false, motivo: "sem_busca" };
    await escrever(busca, nome);
    let anteriores = [];
    for (let i = 0; i < 10; i++) {                          // espera os resultados pararem de mudar
      await espera(600);
      const agora = linhas().map(tituloDe).filter(Boolean);
      if (agora.length && agora.join("|") === anteriores.join("|")) break;
      anteriores = agora;
    }
    return { ok: true, titulos: [...new Set(anteriores)] };
  }

  // Abre a conversa da busca e confere o cabeçalho (usado para enviar e para ler)
  async function abrirConversa(titulo) {
    const row = linhas().find((r) => tituloDe(r) === titulo);
    if (!row) { await limparBusca(); return { ok: false, motivo: "sumiu_da_busca" }; }
    let cab = "";
    // Tenta no nome da conversa e, se não abrir, na própria linha
    for (const alvo of [row.querySelector("span[title]"), row.querySelector('[role="gridcell"], [role="button"]'), row]) {
      if (!alvo) continue;
      clicar(alvo);
      for (let i = 0; i < 10 && norm(cab) !== norm(titulo); i++) { await espera(300); cab = cabecalho(); }
      if (norm(cab) === norm(titulo)) break;
    }
    if (norm(cab) !== norm(titulo)) { await limparBusca(); return { ok: false, motivo: "conversa_errada", cabecalho: cab }; }
    return { ok: true };
  }

  // --------------------------------------------------------------- leitura (não envia nada)
  const contagemNaoLidas = (row) => {
    const el = row.querySelector('[aria-label*="não lida"], [aria-label*="nao lida"], [aria-label*="unread"]');
    if (!el) return 0;
    const n = parseInt((el.getAttribute("aria-label") || el.innerText || "").replace(/\D+/g, " ").trim().split(" ")[0], 10);
    return Number.isFinite(n) && n > 0 ? n : 1;
  };
  function resumoDaLinha(row) {
    const titulos = [...row.querySelectorAll("span[title]")].map((s) => s.getAttribute("title")).filter(Boolean);
    const partes = (row.innerText || "").split("\n").map((t) => t.trim()).filter(Boolean);
    const quando = partes.find((t) => /^(\d{1,2}:\d{2}|ontem|hoje|yesterday|\d{1,2}\/\d{1,2}(\/\d{2,4})?|segunda|terça|quarta|quinta|sexta|sábado|domingo)/i.test(t)) || "";
    const titulo = titulos[0] || partes[0] || "";
    const previa = titulos[1] || partes.find((t) => t !== titulo && t !== quando && !/^\d+$/.test(t)) || "";
    const silenciada = !!row.querySelector('[aria-label*="silenciad" i], [aria-label*="muted" i], [data-icon*="mute"]');
    return { conversa: titulo, previa, quando, nao_lidas: contagemNaoLidas(row), silenciada };
  }
  // Chip de filtro da lista ("Tudo", "Não lidas"...)
  const chip = (re) => [...document.querySelectorAll('#side button, #side [role="tab"], #side [role="button"]')]
    .find((b) => re.test((b.innerText || b.getAttribute("aria-label") || "").trim()));

  async function naoLidas(limite) {
    const est = estado();
    if (est !== "pronto") return { ok: false, motivo: est };
    await limparBusca();
    const filtro = chip(/^(não lidas|nao lidas|unread)/i);
    if (filtro) { clicar(filtro); await espera(900); }     // mostra só as não lidas (volta para "Tudo" no fim)
    const conversas = linhas().map(resumoDaLinha).filter((c) => c.conversa && c.nao_lidas > 0).slice(0, limite || 15);
    if (filtro) { const tudo = chip(/^(tudo|all)$/i); if (tudo) clicar(tudo); }
    return { ok: true, conversas, total: conversas.reduce((s, c) => s + c.nao_lidas, 0) };
  }

  // Para os avisos: as conversas com não lidas que estão na lista agora, sem mexer em filtro nem abrir nada
  function espiar(limite) {
    const est = estado();
    if (est !== "pronto") return { ok: false, motivo: est };
    const conversas = linhas().map(resumoDaLinha).filter((c) => c.conversa && c.nao_lidas > 0).slice(0, limite || 5);
    return { ok: true, conversas };
  }

  async function lerConversa(titulo, n) {
    const aberta = await abrirConversa(titulo);
    if (!aberta.ok) return aberta;
    await espera(700);
    const msgs = [];
    for (const row of [...document.querySelectorAll('#main [role="row"]')]) {
      const meta = row.querySelector("[data-pre-plain-text]");
      const info = meta ? meta.getAttribute("data-pre-plain-text") : "";   // "[10:30, 27/10/2026] Maria: "
      const m = /^\[([^\]]+)\]\s*(.*?):\s*$/.exec(info || "");
      const corpo = row.querySelector(".selectable-text, [data-testid='msg-text']") || meta || row;
      let texto = (corpo.innerText || "").trim();
      if (!info && !texto) continue;
      if (!texto) texto = "[mídia]";
      const id = (row.querySelector("[data-id]") || row).getAttribute("data-id") || "";
      msgs.push({ autor: m ? m[2] : "", quando: m ? m[1] : "", texto: texto.slice(0, 600), minha: id.startsWith("true_") || row.classList.contains("eu") });
    }
    await limparBusca();
    return { ok: true, conversa: titulo, mensagens: msgs.slice(-(n || 10)) };
  }

  async function enviarPara(titulo, mensagem) {
    const aberta = await abrirConversa(titulo);
    if (!aberta.ok) return aberta;
    const box = compositor();
    if (!box) return { ok: false, motivo: "sem_permissao" };          // ex.: grupo em que só admins escrevem
    if (!(await escrever(box, mensagem))) { await apagar(box); return { ok: false, motivo: "nao_digitou" }; }
    // Para conferir depois: quantas mensagens da conversa já têm esse começo de texto
    const inicio = semFormato(mensagem).slice(0, 25);
    const contar = () => [...document.querySelectorAll('#main [role="row"]')].filter((r) => semFormato(r.innerText).includes(inicio)).length;
    const haLista = document.querySelectorAll('#main [role="row"]').length > 0;
    const antes = contar();
    const botao = document.querySelector('#main footer button[aria-label="Enviar"], #main footer button[aria-label="Send"]') ||
      (document.querySelector('#main footer span[data-icon="send"], #main footer span[data-icon="wds-ic-send-filled"]') || {}).closest?.("button");
    if (botao) clicar(botao);
    else box.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", code: "Enter", keyCode: 13, which: 13, bubbles: true }));
    for (let i = 0; i < 30; i++) {                           // confere: caixa vazia e uma mensagem nova com o texto
      await espera(400);
      const vazia = !semFormato(textoDe(compositor() || box));
      if (vazia && contar() > antes) { await limparBusca(); return { ok: true, conferido: true, conversa: titulo }; }
      if (vazia && !haLista && i >= 5) {                     // layout sem lista legível: a caixa esvaziou = enviou
        await limparBusca();
        return { ok: true, conferido: false, conversa: titulo };
      }
    }
    return { ok: false, motivo: "nao_confirmou", conversa: titulo };
  }

  chrome.runtime.onMessage.addListener((m, _remetente, responder) => {
    const tarefa = m.type === "buscar" ? buscar(m.nome)
      : m.type === "enviar_para" ? enviarPara(m.titulo, m.mensagem)
      : m.type === "estado" ? Promise.resolve({ ok: true, estado: estado() })
      : m.type === "limpar" ? limparBusca().then(() => ({ ok: true }))
      : m.type === "nao_lidas" ? naoLidas(m.limite)
      : m.type === "espiar" ? Promise.resolve(espiar(m.limite))
      : m.type === "abrir_conversa" ? buscar(m.titulo).then((b) => (b.ok ? abrirConversa(m.titulo) : b)).then(async (r) => { await limparBusca(); return r; })
      : m.type === "ler_conversa" ? lerConversa(m.titulo, m.n)
      : Promise.resolve({ ok: false, motivo: "desconhecido" });
    tarefa.then(responder, (e) => responder({ ok: false, motivo: "erro", erro: String(e) }));
    return true;                                             // resposta assíncrona
  });
  chrome.runtime.sendMessage({ type: "acordar" }).catch(() => {});
})();
