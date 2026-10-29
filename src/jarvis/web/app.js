/* Interface do JARVIS: WebSocket, painéis de hardware, voz (reconhecimento + síntese) e chat. */
(function () {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const core = new window.JarvisCore($("core"));

  const STATE_LABEL = { idle: "EM ESPERA", listening: "OUVINDO", thinking: "PROCESSANDO", speaking: "FALANDO", error: "ERRO" };
  let ws = null, connected = false, pendingConfirmId = null, booted = false;
  let tratamento = "senhor";

  // ------------------------------------------------------------------ utilidades
  const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const fmtRate = (kbps) => kbps > 1024 ? `${(kbps / 1024).toFixed(1)} MB/s` : `${kbps.toFixed(0)} KB/s`;
  const level = (p, warn = 75, crit = 90) => (p >= crit ? "crit" : p >= warn ? "warn" : "");

  function setCoreState(s) {
    core.setState(s);
    $("coreState").textContent = STATE_LABEL[s] || s.toUpperCase();
    $("coreState").parentElement.className = "core-label " + s;
    avisarFlutuante();
  }

  // Janelinha flutuante (com esta janela minimizada/na bandeja): ela mostra o estado e manda os pedidos para cá
  // (pelo servidor); quem ouve, fala e conversa com o cérebro continua sendo esta janela.
  function avisarFlutuante(extra = {}) {
    if (!connected) return;
    // Confirmação pendente (SIM/NÃO na janelinha) e a letra aberta (abre lá também)
    let confirmar = "", letra = false;
    try { confirmar = pendingConfirmId ? $("confirmText").textContent : ""; letra = lyricsOpen; } catch (e) { /* iniciando */ }
    let cartoesApps = [];
    try {
      cartoesApps = appCards.filter((c) => layout && layout.fixados.includes(c.id))
        .map((c) => ({ id: c.id, tipo: c.tipo, app: c.app, url: c.url, altura: c.altura }));
    } catch (e) { /* iniciando */ }
    try { ws.send(JSON.stringify({ type: "flutuante", tipo: "estado", estado: core.state, voz: $("voiceToggle").checked, confirmar, letra, cartoesApps, ...extra })); }
    catch (e) { /* reconectando */ }
  }
  function doFlutuante(m) {
    if (m.tipo === "ola") avisarFlutuante();
    else if (m.tipo === "enviar") send(m.texto);
    else if (m.tipo === "mic") $("micBtn").click();
    else if (m.tipo === "voz") {
      $("voiceToggle").checked = !!m.ligado;
      if (!m.ligado) stopSpeaking();
      avisarFlutuante();
    } else if (m.tipo === "confirmar" && pendingConfirmId) answer(!!m.aprovar);
    else if (m.tipo === "fechar_card_app") fecharAppCard(m.id);
    else if (m.tipo === "abrir_web" && m.url) {                 // link clicado num card da janelinha
      abrirWeb(m.url);
      fetch("/api/mostrar", { method: "POST" }).catch(() => {});
    }
  }

  function toast(text, kind = "") {
    const el = document.createElement("div");
    el.className = "toast " + kind;
    el.textContent = text;
    $("toasts").appendChild(el);
    setTimeout(() => el.remove(), 5000);
  }

  // Texto com blocos de código (```linguagem ... ```) e `código` em linha: o código aparece formatado,
  // com botão de copiar; o resto continua como texto simples (e escapado).
  function formatarTexto(text) {
    const partes = String(text ?? "").split(/```/);
    return partes.map((p, i) => {
      if (i % 2 === 1) {
        const nl = p.indexOf("\n");
        const lang = nl > 0 && /^[\w#+.-]{1,20}$/.test(p.slice(0, nl).trim()) ? p.slice(0, nl).trim() : "";
        const codigo = lang ? p.slice(nl + 1) : p.replace(/^\n/, "");
        return `<div class="code-box"><div class="code-head"><span>${esc(lang || "código")}</span>`
          + `<button type="button" class="ct code-copy" title="Copiar">⧉</button></div><pre><code>${esc(codigo.replace(/\n$/, ""))}</code></pre></div>`;
      }
      return esc(p).replace(/`([^`\n]+)`/g, "<code class=\"inline\">$1</code>").replace(/\n/g, "<br>");
    }).join("");
  }
  // A voz não lê código: troca os blocos por um aviso curto
  const paraFala = (text) => String(text || "").replace(/```[\s\S]*?```/g, " (o código está na tela) ").replace(/`([^`]+)`/g, "$1");

  function addLog(who, text, kind) {
    const row = document.createElement("div");
    row.className = "row " + kind;
    row.innerHTML = `<span class="who">${esc(who)}</span><span class="msg">${formatarTexto(text)}</span>`;
    $("log").appendChild(row);
    while ($("log").children.length > 80) $("log").firstChild.remove();
    $("log").scrollTop = $("log").scrollHeight;
  }

  // ------------------------------------------------------------------ relógio
  function tick() {
    const d = new Date();
    $("clockTime").textContent = d.toLocaleTimeString("pt-BR");
    $("clockDate").textContent = d.toLocaleDateString("pt-BR", { weekday: "long", day: "2-digit", month: "long", year: "numeric" });
  }
  setInterval(tick, 1000); tick();

  // ------------------------------------------------------------------ gráficos
  function setGauge(id, value, text, warn, crit) {
    const el = $(id);
    const v = Math.max(0, Math.min(100, value || 0));
    el.querySelector(".g-fg").style.strokeDashoffset = String(314.16 * (1 - v / 100));
    el.querySelector("b").textContent = text;
    el.className = "gauge " + level(v, warn, crit);
  }

  class Spark {
    constructor(canvas, max = 100, series = 1) {
      this.c = canvas; this.max = max; this.data = Array.from({ length: series }, () => []);
    }
    push(...vals) {
      vals.forEach((v, i) => { this.data[i].push(v); if (this.data[i].length > 60) this.data[i].shift(); });
      this.draw();
    }
    draw() {
      const c = this.c, dpr = window.devicePixelRatio || 1;
      const w = c.clientWidth * dpr, h = c.clientHeight * dpr;
      if (c.width !== w) { c.width = w; c.height = h; }
      const ctx = c.getContext("2d");
      ctx.clearRect(0, 0, w, h);
      ctx.strokeStyle = "rgba(46,230,255,0.08)";
      for (let y = 0; y <= 4; y++) { ctx.beginPath(); ctx.moveTo(0, (h / 4) * y); ctx.lineTo(w, (h / 4) * y); ctx.stroke(); }
      const max = this.max || Math.max(1, ...this.data.flat()) * 1.2;
      const colors = [[46, 230, 255], [77, 255, 184]];
      this.data.forEach((d, si) => {
        if (d.length < 2) return;
        const [r, g, b] = colors[si % 2];
        ctx.beginPath();
        // Alinhado à direita: o ponto mais recente fica sempre na borda direita
        const xOf = (i) => w - ((d.length - 1 - i) / 59) * w;
        d.forEach((v, i) => {
          const y = h - (Math.min(v, max) / max) * (h - 2) - 1;
          i ? ctx.lineTo(xOf(i), y) : ctx.moveTo(xOf(i), y);
        });
        ctx.strokeStyle = `rgb(${r},${g},${b})`; ctx.lineWidth = 1.5 * dpr; ctx.stroke();
        ctx.lineTo(w, h); ctx.lineTo(xOf(0), h); ctx.closePath();
        const grad = ctx.createLinearGradient(0, 0, 0, h);
        grad.addColorStop(0, `rgba(${r},${g},${b},0.25)`); grad.addColorStop(1, `rgba(${r},${g},${b},0)`);
        ctx.fillStyle = grad; ctx.fill();
      });
    }
  }
  const sparkCpu = new Spark($("sparkCpu")), sparkRam = new Spark($("sparkRam")), sparkNet = new Spark($("sparkNet"), 0, 2);

  const kv = (pairs) => pairs.filter(([, v]) => v !== null && v !== undefined && v !== "")
    .map(([k, v]) => `<dt>${esc(k)}</dt><dd title="${esc(v)}">${esc(v)}</dd>`).join("");

  function renderStats(s) {
    const cpu = s.cpu, mem = s.memoria, temp = s.temperatura;
    setGauge("gCpu", cpu.percent, cpu.percent.toFixed(0));
    const tc = temp.cpu_c ?? (s.gpu[0] && s.gpu[0].temp_c);
    setGauge("gTemp", tc ?? 0, tc != null ? tc.toFixed(0) : "--", 70, 85);
    $("gTemp").querySelector(".g-lbl").textContent = temp.cpu_c != null ? "CPU °C" : s.gpu[0] ? "GPU °C" : "TEMP";
    sparkCpu.push(cpu.percent);

    $("cores").innerHTML = cpu.por_nucleo.map((p) => `<span style="height:${Math.max(4, p)}%" title="${p}%"></span>`).join("");
    $("cpuKv").innerHTML = kv([
      ["Núcleos", `${cpu.nucleos_fisicos} / ${cpu.nucleos_logicos}`],
      ["Frequência", cpu.frequencia_mhz ? `${cpu.frequencia_mhz} MHz` : null],
      ["Load avg", cpu.load_average ? cpu.load_average.join(" · ") : null],
      ["Sensor", temp.fonte || (s.gpu.length ? "nvidia-smi" : "indisponível")],
    ]);

    setGauge("gRam", mem.percent, mem.percent.toFixed(0), 80, 92);
    setGauge("gSwap", mem.swap_percent, mem.swap_percent.toFixed(0), 60, 85);
    sparkRam.push(mem.percent);
    $("ramKv").innerHTML = kv([["Em uso", `${mem.usado_gb} GB`], ["Disponível", `${mem.disponivel_gb} GB`], ["Total", `${mem.total_gb} GB`]]);

    $("gpuCard").hidden = !s.gpu.length;
    $("gpus").innerHTML = s.gpu.map((g) => `
      <div class="gpu"><div class="disk-head"><span>${esc(g.nome)}</span><b>${g.percent.toFixed(0)}% · ${g.temp_c.toFixed(0)}°C</b></div>
      <div class="bar ${level(g.percent)}"><i style="width:${g.percent}%"></i></div>
      <small class="muted">VRAM ${(g.mem_usada_mb / 1024).toFixed(1)} / ${(g.mem_total_mb / 1024).toFixed(1)} GB</small></div>`).join("");

    $("disks").innerHTML = s.discos.map((d) => `
      <div class="disk"><div class="disk-head"><span>${esc(d.ponto_montagem)} <small class="muted">${esc(d.sistema_arquivos)}</small></span>
      <b>${d.livre_gb.toFixed(0)} GB livres</b></div>
      <div class="bar ${level(d.percent, 80, 92)}"><i style="width:${d.percent}%"></i></div></div>`).join("");

    const discoMax = Math.max(0, ...s.discos.map((d) => d.percent));
    $("ovCpu").textContent = `${cpu.percent.toFixed(0)}%`;
    $("ovMem").textContent = `${mem.percent.toFixed(0)}%`;
    $("ovDisk").textContent = `${discoMax.toFixed(0)}%`;
    $("ovNet").textContent = fmtRate(s.rede.download_kbps);
    $("ovTemp").textContent = tc != null ? `${tc.toFixed(0)} °C` : "—";
    $("ovUp").textContent = s.sistema.uptime || "—";
    // Saúde: 100 menos penalidades por CPU, memória, disco e temperatura altos
    const saude = Math.round(Math.max(0, Math.min(100, 100 - Math.max(0, cpu.percent - 60) * 0.5
      - Math.max(0, mem.percent - 70) * 1.2 - Math.max(0, discoMax - 85) * 2 - Math.max(0, (tc || 0) - 80) * 2)));
    const hl = saude < 50 ? "crit" : saude < 75 ? "warn" : "";
    $("ovHealth").className = "health " + hl;
    $("ovHealth").querySelector(".g-fg").style.strokeDashoffset = String(314.16 * (1 - saude / 100));
    $("ovHealth").querySelector("b").textContent = saude;
    const problemas = [cpu.percent >= 95 && "CPU NO LIMITE", mem.percent >= 92 && "MEMÓRIA CHEIA",
      discoMax >= 95 && "DISCO CHEIO", tc >= 90 && "CPU QUENTE"].filter(Boolean);
    $("sysStatus").className = "sys-status " + (problemas.length ? "crit" : hl);
    $("sysStatusText").textContent = problemas.length ? problemas.join(" · ") : hl ? "SISTEMA SOB CARGA" : "SISTEMAS OPERACIONAIS";
    $("sysStatus").title = `Estado geral do PC: ${$("sysStatusText").textContent}`;   // o texto inteiro, se não couber
    $("dgMem").textContent = `${mem.percent.toFixed(0)}%`;
    $("dgUp").textContent = s.sistema.uptime || "—";
    $("bbNet").textContent = `▼ ${fmtRate(s.rede.download_kbps)}`;

    $("netUp").textContent = fmtRate(s.rede.upload_kbps);
    $("netDown").textContent = fmtRate(s.rede.download_kbps);
    sparkNet.push(s.rede.download_kbps, s.rede.upload_kbps);

    $("procCount").textContent = `· ${s.sistema.total_processos}`;
    $("procs").innerHTML = (s.processos || []).map((p) => `
      <tr><td>${p.pid}</td><td title="${esc(p.nome)}">${esc(p.nome)}</td><td>${p.cpu_percent.toFixed(1)}%</td><td>${p.memoria_mb.toFixed(0)}</td></tr>`).join("");

    const sys = s.sistema;
    $("sysKv").innerHTML = kv([
      ["Host", sys.hostname], ["SO", sys.sistema], ["Usuário", sys.usuario], ["Uptime", sys.uptime],
      ["Bateria", s.bateria ? `${s.bateria.percent}% ${s.bateria.carregando ? "⚡" : ""}` : null],
    ]);
  }

  // ------------------------------------------------------------------ síntese de voz
  let voice = null;
  function pickVoice() {
    const voices = speechSynthesis.getVoices();
    const score = (v) => {
      let s = 0;
      if (/^pt[-_]BR/i.test(v.lang)) s += 10; else if (/^pt/i.test(v.lang)) s += 5; else return -1;
      if (/natural|online|neural/i.test(v.name)) s += 4;
      if (/antonio|daniel|ricardo|fabio|humberto|donato|julio|nicolau|valerio/i.test(v.name)) s += 3;
      if (/google/i.test(v.name)) s += 1;
      return s;
    };
    voice = voices.filter((v) => score(v) >= 0).sort((a, b) => score(b) - score(a))[0] || null;
  }
  if ("speechSynthesis" in window) { pickVoice(); speechSynthesis.onvoiceschanged = pickVoice; }

  // Três vozes: ElevenLabs (nuvem), voz clonada local (servidor XTTS) e a do navegador (Web Speech API).
  // Se a escolhida falhar, cai para a seguinte da lista.
  let storedMode = null, cloneAvailable = false, elevenReady = false, warnedFallback = false, vozCarregada = false;
  let speakTimer = null, speakId = 0, currentSource = null;
  try { storedMode = localStorage.getItem("jarvis.voz"); } catch (e) { /* sem storage */ }
  let voiceMode = storedMode || "clonada";

  let isSpeaking = false;   // vale para as três vozes (navegador, local e ElevenLabs)
  function onSpeechStart() {
    isSpeaking = true;
    if (podeInterromper()) { if (!recActive) startRecognition(); }   // ouvindo para você poder interromper
    else stopRecognitionForSpeech();
    setCoreState("speaking");
  }
  function onSpeechEnd() {
    isSpeaking = false;
    if (focoAuto && focoFimDaFala) setTimeout(() => { if (focoAuto && focoFimDaFala && !isSpeaking) desfocarCard(); }, 1200);
    clearInterval(speakTimer); core.setLevel(0); setCoreState("idle"); resumeRecognitionAfterSpeech();
    abrirConversa();
  }

  // Depois da fala: pergunta -> 10 s para responder; resposta comum -> "conversa contínua" (padrão 8 s)
  function abrirConversa() {
    const pergunta = expectingAnswer;
    expectingAnswer = false;
    const ms = pergunta ? FOLLOWUP_MS : conversaSeg() * 1000;
    if (ms > 0) startFollowUp(ms, pergunta ? "AGUARDANDO SUA RESPOSTA" : "PODE FALAR");
  }

  // ------------------------------------------------------------------ conversa contínua
  // Quando o JARVIS faz uma pergunta ou pede autorização, o microfone abre sozinho para a resposta
  // (sem precisar dizer "Jarvis" nem clicar no microfone). Se você não falar nada, fecha em ~10 s.
  const FOLLOWUP_MS = 10000;
  let expectingAnswer = false, followUpTimer = null, followUpMs = FOLLOWUP_MS;

  // Preferências da conversa (aba AI Voice): segundos ouvindo depois de cada resposta e "posso interromper"
  const pref = (k, padrao) => { try { const v = localStorage.getItem(k); return v === null ? padrao : v; } catch (e) { return padrao; } };
  const conversaSeg = () => Number(pref("jarvis.conversa", "8")) || 0;
  const podeInterromper = () => !!SR && pref("jarvis.interromper", "1") === "1" && $("voiceToggle").checked;

  // O que o JARVIS está falando agora: o microfone também ouve as caixas de som, então o que for parecido
  // com a própria fala é eco, não você.
  let falaAtual = "";
  const palavrasDe = (t) => (t || "").toLowerCase().normalize("NFD").replace(/[\u0300-\u036f]/g, "").match(/[a-z0-9]+/g) || [];
  function ehEco(texto) {
    const fala = new Set(palavrasDe(falaAtual));
    const ouvidas = palavrasDe(texto).filter((p) => p !== "jarvis");
    if (!ouvidas.length) return true;
    const iguais = ouvidas.filter((p) => fala.has(p)).length;
    return iguais / ouvidas.length >= 0.6;
  }

  function expectsAnswer(text) {
    const t = (text || "").trim();
    return !!pendingConfirmId || /\?\s*["”')]*\s*$/.test(t)
      || /(aguardo sua (confirma|resposta|autoriza)|deseja que eu|quer que eu|devo prosseguir|posso (prosseguir|continuar))/i.test(t);
  }

  function startFollowUp(ms = FOLLOWUP_MS, rotulo = "AGUARDANDO SUA RESPOSTA") {
    if (!SR || !booted) return;
    clearTimeout(followUpTimer);
    followUpMs = ms;
    awaitingCommand = true;
    setCoreState("listening");
    $("coreState").textContent = rotulo;
    if (!recActive) startRecognition();
    $("micBtn").classList.add("on");
    followUpTimer = setTimeout(endFollowUp, ms);
  }

  function endFollowUp() {
    clearTimeout(followUpTimer);
    if (!awaitingCommand) return;
    awaitingCommand = false;
    $("micBtn").classList.remove("on");
    if (!wantListening) stopRecognition();         // no modo "Jarvis" continua ouvindo a palavra de ativação
    if (core.state === "listening") setCoreState("idle");
  }

  function stopSpeaking() {
    speakId++;
    isSpeaking = false;
    if ("speechSynthesis" in window) speechSynthesis.cancel();
    if (currentSource) { try { currentSource.onended = null; currentSource.stop(); } catch (e) { /* já parou */ } currentSource = null; }
    clearInterval(speakTimer);
  }

  function speak(text) {
    if (!$("voiceToggle").checked || !text) return;
    falaAtual = text;
    stopSpeaking();
    const id = speakId;
    // Logo ao abrir, a saudação chega antes de sabermos se o ElevenLabs está configurado: espera essa
    // resposta em vez de cair na voz do navegador só na primeira fala.
    if (!vozCarregada) { esperarVoz().then(() => { if (id === speakId) falar(text, id); }); return; }
    falar(text, id);
  }

  function falar(text, id) {
    if (voiceMode === "elevenlabs" && (elevenReady || cloneAvailable)) speakRemote(text, id, "elevenlabs");
    else if (voiceMode === "clonada" && cloneAvailable) speakRemote(text, id, "local");
    else speakBrowser(text);
  }

  let esperandoVoz = null;
  function esperarVoz() {
    esperandoVoz = esperandoVoz || (async () => {
      for (let i = 0; i < 10 && !vozCarregada; i++) {
        await refreshVoiceStatus();
        if (!vozCarregada) await new Promise((r) => setTimeout(r, 500));
      }
    })().finally(() => { esperandoVoz = null; });
    return esperandoVoz;
  }

  function speakBrowser(text) {
    if (!("speechSynthesis" in window)) return;
    const u = new SpeechSynthesisUtterance(text);
    u.lang = "pt-BR"; u.rate = 1.05; u.pitch = 0.9;
    if (voice) u.voice = voice;
    u.onstart = () => {
      onSpeechStart();
      // A Web Speech API não expõe o volume da fala; simulamos o envelope
      speakTimer = setInterval(() => core.setLevel(0.35 + Math.random() * 0.55), 90);
    };
    u.onboundary = () => core.setLevel(0.9);
    u.onend = u.onerror = onSpeechEnd;
    speechSynthesis.speak(u);
  }

  function getAudioCtx() {
    if (!audioCtx) audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    if (audioCtx.state === "suspended") audioCtx.resume();
    return audioCtx;
  }

  // Frases curtas são juntadas para reduzir o número de sínteses
  function splitSentences(text) {
    const parts = text.match(/[^.!?…]+[.!?…]*\s*/g) || [text];
    const out = [];
    for (const p of parts) {
      if (out.length && (out[out.length - 1].length < 40 || p.trim().length < 12)) out[out.length - 1] += p;
      else out.push(p);
    }
    return out.map((s) => s.trim()).filter(Boolean);
  }

  async function synth(sentences, i, engine) {
    const r = await fetch("/api/tts", {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ text: sentences[i], engine, previous: sentences[i - 1] || "", next: sentences[i + 1] || "" }),
    });
    if (!r.ok) throw new Error("tts " + r.status);
    // O servidor avisa quando o ElevenLabs falhou e quem respondeu foi a voz local
    if (engine === "elevenlabs" && r.headers.get("X-Voz-Motor") === "local" && !warnedFallback) {
      warnedFallback = true;
      toast(`ElevenLabs indisponível (${decodeURIComponent(r.headers.get("X-Voz-Aviso") || "erro")}); usando sua voz local.`, "warn");
    }
    if (r.headers.get("X-Voz-Motor") === "elevenlabs") warnedFallback = false;
    return getAudioCtx().decodeAudioData(await r.arrayBuffer());
  }

  async function speakRemote(text, id, engine) {
    const sentences = splitSentences(text);
    const ctx = getAudioCtx();
    const analyser = ctx.createAnalyser();
    analyser.fftSize = 512;
    analyser.connect(ctx.destination);
    const buf = new Uint8Array(analyser.fftSize);
    let started = false;
    if (core.state !== "speaking") setCoreState("thinking");   // gerando a 1ª frase
    let next = synth(sentences, 0, engine);
    for (let i = 0; i < sentences.length; i++) {
      let audio;
      try { audio = await next; } catch (e) {
        if (id !== speakId) return;
        refreshVoiceStatus();
        toast("Vozes do servidor indisponíveis; usando a voz do navegador.", "warn");
        return speakBrowser(sentences.slice(i).join(" "));
      }
      if (id !== speakId) return;                      // foi interrompido (nova fala / microfone)
      if (i + 1 < sentences.length) next = synth(sentences, i + 1, engine);  // gera a próxima enquanto esta toca
      if (!started) { started = true; onSpeechStart(); }
      await new Promise((resolve) => {
        const src = ctx.createBufferSource();
        src.buffer = audio;
        src.connect(analyser);
        src.onended = resolve;
        currentSource = src;
        src.start();
        clearInterval(speakTimer);
        // Aqui o núcleo reage ao volume REAL do áudio
        speakTimer = setInterval(() => {
          analyser.getByteTimeDomainData(buf);
          let sum = 0;
          for (const x of buf) { const v = (x - 128) / 128; sum += v * v; }
          core.setLevel(Math.min(1, Math.sqrt(sum / buf.length) * 5));
        }, 40);
      });
      if (id !== speakId) return;
    }
    currentSource = null;
    onSpeechEnd();
  }

  let voiceInfo = null;
  async function refreshVoiceStatus() {
    let s = { local: { disponivel: false, carregando: false, erro: "sem conexão" }, elevenlabs: { configurado: false } };
    try { s = await (await fetch("/api/voz")).json(); vozCarregada = true; } catch (e) { /* servidor fora */ }
    voiceInfo = s;
    cloneAvailable = s.local.disponivel;
    elevenReady = s.elevenlabs.configurado;
    // Sem preferência salva: usa o ElevenLabs assim que ele estiver configurado
    if (!storedMode && elevenReady && voiceMode !== "elevenlabs") setVoiceMode("elevenlabs", false);
    renderVoiceStatus();
    return s;
  }

  function renderVoiceStatus() {
    if (!voiceInfo) return;
    const el = $("voiceStatus"), loc = voiceInfo.local;
    let text, cls;
    if (voiceMode === "elevenlabs") {
      [text, cls] = elevenReady ? ["● ElevenLabs conectado (reserva: sua voz local)", "ok"]
        : ["○ Configure a chave e a voz do ElevenLabs abaixo", "wait"];
    } else if (voiceMode === "clonada") {
      [text, cls] = loc.disponivel ? ["● Voz clonada local pronta", "ok"]
        : loc.carregando ? ["◌ Carregando o modelo de voz…", "wait"] : [`○ Indisponível (${loc.erro})`, "err"];
    } else {
      [text, cls] = ["● Voz do navegador (instantânea)", "ok"];
    }
    el.textContent = text;
    el.className = "key-status " + cls;
    $("elevenBox").hidden = voiceMode !== "elevenlabs";
  }

  // Enquanto o modelo local carrega, consulta de tempos em tempos
  (async function pollVoice() {
    const s = await refreshVoiceStatus();
    setTimeout(pollVoice, s.local.disponivel ? 30000 : 5000);
  })();

  function setVoiceMode(mode, persist = true) {
    voiceMode = mode;
    document.querySelectorAll('input[name="voiceMode"]').forEach((r) => { r.checked = r.value === mode; });
    if (persist) {
      storedMode = mode;
      try { localStorage.setItem("jarvis.voz", mode); } catch (e) { /* sem storage */ }
    }
    renderVoiceStatus();
  }

  document.querySelectorAll('input[name="voiceMode"]').forEach((r) => {
    r.checked = r.value === voiceMode;
    r.addEventListener("change", () => {
      setVoiceMode(r.value);
      if (r.value === "elevenlabs") {
        if (elevenReady) speak(`Voz do ElevenLabs ativada, ${tratamento}.`); else loadElevenOptions();
      } else {
        speak(r.value === "clonada" ? `Voz clonada local ativada, ${tratamento}.` : "Voz do navegador ativada.");
      }
    });
  });

  // ------------------------------------------------------------------ ElevenLabs: vozes e modelos
  let elReq = 0;
  async function loadElevenOptions() {
    const key = $("elKey").value.trim();
    if (!key && !(voiceInfo && voiceInfo.elevenlabs.tem_chave)) {
      $("elVoice").innerHTML = `<option value="">Cole a chave para ver as suas vozes</option>`;
      $("elModel").innerHTML = `<option value="">eleven_flash_v2_5 (padrão)</option>`;
      return;
    }
    const req = ++elReq, st = $("elStatus");
    $("elRefresh").classList.add("spin");
    st.textContent = "Buscando suas vozes…"; st.className = "key-status wait";
    try {
      const r = await fetch("/api/elevenlabs/opcoes", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ key }) });
      const d = await r.json();
      if (req !== elReq) return;
      if (!r.ok) throw new Error(d.detail || `HTTP ${r.status}`);
      const mine = d.vozes.filter((v) => ["cloned", "professional", "generated"].includes(v.categoria));
      const ready = d.vozes.filter((v) => !mine.includes(v));
      const opt = (v) => `<option value="${esc(v.id)}">${esc(v.nome)}</option>`;
      $("elVoice").innerHTML = (mine.length ? `<optgroup label="Suas vozes">${mine.map(opt).join("")}</optgroup>` : "")
        + (ready.length ? `<optgroup label="Vozes prontas do ElevenLabs">${ready.map(opt).join("")}</optgroup>` : "");
      if (d.voice_id) $("elVoice").value = d.voice_id;
      const models = d.modelos.length ? d.modelos : [{ id: d.modelo, nome: d.modelo, pt: true }];
      $("elModel").innerHTML = models.map((m) => `<option value="${esc(m.id)}">${esc(m.nome)}${m.pt ? "" : " (sem português)"}</option>`).join("");
      $("elModel").value = models.some((m) => m.id === d.modelo) ? d.modelo : models[0].id;
      st.textContent = mine.length ? `${mine.length} voz(es) sua(s) encontrada(s).`
        : "Nenhuma voz clonada na conta ainda; crie em Voices → Instant Voice Clone.";
      st.className = "key-status " + (mine.length ? "ok" : "wait");
    } catch (err) {
      if (req !== elReq) return;
      st.textContent = err.message; st.className = "key-status err";
    } finally {
      if (req === elReq) $("elRefresh").classList.remove("spin");
    }
  }

  let elTimer = null;
  $("elKey").addEventListener("input", () => {
    clearTimeout(elTimer);
    if ($("elKey").value.trim().length >= 20) elTimer = setTimeout(loadElevenOptions, 700);
  });
  $("elRefresh").addEventListener("click", loadElevenOptions);
  $("elSave").addEventListener("click", async () => {
    const st = $("elStatus");
    const body = { key: $("elKey").value.trim(), voice_id: $("elVoice").value, model: $("elModel").value };
    if (!body.voice_id) { st.textContent = "Escolha uma voz."; st.className = "key-status err"; return; }
    st.textContent = "Testando a chave e a voz…"; st.className = "key-status wait";
    $("elSave").disabled = true;
    try {
      const r = await fetch("/api/elevenlabs", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
      const d = await r.json();
      if (!r.ok) throw new Error(d.detail || `HTTP ${r.status}`);
      $("elKey").value = "";
      $("elKey").placeholder = "Já configurada — cole outra só se quiser trocar";
      st.textContent = "✓ ElevenLabs conectado."; st.className = "key-status ok";
      await refreshVoiceStatus();
      setVoiceMode("elevenlabs");
      speak(`Voz do ElevenLabs ativada, ${tratamento}. Como estou soando?`);
    } catch (err) {
      st.textContent = err.message; st.className = "key-status err";
    } finally {
      $("elSave").disabled = false;
    }
  });

  // ------------------------------------------------------------------ microfone (nível para o núcleo)
  let audioCtx = null, analyser = null, micData = null, micStream = null;
  async function ensureMicLevel() {
    if (analyser || !navigator.mediaDevices) return;
    try {
      micStream = await navigator.mediaDevices.getUserMedia({ audio: true });
      analyser = getAudioCtx().createAnalyser();
      analyser.fftSize = 512;
      micData = new Uint8Array(analyser.fftSize);
      audioCtx.createMediaStreamSource(micStream).connect(analyser);
      const loop = () => {
        if (core.state === "listening") {
          analyser.getByteTimeDomainData(micData);
          let sum = 0;
          for (const x of micData) { const v = (x - 128) / 128; sum += v * v; }
          core.setLevel(Math.min(1, Math.sqrt(sum / micData.length) * 6));
        }
        requestAnimationFrame(loop);
      };
      loop();
    } catch (e) { /* sem permissão: o núcleo apenas não reage ao volume */ }
  }

  // ------------------------------------------------------------------ reconhecimento de voz
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  let rec = null, recActive = false, wantListening = false, pushToTalk = false, awaitingCommand = false, awaitTimer = null;

  function setupRecognition() {
    if (!SR) return null;
    const r = new SR();
    r.lang = "pt-BR";
    r.interimResults = true;
    r.continuous = true;
    r.maxAlternatives = 1;
    r.onstart = () => {
      recActive = true; recStartAt = 0;
      if (recFalhas) report(`escuta religada depois de ${recFalhas} falha(s) (${document.visibilityState})`);
    };
    r.onend = () => {
      recActive = false;
      pushToTalk = false;
      // Reinicia sozinho no modo "Jarvis" ou enquanto espera a resposta a uma pergunta (nunca durante a fala).
      // Depois de erro (ex.: "network" do serviço de voz do Edge) espera cada vez mais, até 8 s.
      if ((wantListening || awaitingCommand) && !isSpeaking) {
        setTimeout(startRecognition, recFalhas ? Math.min(8000, 400 * 2 ** recFalhas) : 250);
        return;
      }
      $("micBtn").classList.toggle("on", false);
      if (core.state === "listening") setCoreState("idle");
    };
    r.onerror = (e) => {
      if (e.error !== "no-speech" && e.error !== "aborted") { recFalhas++; report(`reconhecimento de voz: ${e.error}`); }
      if (e.error === "not-allowed" || e.error === "service-not-allowed") {
        wantListening = false; $("wakeToggle").checked = false;
        toast("Permissão de microfone negada.", "warn");
      }
    };
    r.onresult = (ev) => {
      recFalhas = 0;
      let interim = "", final = "";
      for (let i = ev.resultIndex; i < ev.results.length; i++) {
        const txt = ev.results[i][0].transcript;
        if (ev.results[i].isFinal) final += txt; else interim += txt;
      }
      handleSpeech(interim.trim(), final.trim());
    };
    return r;
  }

  function handleSpeech(interim, final) {
    const wakeMode = $("wakeToggle").checked && !pushToTalk;
    const hasWake = (t) => /\bjarvis\b|\bjarbas\b|\bjarvas\b/i.test(t);
    const semNome = (t) => t.replace(/^.*?\b(jarvis|jarbas|jarvas)\b[\s,.!?]*/i, "").trim() || t;
    // Você falou por cima do JARVIS: ele para de falar e atende (o que for eco da própria voz é ignorado)
    if (isSpeaking) {
      const ouvido = (final || interim || "").trim();
      if (!podeInterromper() || !ouvido || ehEco(ouvido) || (!final && palavrasDe(ouvido).length < 2)) return;
      stopSpeaking();
      clearInterval(speakTimer); core.setLevel(0);
      expectingAnswer = false;
      if (!final) { startFollowUp(conversaSeg() * 1000 || FOLLOWUP_MS, "PODE FALAR"); $("interim").textContent = ouvido; return; }
      $("interim").textContent = "";
      send(semNome(final));
      return;
    }
    // Conversa aberta: enquanto você fala, o prazo de escuta é renovado
    if (awaitingCommand && interim) { clearTimeout(followUpTimer); followUpTimer = setTimeout(endFollowUp, followUpMs); }
    if (awaitingCommand && final) {
      clearTimeout(followUpTimer); clearTimeout(awaitTimer);
      awaitingCommand = false;
      $("interim").textContent = "";
      $("micBtn").classList.remove("on");
      send(semNome(final));
      if (!wantListening) stopRecognition();
      return;
    }
    if (interim) {
      if (!wakeMode || awaitingCommand || hasWake(interim)) {
        $("interim").textContent = interim;
        if (core.state !== "thinking") setCoreState("listening");
        avisarFlutuante({ ouvido: interim });
      }
    }
    if (!final) return;
    $("interim").textContent = "";

    if (!wakeMode) {                 // apertar-para-falar: tudo é comando
      send(final);
      stopRecognition();
      return;
    }
    if (awaitingCommand) {           // já ouviu "Jarvis", agora vem o comando
      clearTimeout(awaitTimer); awaitingCommand = false;
      send(final);
      return;
    }
    if (hasWake(final)) {
      const after = final.split(/\bjarvis\b|\bjarbas\b|\bjarvas\b/i).slice(1).join(" ").replace(/^[\s,.!?]+/, "").trim();
      if (after.length > 2) {
        send(after);
      } else {
        awaitingCommand = true;
        setCoreState("listening");
        speak(`Sim, ${chamar()}?`);
        awaitTimer = setTimeout(() => { awaitingCommand = false; if (core.state === "listening") setCoreState("idle"); }, 8000);
      }
    } else if (core.state === "listening") {
      setCoreState("idle");
    }
  }

  function startRecognition() {
    if (!SR) { toast("Reconhecimento de voz indisponível neste navegador. Use Chrome ou Edge.", "warn"); return; }
    if (!rec) rec = setupRecognition();
    if (recActive) return;
    recStartAt = Date.now();
    try { rec.start(); } catch (e) { /* ainda encerrando a sessão anterior: o vigia tenta de novo */ }
    ensureMicLevel();
  }

  // Vigia da escuta: se deveria estar ouvindo e não está (o Edge às vezes encerra o reconhecimento sem avisar,
  // principalmente com a janela minimizada ou escondida na bandeja), religa sozinho.
  let recStartAt = 0, recFalhas = 0;
  setInterval(() => {
    if (!SR || !booted || recActive || isSpeaking || !(wantListening || awaitingCommand)) return;
    if (recStartAt && Date.now() - recStartAt < 5000) return;          // uma tentativa ainda em andamento
    report(`vigia: escuta estava parada (${document.visibilityState}); religando`);
    startRecognition();
  }, 5000);
  function stopRecognition() { if (rec && recActive) rec.stop(); }

  let pausedForSpeech = false;
  function stopRecognitionForSpeech() {
    if (recActive) { pausedForSpeech = true; rec.abort(); }
  }
  function resumeRecognitionAfterSpeech() {
    if (pausedForSpeech || wantListening || awaitingCommand) {
      pausedForSpeech = false;
      if (wantListening || awaitingCommand) startRecognition();
    }
  }

  // Microfone liga/desliga: clicar de novo enquanto ele ouve o seu comando fecha a escuta
  // (no modo "Jarvis" ele volta a só esperar a palavra "Jarvis").
  const ouvindoComando = () => pushToTalk || awaitingCommand || $("micBtn").classList.contains("on");
  function fecharEscuta() {
    pushToTalk = false; awaitingCommand = false;
    clearTimeout(awaitTimer); clearTimeout(followUpTimer);
    $("micBtn").classList.remove("on");
    $("interim").textContent = "";
    if (recActive) rec.abort();                // o onend religa a escuta da palavra "Jarvis", se ligada
    if (core.state === "listening") setCoreState("idle");
  }

  $("micBtn").addEventListener("click", () => {
    if (ouvindoComando() && !isSpeaking) { fecharEscuta(); return; }
    stopSpeaking();
    pushToTalk = true;
    if (recActive) rec.abort();
    setTimeout(() => {
      startRecognition();
      $("micBtn").classList.add("on");
      setCoreState("listening");
    }, recActive ? 300 : 0);
  });

  // Modo "Jarvis" vem ligado por padrão; se você desligar, a escolha fica salva
  try { if (localStorage.getItem("jarvis.wake") === "0") $("wakeToggle").checked = false; } catch (e) { /* sem storage */ }

  $("wakeToggle").addEventListener("change", (e) => {
    try { localStorage.setItem("jarvis.wake", e.target.checked ? "1" : "0"); } catch (err) { /* sem storage */ }
    wantListening = e.target.checked;
    pushToTalk = false;
    if (wantListening) { startRecognition(); toast('Modo "Jarvis" ativo: diga "Jarvis" seguido do comando.', "ok"); }
    else stopRecognition();
  });

  // Atalho: segurar Espaço (fora do campo de texto) para falar
  document.addEventListener("keydown", (e) => {
    if (e.code === "Space" && document.activeElement === document.body && !e.repeat && booted) {
      e.preventDefault(); $("micBtn").click();
    }
  });

  // ------------------------------------------------------------------ WebSocket
  function connect() {
    ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`);
    ws.onopen = () => {
      connected = true;
      setTimeout(() => avisarFlutuante(), 600);
      $("link").className = "link on"; $("link").querySelector("b").textContent = "ONLINE";
    };
    ws.onclose = () => {
      connected = false;
      $("link").className = "link off"; $("link").querySelector("b").textContent = "OFFLINE";
      setCoreState("error");
      setTimeout(connect, 2000);
    };
    ws.onmessage = (ev) => onMessage(JSON.parse(ev.data));
  }

  function onMessage(m) {
    switch (m.type) {
      case "stats": renderStats(m.data); break;
      case "hello":
        currentBrain = m.brain;
        $("brainSelect").value = m.brain;
        if (core.state === "error") setCoreState("idle");
        addLog("SISTEMA", `Cérebro ativo: ${m.label}`, "sys");
        $("dgBrain").textContent = m.label;
        renderLlmCard();
        if (!$("page-core").hidden) renderCorePage();
        break;
      case "state":
        if (m.value === "thinking") setCoreState("thinking");
        else if (core.state === "thinking") setCoreState("idle");
        break;
      case "reply":
        $("caption").textContent = paraFala(m.text);
        addLog("JARVIS", m.text, "jarvis");
        avisarFlutuante({ resposta: paraFala(m.text).trim() });
        focoAteFimDaFala();
        expectingAnswer = expectsAnswer(m.text);
        if (/```/.test(m.text)) mostrarCodigo(m.text);
        if ($("voiceToggle").checked) speak(paraFala(m.text));
        else abrirConversa();                                         // sem voz: a escuta abre na hora
        break;
      case "action":
        if (m.status !== "executando") addLog("AÇÃO", `${m.tool}${m.mensagem ? " — " + m.mensagem : ""}`, m.status === "ok" ? "sys" : "warn");
        // Pediu música: o card do player aparece (ou atualiza) na hora
        if (m.status === "ok" && /^spotify_|^controle_midia/.test(m.tool)) { pollMusic(true); setTimeout(() => pollMusic(), 1500); }
        break;
      case "confirm_request":
        pendingConfirmId = m.id;
        $("confirmText").textContent = m.texto;
        $("confirm").hidden = false;
        avisarFlutuante();
        break;
      case "confirm_resolved":
        $("confirm").hidden = true; pendingConfirmId = null;
        avisarFlutuante();
        break;
      case "painel":
        showContext(m.tipo, m.dados || {});
        break;
      case "flutuante":
        doFlutuante(m);
        break;
      case "warning":
        toast(m.mensagem, "warn"); addLog("AVISO", m.mensagem, "warn");
        break;
      case "aviso_app":
        avisoApp(m.texto, m.detalhe);
        break;
    }
  }

  // Diagnóstico: o que acontece na janela (inclusive a oculta na bandeja) vai para o log do JARVIS
  let lastReport = "";
  const reportAt = {};
  function report(texto) {
    // Não repete a mesma linha mais de uma vez por minuto (o vigia pode disparar várias vezes)
    if (!connected || (texto === lastReport && Date.now() - (reportAt[texto] || 0) < 60000)) return;
    lastReport = texto; reportAt[texto] = Date.now();
    try { ws.send(JSON.stringify({ type: "cliente", texto })); } catch (e) { /* desconectado */ }
  }

  function send(text) {
    text = (text || "").trim();
    if (!text) return;
    if (!connected) { toast("Sem conexão com o núcleo do JARVIS.", "warn"); return; }
    addLog("VOCÊ", text, "user");
    $("caption").textContent = "";
    ws.send(JSON.stringify({ type: "chat", text }));
  }

  $("chatForm").addEventListener("submit", (e) => {
    e.preventDefault();
    send($("chatInput").value);
    $("chatInput").value = "";
  });
  // ------------------------------------------------------------------ cérebros e chaves de API
  let brains = [], currentBrain = "local";

  function renderBrains(list) {
    brains = list;
    const group = (tipo, rotulo, falta) => {
      const items = list.filter((b) => (b.tipo || (b.id === "local" ? "local" : "api")) === tipo);
      if (!items.length) return "";
      const opts = items.map((b) => `<option value="${b.id}">${esc(b.label)}${b.configured ? "" : ` — ${falta}`}</option>`).join("");
      return rotulo ? `<optgroup label="${rotulo}">${opts}</optgroup>` : opts;
    };
    $("brainSelect").innerHTML = group("conta", "Sua conta (seu plano)", "instalar…")
      + group("api", "Chave de API", "configurar…") + group("local", "", "");
    $("brainSelect").value = currentBrain;
    const apiOrLocal = list.filter((b) => b.tipo !== "conta");
    $("brainList").innerHTML = apiOrLocal.map((b) =>
      `<div class="${b.configured ? "on" : "off"}"><span>${esc(b.label)}</span><b>${b.configured ? "● ATIVO" : "○ SEM CHAVE"}</b></div>`).join("");
    const online = list.filter((b) => (b.tipo || "api") === "api" && b.id !== "local");
    const keep = $("keyBrain").value;
    $("keyBrain").innerHTML = online.map((b) => `<option value="${b.id}">${esc(b.label)}</option>`).join("");
    if (keep) $("keyBrain").value = keep;
    if (typeof renderLlmCard === "function" && $("llmBody")) renderLlmCard();
  }

  function fillKeyForm() {
    const b = brains.find((x) => x.id === $("keyBrain").value);
    if (!b) return;
    $("keyValue").value = "";
    $("keyValue").placeholder = b.configured ? "Já configurada — cole outra só se quiser trocar" : "Cole aqui a sua chave";
    $("keyLink").href = b.key_url || "#";
    $("keyStatus").textContent = ""; $("keyStatus").className = "key-status";
    loadModels();
  }

  // ------------------------------------------------------------------ lista de modelos
  let modelsReq = 0;
  function setModelOptions(list, current, hint) {
    const sel = $("keyModel");
    const ids = list.map((m) => m.id);
    if (current && !ids.includes(current)) list = [{ id: current, label: current }, ...list];
    sel.innerHTML = list.map((m) =>
      `<option value="${esc(m.id)}">${esc(m.label)}${m.label !== m.id ? ` — ${esc(m.id)}` : ""}${m.id === current ? "  • em uso" : ""}</option>`).join("")
      + `<option value="__custom">Outro (digitar o ID)…</option>`;
    sel.value = current && list.length ? current : (list[0] ? list[0].id : "__custom");
    $("keyModelCustom").hidden = sel.value !== "__custom";
    $("modelHint").textContent = hint || "";
  }

  async function loadModels() {
    const brain = $("keyBrain").value, key = $("keyValue").value.trim();
    const b = brains.find((x) => x.id === brain);
    if (!b) return;
    if (!b.configured && key.length < 10) {
      $("keyModel").innerHTML = `<option value="">Cole a chave para ver os modelos disponíveis</option>`;
      $("keyModelCustom").hidden = true;
      $("modelHint").textContent = `Padrão: ${b.model}`;
      return;
    }
    const req = ++modelsReq;
    $("modelRefresh").classList.add("spin");
    $("modelHint").textContent = "Buscando os modelos disponíveis…";
    try {
      const r = await fetch("/api/models", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ brain, key }) });
      const data = await r.json();
      if (req !== modelsReq) return;                       // o usuário já trocou de cérebro
      if (!r.ok) throw new Error(data.detail || `HTTP ${r.status}`);
      setModelOptions(data.models, data.atual, `${data.models.length} modelos disponíveis para esta chave.`);
    } catch (err) {
      if (req !== modelsReq) return;
      setModelOptions([], b.model, `Não consegui listar (${err.message}). Você pode digitar o ID em "Outro".`);
    } finally {
      if (req === modelsReq) $("modelRefresh").classList.remove("spin");
    }
  }

  let keyTimer = null;
  $("keyValue").addEventListener("input", () => {
    clearTimeout(keyTimer);
    if ($("keyValue").value.trim().length >= 20) keyTimer = setTimeout(loadModels, 700);
  });
  $("keyModel").addEventListener("change", () => {
    $("keyModelCustom").hidden = $("keyModel").value !== "__custom";
    if (!$("keyModelCustom").hidden) $("keyModelCustom").focus();
  });
  $("modelRefresh").addEventListener("click", loadModels);

  function chosenModel() {
    const v = $("keyModel").value;
    return v === "__custom" ? $("keyModelCustom").value.trim() : v;
  }

  function openSettings(brainId) {
    // Sem cérebro indicado: abre no que está em uso (ou no primeiro já configurado)
    const online = brains.filter((b) => (b.tipo || "api") === "api" && b.id !== "local");
    if (!brainId || !online.some((b) => b.id === brainId)) {
      brainId = online.some((b) => b.id === currentBrain) ? currentBrain : (online.find((b) => b.configured) || online[0] || {}).id;
    }
    if (brainId) $("keyBrain").value = brainId;
    fillKeyForm();
    renderVoiceStatus();
    loadAccounts();
    loadSpotify();
    loadAutostart();
    if (voiceMode === "elevenlabs") loadElevenOptions();
    $("settings").hidden = false;
    setTimeout(() => $("keyValue").focus(), 50);
  }

  $("brainSelect").addEventListener("change", (e) => {
    const b = brains.find((x) => x.id === e.target.value);
    if (b && !b.configured) {          // sem chave/CLI: abre a configuração e volta ao cérebro atual
      e.target.value = currentBrain;
      openSettings(b.id);
      return;
    }
    if (ws) ws.send(JSON.stringify({ type: "set_brain", brain: e.target.value }));
  });

  // ------------------------------------------------------------------ iniciar com o Windows
  async function loadAutostart() {
    try {
      const s = await (await fetch("/api/autostart")).json();
      $("autostartField").hidden = !s.disponivel;
      $("janelaField").hidden = !s.disponivel;
      $("navegadorField").hidden = !s.disponivel;
      $("flutuanteField").hidden = !s.disponivel;
      try { $("flutuanteToggle").checked = !!(await (await fetch("/api/flutuante")).json()).ativo; } catch (e) { /* servidor fora */ }
      loadNavegador();
      $("autostartToggle").checked = !!s.ativo;
    } catch (e) { /* servidor fora */ }
  }
  $("autostartToggle").addEventListener("change", async (e) => {
    try {
      const r = await fetch("/api/autostart", { method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify({ ativo: e.target.checked }) });
      const d = await r.json();
      if (!r.ok) throw new Error(d.detail || `HTTP ${r.status}`);
      e.target.checked = d.ativo;
      toast(d.ativo ? "O JARVIS vai iniciar junto com o Windows." : "Início automático desligado.", "ok");
    } catch (err) { toast(err.message, "warn"); loadAutostart(); }
  });

  // ------------------------------------------------------------------ navegador (Chrome / Edge)
  const NAV_NOME = { chrome: "Google Chrome", edge: "Microsoft Edge" };
  async function loadNavegador() {
    try {
      const d = await getJSON("/api/navegador");
      document.querySelectorAll('input[name="navegadorEscolha"]').forEach((r) => {
        r.checked = r.value === d.escolha;
        r.disabled = r.value !== "auto" && !d.instalados.includes(r.value);
        r.parentElement.title = r.disabled ? "Não instalado neste PC" : "";
      });
      $("navegadorHint").textContent = `Em uso agora: ${NAV_NOME[d.em_uso] || "—"}. Vale para a janela do JARVIS e o WhatsApp Web; `
        + "ao trocar, a janela reabre no navegador escolhido (na 1ª vez nele, permita o microfone de novo).";
    } catch (e) { /* servidor ainda subindo */ }
  }
  document.querySelectorAll('input[name="navegadorEscolha"]').forEach((r) => r.addEventListener("change", async () => {
    if (!r.checked) return;
    try {
      const resp = await fetch("/api/navegador", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ escolha: r.value }) });
      const d = await resp.json();
      if (!resp.ok) throw new Error(d.detail);
      toast(d.reabrindo ? `Reabrindo no ${NAV_NOME[d.em_uso]}…` : `Navegador: ${NAV_NOME[d.em_uso]} (vale na próxima abertura).`, "ok");
      loadNavegador();
    } catch (e) { toast("Não consegui salvar: " + e.message, "warn"); }
  }));

  $("flutuanteToggle").addEventListener("change", async (e) => {
    try {
      const r = await fetch("/api/flutuante", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ ativo: e.target.checked }) });
      e.target.checked = (await r.json()).ativo;
      toast(e.target.checked ? "Janelinha flutuante ligada: aparece quando o JARVIS estiver minimizado ou na bandeja." : "Janelinha flutuante desligada.", "ok");
    } catch (err) { toast("Não consegui salvar: " + err.message, "warn"); }
  });

  // ------------------------------------------------------------------ modo da janela (tela cheia / maximizada / janela)
  document.querySelectorAll('input[name="janelaModo"]').forEach((r) => r.addEventListener("change", async () => {
    if (!r.checked) return;
    try {
      const resp = await fetch("/api/janela", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ modo: r.value }) });
      if (!resp.ok) throw new Error((await resp.json()).detail);
      toast({ cheia: "O JARVIS vai abrir em tela cheia.", maximizada: "O JARVIS vai abrir maximizado.", janela: "O JARVIS vai abrir em janela." }[r.value], "ok");
    } catch (e) { toast("Não consegui salvar: " + e.message, "warn"); }
  }));

  // ------------------------------------------------------------------ perfil (nome e tratamento)
  let userName = "";
  // Como o JARVIS te chama: quase sempre o tratamento ("senhor"), às vezes o nome
  const chamar = () => (userName && Math.random() < 0.35 ? userName : tratamento);

  $("pfSave").addEventListener("click", async () => {
    try {
      const r = await fetch("/api/perfil", { method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify({ nome: $("pfNome").value.trim(), tratamento: $("pfTrat").value.trim() || "senhor" }) });
      const d = await r.json();
      userName = d.nome; tratamento = d.tratamento;
      toast(d.mensagem, "ok");
      speak(d.mensagem);
    } catch (e) { toast("Não consegui salvar.", "warn"); }
  });

  // ------------------------------------------------------------------ card do player do Spotify
  // Aparece sozinho quando algo toca (pelo JARVIS ou direto no app) e controla sem sair do JARVIS.
  const PLAY_ICON = "M8 5v14l11-7L8 5Z", PAUSE_ICON = "M6 5h4v14H6V5Zm8 0h4v14h-4V5Z";
  let music = null, musicAt = 0, musicTimer = null, musicIdleSince = 0, lastTrack = "";
  const fmtTime = (ms) => { const s = Math.max(0, Math.floor(ms / 1000)); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`; };

  function renderMusic(p, flash = false) {
    const card = $("musicCard");
    if (!p || !p.conectado || !p.ativo) {
      // Some depois de 30 s sem nada carregado no Spotify
      if (!musicIdleSince) musicIdleSince = Date.now();
      if (Date.now() - musicIdleSince > 30000) card.hidden = true;
      music = null;
      return;
    }
    musicIdleSince = 0;
    music = p; musicAt = Date.now();
    const wasHidden = card.hidden;
    card.hidden = false;
    card.classList.toggle("playing", !!p.tocando);
    if ((wasHidden || flash || p.musica !== lastTrack) && lastTrack !== null) {
      card.classList.remove("flash"); void card.offsetWidth; card.classList.add("flash");
    }
    lastTrack = p.musica;
    if (lyricsOpen) loadLyrics();
    if ($("mcImg").getAttribute("src") !== p.imagem) $("mcImg").src = p.imagem || "";
    $("mcLink").href = p.link || "#";
    $("mcTitle").textContent = p.musica || "—";
    $("mcTitle").title = p.musica || "";
    $("mcArtist").textContent = p.artistas || "";
    $("mcAlbum").textContent = p.album || "";
    $("mcDevice").textContent = p.dispositivo ? `· ${p.dispositivo}` : "";
    $("mcPlayIcon").setAttribute("d", p.tocando ? PAUSE_ICON : PLAY_ICON);
    $("mcShuffle").classList.toggle("on", !!p.aleatorio);
    $("mcRepeat").classList.toggle("on", p.repetir && p.repetir !== "off");
    $("mcRepeat").title = { off: "Repetir: desligado", context: "Repetir: lista", track: "Repetir: esta música" }[p.repetir] || "Repetir";
    if (p.volume != null && document.activeElement !== $("mcVol")) $("mcVol").value = p.volume;
    tickMusic();
  }

  function tickMusic() {
    if (!music) return;
    const pos = Math.min(music.duracao_ms, music.progresso_ms + (music.tocando ? Date.now() - musicAt : 0));
    $("mcPos").textContent = fmtTime(pos);
    $("mcDur").textContent = fmtTime(music.duracao_ms);
    $("mcFill").style.width = music.duracao_ms ? `${(pos / music.duracao_ms) * 100}%` : "0%";
    if (lyricsOpen) syncLyrics(pos);
    if (music.tocando && pos >= music.duracao_ms - 300) pollMusic();   // acabou: busca a próxima
  }
  setInterval(tickMusic, 500);

  let musicBusy = false;
  async function pollMusic(flash = false) {
    if (musicBusy) return;
    musicBusy = true;
    try { renderMusic(await (await fetch("/api/spotify/player")).json(), flash); }
    catch (e) { /* servidor fora: tenta de novo no próximo ciclo */ }
    finally { musicBusy = false; }
  }
  // Consulta a cada 3 s com o card visível; a cada 10 s quando escondido (para notar quando você der play no app)
  (function loopMusic() {
    pollMusic();
    setTimeout(loopMusic, $("musicCard").hidden ? 10000 : 3000);
  })();

  async function musicControl(acao, valor) {
    const btns = document.querySelectorAll(".mc-btn");
    btns.forEach((b) => { b.disabled = true; });
    try {
      const r = await fetch("/api/spotify/controle", { method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify({ acao, valor }) });
      const d = await r.json();
      if (!r.ok || d.ok === false) toast(d.mensagem || d.detail || "O Spotify recusou o comando.", "warn");
      if (d.player) renderMusic(d.player);
      setTimeout(() => pollMusic(), 1200);   // teclas de mídia levam um instante para refletir
    } catch (e) { toast("Sem conexão com o JARVIS.", "warn"); }
    finally { btns.forEach((b) => { b.disabled = false; }); }
  }

  $("mcPlay").addEventListener("click", () => musicControl("alternar"));
  $("mcNext").addEventListener("click", () => musicControl("proxima"));
  $("mcPrev").addEventListener("click", () => musicControl("anterior"));
  $("mcShuffle").addEventListener("click", () => musicControl("aleatorio"));
  $("mcRepeat").addEventListener("click", () => musicControl("repetir"));
  $("mcVol").addEventListener("change", (e) => musicControl("volume", Number(e.target.value)));
  $("mcBar").addEventListener("click", (e) => {
    if (!music || !music.duracao_ms) return;
    const rect = e.currentTarget.getBoundingClientRect();
    musicControl("posicao", Math.round(((e.clientX - rect.left) / rect.width) * music.duracao_ms));
  });

  // ------------------------------------------------------------------ quadro de letra
  // A letra vem do LRCLIB (servidor); o verso atual acompanha a posição do Spotify (a mesma do card).
  let lyricsOpen = false, lyricsKey = "", lyricsData = null, lyricsIdx = -2, lyricsLoading = false;
  const trackKey = () => (music ? `${music.musica}|${music.artistas}` : "");

  function lyricsMsg(text) {
    lyricsData = null;
    $("lyBody").classList.remove("plain");
    $("lyBody").innerHTML = "";
    const p = document.createElement("p");
    p.className = "ly-msg"; p.textContent = text;
    $("lyBody").appendChild(p);
  }

  async function openLyrics() {
    desfocarCard();
    fecharWeb();
    fecharAppWeb();
    lyricsOpen = true;
    $("lyrics").hidden = false;
    $("center").classList.add("lyrics-on");
    $("mcLyrics").classList.add("on");
    avisarFlutuante();
    if (!music) await pollMusic();
    loadLyrics(true);
  }

  function closeLyrics() {
    lyricsOpen = false; lyricsKey = "";
    $("lyrics").hidden = true;
    $("center").classList.remove("lyrics-on");
    $("mcLyrics").classList.remove("on");
    avisarFlutuante();
  }

  async function loadLyrics(force = false) {
    const key = trackKey();
    if (!key) { lyricsKey = ""; $("lyTitle").textContent = "LETRA"; $("lyArtist").textContent = ""; $("lyMode").textContent = ""; lyricsMsg("Nada tocando no Spotify agora."); return; }
    if ((!force && key === lyricsKey) || lyricsLoading) return;
    lyricsKey = key; lyricsLoading = true; lyricsIdx = -2;
    $("lyTitle").textContent = music.musica || "LETRA";
    $("lyArtist").textContent = music.artistas || "";
    $("lyMode").textContent = ""; $("lyMode").className = "tag";
    lyricsMsg("Buscando a letra…");
    try {
      const d = await (await fetch("/api/spotify/letra")).json();
      if (key !== trackKey()) { lyricsKey = ""; return; }      // trocou de música no meio da busca
      renderLyrics(d);
    } catch (e) { lyricsKey = ""; lyricsMsg("Sem conexão com o JARVIS."); }
    finally { lyricsLoading = false; }
  }

  function renderLyrics(d) {
    if (!d.ok) { lyricsMsg(d.mensagem || "Não encontrei a letra desta música."); return; }
    if (d.instrumental) { $("lyMode").textContent = "INSTRUMENTAL"; lyricsMsg("Música instrumental — sem letra."); return; }
    lyricsData = d;
    $("lyMode").textContent = d.sincronizada ? "SINCRONIZADA" : "SEM SINCRONIA";
    $("lyMode").className = "tag " + (d.sincronizada ? "ok" : "warn");
    const body = $("lyBody");
    body.innerHTML = "";
    body.classList.toggle("plain", !d.sincronizada);
    for (const l of d.linhas) {
      const p = document.createElement("p");
      p.textContent = l.texto || "♪";
      if (l.t != null) { p.dataset.t = l.t; p.title = "Pular para este verso"; }
      body.appendChild(p);
    }
    body.scrollTop = 0;
    if (music) syncLyrics(music.progresso_ms + (music.tocando ? Date.now() - musicAt : 0));
  }

  function syncLyrics(pos) {
    if (!lyricsData || !lyricsData.sincronizada) return;
    const ls = lyricsData.linhas;
    let i = -1;
    for (let k = 0; k < ls.length && ls[k].t <= pos + 200; k++) i = k;
    if (i === lyricsIdx) return;
    lyricsIdx = i;
    const body = $("lyBody"), ps = body.children;
    for (let k = 0; k < ps.length; k++) { ps[k].classList.toggle("now", k === i); ps[k].classList.toggle("past", k < i); }
    const el = ps[Math.max(i, 0)];
    if (el) body.scrollTo({ top: el.offsetTop - body.clientHeight / 2 + el.clientHeight / 2, behavior: "smooth" });
  }

  $("lyBody").addEventListener("click", (e) => {
    const p = e.target.closest("p[data-t]");
    if (p) musicControl("posicao", Number(p.dataset.t));
  });
  $("lyClose").addEventListener("click", closeLyrics);
  $("mcLyrics").addEventListener("click", () => (lyricsOpen ? closeLyrics() : openLyrics()));

  // ------------------------------------------------------------------ cards de contexto
  // "Atualizações do trabalho", notícias e clima trocam os cards de hardware pelos dados do assunto.
  // Voltam sozinhos em 5 min, pelo botão ◂ SISTEMA ou ao pedir o status do sistema.
  const CTX_MS = 5 * 60 * 1000;
  let ctxTimer = null;
  const back = '<button type="button" class="ghost mini ctx-back" data-ctx-back>◂ SISTEMA</button>';
  const ctxCard = (titulo, corpo, voltar = false) =>
    `<div class="card ctx-card"><h3><span>${titulo}</span>${voltar ? back : ""}</h3>${corpo}</div>`;
  const num = (v, label, alert = false) => `<div class="ctx-num${alert ? " alert" : ""}"><b>${esc(v)}</b><small>${label}</small></div>`;

  function showContext(tipo, d) {
    if (tipo === "cards") {
      applyLayout(d.layout || layout, d.foco === undefined ? d.card : null);
      if (d.foco) focarCard(d.foco, true); else if (d.foco === "") desfocarCard();
      return;
    }
    if (tipo === "custom") { showCustomCard(d.card); return; }
    if (tipo.startsWith("card:")) {                  // agenda, e-mail, GitHub, Docker: mostra e atualiza o card
      const id = tipo.slice(5);
      if (layout && layout.ocultos.includes(id)) setHidden(id, false);
      if (PAINEL_RENDER[id]) PAINEL_RENDER[id](d);
      if (cardEl(id)) avisarFlutuante({ cartoes: [cardEl(id).outerHTML] });
      focarCard(id, true);
      return;
    }
    if (tipo === "letra") { if (d.acao === "fechar") closeLyrics(); else openLyrics(); return; }
    if (tipo === "web") { abrirWeb(d.url); return; }
    if (tipo === "apps") { if (d.onde === "card") novoAppCard({ tipo: "app", app: d.app }); else abrirAppWeb(d.app); return; }
    if (tipo === "sistema") { hideContext(); return; }
    const render = { trabalho: renderWork, noticias: renderNews, clima: renderWeather }[tipo];
    if (!render) return;
    const [left, right] = render(d);
    avisarFlutuante({ cartoes: [left, right].filter(Boolean) });     // na janelinha também
    if (focoId === "ctx") desfocarCard();
    $("ctxLeft").innerHTML = left;
    $("ctxRight").innerHTML = right;
    $("ctxLeft").hidden = false; $("ctxRight").hidden = !right;
    document.querySelector(".hud").classList.add("ctx-on");
    clearTimeout(ctxTimer);
    ctxTimer = setTimeout(hideContext, CTX_MS);
    focarContexto(tipo === "noticias");
  }

  // Cards montados pela IA (tabela, lista, números, texto). Vários seguidos (ex.: pilotos e construtores)
  // vão se alternando entre as duas colunas; um pedido novo, mais tarde, substitui os anteriores.
  let customAt = 0, customCount = 0;
  function renderCustom(c) {
    const alvo = (c.destacar || "").toLowerCase();
    const realce = (txt) => alvo && String(txt).toLowerCase().includes(alvo);
    let corpo = "";
    if (c.tipo === "tabela") {
      const head = c.colunas && c.colunas.length ? `<thead><tr>${c.colunas.map((h) => `<th>${esc(h)}</th>`).join("")}</tr></thead>` : "";
      corpo = `<div class="ctx-table-wrap"><table class="ctx-table">${head}<tbody>${c.linhas.map((r) =>
        `<tr class="${realce(r.join(" ")) ? "hl" : ""}">${r.map((v) => `<td>${esc(v)}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`;
    } else if (c.tipo === "numeros") {
      corpo = `<div class="ctx-grid">${c.itens.map((i) => `<div class="ctx-num"><b>${esc(i.valor || "—")}</b><small>${esc(i.titulo.toUpperCase())}</small>
        ${i.detalhe ? `<em>${esc(i.detalhe)}</em>` : ""}</div>`).join("")}</div>`;
    } else if (c.tipo === "lista") {
      corpo = `<ul class="ctx-list">${c.itens.map((i) => `<li class="${realce(i.titulo) ? "hl" : ""}"><div class="row1"><b>${esc(i.titulo)}</b>
        ${i.valor ? `<span class="af">${esc(i.valor)}</span>` : ""}</div>${i.detalhe ? `<small>${esc(i.detalhe)}</small>` : ""}</li>`).join("")}</ul>`;
    } else {
      corpo = `<p class="ctx-text">${esc(c.texto)}</p>`;
    }
    if (c.fonte) corpo += `<p class="ctx-muted" style="margin-top:8px">Fonte: ${esc(c.fonte)}</p>`;
    return ctxCard(esc(c.titulo.toUpperCase()), corpo, true);
  }

  function showCustomCard(c) {
    if (!c) return;
    const seguido = Date.now() - customAt < 20000;
    customAt = Date.now();
    customCount = seguido ? customCount + 1 : 0;
    const html = renderCustom(c);
    avisarFlutuante({ cartoes: [html], somar: seguido });
    if (focoId === "ctx") desfocarCard();
    if (!seguido) { $("ctxLeft").innerHTML = html; $("ctxRight").innerHTML = ""; }
    else if (customCount % 2 === 1) $("ctxRight").insertAdjacentHTML("beforeend", html);
    else $("ctxLeft").insertAdjacentHTML("beforeend", html);
    // Só a primeira coluna ganha o botão de voltar
    $("ctxRight").querySelectorAll("[data-ctx-back]").forEach((b) => b.remove());
    $("ctxLeft").hidden = false; $("ctxRight").hidden = !$("ctxRight").innerHTML.trim();
    document.querySelector(".hud").classList.add("ctx-on");
    showPage("command");
    clearTimeout(ctxTimer);
    ctxTimer = setTimeout(hideContext, CTX_MS);
    focarContexto(customCount % 2 === 1);
  }

  // Resposta com código: também abre um card grande com o código formatado (cabe mais que o card Conversa)
  function mostrarCodigo(text) {
    $("ctxLeft").innerHTML = ctxCard("CÓDIGO", `<div class="code-ctx">${formatarTexto(text)}</div>`, true);
    $("ctxRight").innerHTML = "";
    $("ctxLeft").hidden = false; $("ctxRight").hidden = true;
    document.querySelector(".hud").classList.add("ctx-on");
    clearTimeout(ctxTimer);
    ctxTimer = setTimeout(hideContext, CTX_MS * 2);
  }
  document.addEventListener("click", async (e) => {
    const b = e.target.closest(".code-copy");
    if (!b) return;
    try { await navigator.clipboard.writeText(b.closest(".code-box").querySelector("code").innerText); toast("Código copiado.", "ok"); }
    catch (err) { toast("Selecione o código e copie com Ctrl+C.", "warn"); }
  });

  function hideContext() {
    clearTimeout(ctxTimer);
    if (focoId === "ctx") desfocarCard();
    document.querySelector(".hud").classList.remove("ctx-on");
    $("ctxLeft").hidden = $("ctxRight").hidden = true;
    $("ctxLeft").innerHTML = $("ctxRight").innerHTML = "";
  }
  for (const id of ["ctxLeft", "ctxRight"]) {
    $(id).addEventListener("click", (e) => { if (e.target.closest("[data-ctx-back]")) hideContext(); });
  }

  const REPORT_CAMPOS = [
    ["totalColaboradoresAteMomento", "Atendentes"], ["faltasDoDia", "Faltas"], ["filaDigital", "Fila digital"],
    ["tmeDigital", "TME digital"], ["emAtendimentoAgoraChat", "Em atendimento"], ["entrantesChat", "Entrantes chat"],
    ["filaTelefonicaContact", "Fila URA (Contact)"], ["tmeTelefonicoContact", "TME URA"], ["backlog", "Backlog"],
    ["callRetornoN2", "Retorno N2"], ["totalEventosAtivos", "Eventos"], ["estimativaClientesAfetados", "Afetados"],
  ];

  function renderWork(d) {
    const m = Array.isArray(d.massivas) ? d : (d.massivas || {});
    let left;
    if (m.ok === false) {
      left = ctxCard("MASSIVAS", `<p class="ctx-warn">${esc(m.mensagem)}</p>`, true);
    } else {
      const lista = m.massivas || [];
      const max = Math.max(1, ...lista.map((x) => x.afetados || 0));
      const filtro = m.local_pesquisado && m.local_pesquisado !== "(todos)" ? ` · ${esc(m.local_pesquisado).toUpperCase()}` : "";
      const abertas = filtro ? m.encontradas : m.total_ativas_no_geral;
      const itens = lista.map((x) => {
        const pppoe = (x.tipo || "").startsWith("queda PPPoE");
        const nome = pppoe ? `PPPoE ${x.concentrador || ""}` : (x.cidade || x.olt_slot_pon || "—");
        const sub = pppoe ? (x.cidades || []).slice(0, 4).join(", ") : (x.olt_slot_pon || "");
        const status = (x.status || "").toUpperCase();
        return `<li><div class="row1"><b>${esc(nome)}</b><span class="af">${esc(x.afetados)} afet.</span></div>
          <small>${pppoe ? '<span class="tag warn">GRAFANA</span> ' : ""}${esc(status)}${x.desde ? " · desde " + esc(x.desde) : ""}</small>
          ${sub ? `<small>${esc(sub)}</small>` : ""}
          <div class="bar"><i style="width:${((x.afetados || 0) / max) * 100}%"></i></div></li>`;
      }).join("");
      left = ctxCard(`MASSIVAS${filtro}`,
        `<div class="ctx-nums">${num(abertas ?? 0, "EM ABERTO", abertas > 0)}${num(m.total_afetados_encontrados ?? 0, "AFETADOS")}
         </div>${m.ultima_sincronizacao ? `<p class="ctx-muted">Sincronizado pelo MHnet Notifier em ${esc(m.ultima_sincronizacao)}</p>` : ""}
         ${m.aviso ? `<p class="ctx-warn" style="margin:4px 0 10px">⚠ ${esc(m.aviso)}</p>` : ""}
         ${itens ? `<ul class="ctx-list" style="margin-top:8px">${itens}</ul>` : '<p class="ctx-muted">Nenhuma massiva em aberto.</p>'}
`, true);
    }
    let right = "";
    const n2 = m.avisos_n2 || [];
    if (n2.length) {
      right += ctxCard("AVISOS N2", `<ul class="ctx-list">${n2.map((a) => `<li><div class="row1"><b>${esc(a.titulo)}</b>
        <span class="tag ${/alerta|alta|crit/i.test(a.prioridade || "") ? "bad" : "warn"}">${esc((a.prioridade || "").toUpperCase())}</span></div>
        <small>${esc(a.status || "")}${a.criado_em ? " · " + esc(a.criado_em) : ""}</small></li>`).join("")}</ul>`);
    }
    if ("status_report" in d) {
      const sr = d.status_report;
      let corpo;
      if (sr && typeof sr === "object") {
        const tag = sr.de_hoje ? '<span class="tag ok">DE HOJE</span>' : '<span class="tag warn">ANTIGO</span>';
        if (typeof sr.campos === "object") {
          const linhas = REPORT_CAMPOS.filter(([k]) => sr.campos[k] != null && sr.campos[k] !== "")
            .map(([k, label]) => `<dt>${label}</dt><dd>${esc(sr.campos[k])}</dd>`).join("");
          corpo = `<p class="ctx-muted">${tag} gerado em ${esc(sr.gerado_em)}</p><dl class="kv-ctx" style="margin-top:8px">${linhas}</dl>`;
        } else {
          corpo = `<p class="ctx-muted">${tag} gerado em ${esc(sr.gerado_em)}</p><p class="ctx-muted" style="white-space:pre-wrap">${esc(String(sr.campos || "").slice(0, 600))}</p>`;
        }
      } else {
        corpo = `<p class="ctx-muted">${esc(sr || "Nenhum relatório salvo ainda.")}</p>`;
      }
      if (d.status_robot_aberto) corpo += '<p class="ctx-warn">● StatusRobot aberto agora</p>';
      right += ctxCard("STATUS REPORT", corpo);
    }
    if (!right && (m.massivas || []).length) {
      const pppoe = m.massivas.filter((x) => (x.tipo || "").startsWith("queda PPPoE")).length;
      right = ctxCard("POR TIPO", `<div class="ctx-nums">${num(m.massivas.length - pppoe, "MASSIVAS")}${num(pppoe, "PPPoE")}</div>`);
    }
    return [left, right];
  }

  function renderNews(d) {
    const ns = d.noticias || [];
    if (!ns.length) return [ctxCard("NOTÍCIAS", `<p class="ctx-warn">${esc(d.mensagem || "Sem notícias agora.")}</p>`, true), ""];
    const tema = d.tema && d.tema !== "principais do dia" ? ` · ${esc(d.tema).toUpperCase()}` : "";
    const link = (n, inner) => (n.link ? `<a href="${esc(n.link)}" target="_blank" rel="noopener">${inner}</a>` : inner);
    const [hero, ...resto] = ns;
    const left = ctxCard(`NOTÍCIAS${tema}`,
      `<div class="news-hero">${link(hero, `<b>${esc(hero.titulo)}</b><small>${esc(hero.fonte)}${hero.quando ? " · " + esc(hero.quando) : ""}</small>`)}</div>`, true);
    const right = resto.length ? ctxCard("MANCHETES", `<ul class="ctx-list">${resto.map((n) =>
      `<li>${link(n, `<b>${esc(n.titulo)}</b><small>${esc(n.fonte)}${n.quando ? " · " + esc(n.quando) : ""}</small>`)}</li>`).join("")}</ul>
      <p class="ctx-muted" style="margin-top:8px">Fonte: ${esc(d.fonte || "Google Notícias")}</p>`) : "";
    return [left, right];
  }

  const wxIcon = (c) => (c === 0 ? "☀️" : c <= 2 ? "🌤️" : c === 3 ? "☁️" : c <= 48 ? "🌫️" : c <= 67 ? "🌧️"
    : c <= 77 ? "❄️" : c <= 82 ? "🌦️" : "⛈️");
  const DIAS = ["DOM", "SEG", "TER", "QUA", "QUI", "SEX", "SÁB"];

  function renderWeather(d) {
    if (!d.agora) return [ctxCard("CLIMA", `<p class="ctx-warn">${esc(d.mensagem || "Previsão indisponível.")}</p>`, true), ""];
    const a = d.agora;
    const left = ctxCard(`CLIMA · ${esc((d.local || "").split(",")[0]).toUpperCase()}`,
      `<div class="wx-now"><span class="wx-icon">${wxIcon(a.codigo)}</span><div><div class="wx-temp">${Math.round(a.temperatura_c)}°</div>
       <div class="wx-cond">${esc(a.condicao)}</div></div></div>
       <dl class="kv-ctx"><dt>Sensação</dt><dd>${Math.round(a.sensacao_c)}°C</dd><dt>Umidade</dt><dd>${esc(a.umidade_pct)}%</dd>
       <dt>Vento</dt><dd>${Math.round(a.vento_kmh)} km/h</dd><dt>Chuva agora</dt><dd>${esc(a.chuva_mm)} mm</dd></dl>
       <p class="ctx-muted" style="margin-top:8px">${esc(d.local || "")} · ${esc(d.fonte || "")}</p>`, true);
    const dias = d.dias || [];
    const lo = Math.min(...dias.map((x) => x.min_c)), hi = Math.max(...dias.map((x) => x.max_c));
    const span = Math.max(1, hi - lo);
    const ymd = (dt) => `${dt.getFullYear()}-${String(dt.getMonth() + 1).padStart(2, "0")}-${String(dt.getDate()).padStart(2, "0")}`;
    const agora = new Date(), hoje = ymd(agora), amanha = ymd(new Date(agora.getTime() + 864e5));
    const linhas = dias.map((x) => {
      const dt = new Date(x.data + "T12:00:00");
      const nome = x.data === hoje ? "HOJE" : x.data === amanha ? "AMANHÃ" : DIAS[dt.getDay()];
      return `<div class="wx-day"><span class="d">${nome}</span><span class="i" title="${esc(x.condicao)}">${wxIcon(x.codigo)}</span>
        <div class="wx-range"><i style="left:${((x.min_c - lo) / span) * 100}%;right:${100 - ((x.max_c - lo) / span) * 100}%"></i></div>
        <span class="r">${Math.round(x.min_c)}° / ${Math.round(x.max_c)}°</span><span class="c">${x.chance_chuva_pct ?? 0}%</span></div>`;
    }).join("");
    return [left, dias.length ? ctxCard("PRÓXIMOS DIAS", linhas) : ""];
  }

  // ------------------------------------------------------------------ cards: layout, arrastar, fixar, ocultar
  // O layout fica no servidor (data/cards.json): você arrasta aqui e o JARVIS mexe pela ferramenta controlar_cards.
  const ZONES = ["esquerda", "direita", "extra"];
  const PIN_ICON = '<svg viewBox="0 0 24 24"><path d="M16 3v2h-1v6l3 3v2h-5v5l-1 1-1-1v-5H6v-2l3-3V5H8V3h8Z"/></svg>';
  const FOCO_ICON = '<svg viewBox="0 0 24 24"><path d="M4 4h6v2H6v4H4V4Zm10 0h6v6h-2V6h-4V4ZM4 14h2v4h4v2H4v-6Zm14 0h2v6h-6v-2h4v-4Z"/></svg>';
  const HIDE_ICON = '<svg viewBox="0 0 24 24"><path d="M6.4 5 12 10.6 17.6 5 19 6.4 13.4 12l5.6 5.6-1.4 1.4-5.6-5.6L6.4 19 5 17.6l5.6-5.6L5 6.4 6.4 5Z"/></svg>';
  let layout = null, catalogo = {}, dragId = null;
  const nomeCard = (id) => catalogo[id]?.nome || cardEl(id)?.querySelector("h3 span")?.textContent || id;
  const cardEl = (id) => document.querySelector(`.card[data-card="${id}"]`);
  const zoneEl = (z) => document.querySelector(`.zone[data-zone="${z}"]`);
  const cardVisible = (id) => { const el = cardEl(id); return !!el && !el.classList.contains("oculto") && !el.hidden; };

  function decorateCards() {
    document.querySelectorAll(".zone > .card[data-card]").forEach((card) => {
      const h3 = card.querySelector("h3");
      if (!h3 || h3.querySelector(".card-tools")) return;
      const tools = document.createElement("span");
      tools.className = "card-tools";
      tools.innerHTML = `<button type="button" class="ct ct-foco" title="Trazer para o foco (centro da tela)">${FOCO_ICON}</button>`
        + `<button type="button" class="ct ct-pin" title="Fixar no topo (não some com os cards de contexto)">${PIN_ICON}</button>`
        + `<button type="button" class="ct ct-hide" title="Ocultar (reabra pelo botão ▦ no topo)">${HIDE_ICON}</button>`;
      h3.appendChild(tools);
      tools.querySelector(".ct-pin").addEventListener("click", (e) => { e.stopPropagation(); togglePin(card.dataset.card); });
      tools.querySelector(".ct-foco").addEventListener("click", (e) => {
        e.stopPropagation();
        if (focoId === card.dataset.card) desfocarCard(); else focarCard(card.dataset.card);
      });
      tools.querySelector(".ct-hide").addEventListener("click", (e) => {
        e.stopPropagation();
        if (ehAppCard(card.dataset.card)) fecharAppCard(card.dataset.card); else setHidden(card.dataset.card, true);
      });
      if (ehAppCard(card.dataset.card)) tools.querySelector(".ct-hide").title = "Fechar";
      // Arrastar só pelo título (para não atrapalhar sliders, botões e seleção de texto)
      h3.addEventListener("mousedown", (e) => { if (!e.target.closest("button, select, input, a") && !card.closest("#foco")) card.draggable = true; });
      card.addEventListener("dragstart", (e) => {
        if (!card.draggable) { e.preventDefault(); return; }
        dragId = card.dataset.card; card.classList.add("dragging");
        arrastandoCard = true; relatarAppWeb();                        // os apps encaixados saem da frente do arrasto
        e.dataTransfer.effectAllowed = "move"; e.dataTransfer.setData("text/plain", dragId);
      });
      card.addEventListener("dragend", () => {
        card.draggable = false; card.classList.remove("dragging"); dragId = null;
        arrastandoCard = false; setTimeout(relatarAppWeb, 50);
        document.querySelectorAll(".drop-mark").forEach((m) => m.remove());
        document.querySelectorAll(".zone.drop-on").forEach((z) => z.classList.remove("drop-on"));
      });
    });
    document.addEventListener("mouseup", () => document.querySelectorAll(".card[draggable=true]:not(.dragging)").forEach((c) => { c.draggable = false; }));
  }

  function dropTarget(zone, y) {
    const cards = [...zone.querySelectorAll(":scope > .card[data-card]:not(.dragging)")].filter((c) => c.offsetParent);
    return cards.find((c) => { const r = c.getBoundingClientRect(); return y < r.top + r.height / 2; }) || null;
  }

  ZONES.forEach((z) => {
    const zone = zoneEl(z);
    zone.addEventListener("dragover", (e) => {
      if (!dragId) return;
      e.preventDefault();
      zone.classList.add("drop-on");
      let mark = document.querySelector(".drop-mark");
      if (!mark) { mark = document.createElement("div"); mark.className = "drop-mark"; }
      const before = dropTarget(zone, e.clientY);
      if (before) zone.insertBefore(mark, before); else zone.appendChild(mark);
    });
    zone.addEventListener("dragleave", (e) => { if (!zone.contains(e.relatedTarget)) zone.classList.remove("drop-on"); });
    zone.addEventListener("drop", (e) => {
      if (!dragId) return;
      e.preventDefault();
      const mark = document.querySelector(".drop-mark");
      if (mark) { zone.insertBefore(cardEl(dragId), mark); mark.remove(); }
      zone.classList.remove("drop-on");
      // Soltou um card fixado no meio dos outros? Ele continua fixado (vai para o topo)
      layout = readLayout();
      applyLayout(layout);
      saveLayout();
    });
  });

  function readLayout() {
    const zonas = {};
    ZONES.forEach((z) => { zonas[z] = [...zoneEl(z).querySelectorAll(":scope > .card[data-card], :scope > .foco-ph")].map((c) => c.dataset.card || c.dataset.ph); });
    return { zonas, fixados: layout ? layout.fixados : [], ocultos: layout ? layout.ocultos : [] };
  }

  function applyLayout(l, destaque) {
    if (!l || !l.zonas) return;
    layout = l;
    ZONES.forEach((z) => {
      const zone = zoneEl(z);
      const ids = l.zonas[z] || [];
      const ordem = [...ids.filter((id) => l.fixados.includes(id)), ...ids.filter((id) => !l.fixados.includes(id))];
      ordem.forEach((id) => { const el = id === focoId ? focoMarcador(id) : cardEl(id); if (el) zone.appendChild(el); });
    });
    document.querySelectorAll(".card[data-card]").forEach((el) => {
      const id = el.dataset.card;
      el.classList.toggle("pinned", l.fixados.includes(id));
      el.classList.toggle("oculto", l.ocultos.includes(id));
    });
    if (focoId && l.ocultos.includes(focoId)) desfocarCard();
    if (destaque && cardEl(destaque)) {
      const el = cardEl(destaque);
      el.classList.remove("flash-card"); void el.offsetWidth; el.classList.add("flash-card");
      if (!el.classList.contains("oculto")) el.scrollIntoView({ behavior: "smooth", block: "nearest" });
      refreshCard(destaque);
    }
    renderCardsMenu();
  }

  async function saveLayout() {
    try {
      const r = await fetch("/api/cards", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(layout) });
      const d = await r.json();
      if (d.layout) layout = d.layout;
    } catch (e) { toast("Não consegui salvar o layout dos cards.", "warn"); }
  }

  function togglePin(id) {
    const on = layout.fixados.includes(id);
    layout.fixados = on ? layout.fixados.filter((x) => x !== id) : [...layout.fixados, id];
    applyLayout(layout, id); saveLayout();
    toast(on ? `${nomeCard(id)} desafixado.` : `${nomeCard(id)} fixado no topo.`, "ok");
    if (ehAppCard(id)) avisarFlutuante();                         // fixado = vai junto para a janelinha
  }

  function setHidden(id, hide) {
    layout.ocultos = hide ? [...new Set([...layout.ocultos, id])] : layout.ocultos.filter((x) => x !== id);
    if (hide) layout.fixados = layout.fixados.filter((x) => x !== id);
    applyLayout(layout, hide ? null : id); saveLayout();
    if (hide) toast(`${catalogo[id]?.nome || id} ocultado. Reabra pelo botão ▦ no topo.`, "ok");
  }

  function renderCardsMenu() {
    if (!layout) return;
    const linhas = Object.entries(catalogo).map(([id, c]) => {
      const vis = !layout.ocultos.includes(id), pin = layout.fixados.includes(id);
      return `<div class="dd-row"><label><input type="checkbox" data-vis="${id}" ${vis ? "checked" : ""}> ${esc(c.nome)}</label>
        <button type="button" class="ct ct-pin" data-pin="${id}" title="Fixar" style="${pin ? "color:var(--amber);border-color:var(--amber)" : ""}">${PIN_ICON}</button></div>`;
    }).join("");
    $("cardsMenu").innerHTML = `<h4>CARDS DO COMMAND CENTER</h4>${linhas}
      <div class="dd-foot"><button type="button" class="ghost mini" data-restaurar>RESTAURAR PADRÃO</button></div>
      <p class="dd-hint">Arraste um card pelo título para mudar de lugar. Por voz: "Jarvis, fixa o card do Spotify",
      "move os processos para a esquerda", "esconde a rede".</p>`;
  }

  $("cardsMenu").addEventListener("change", (e) => {
    const id = e.target.dataset.vis;
    if (id) setHidden(id, !e.target.checked);
  });
  $("cardsMenu").addEventListener("click", async (e) => {
    const pin = e.target.closest("[data-pin]");
    if (pin) { togglePin(pin.dataset.pin); return; }
    if (e.target.closest("[data-restaurar]")) {
      const d = await (await fetch("/api/cards", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ restaurar: true }) })).json();
      applyLayout(d.layout); toast("Layout padrão restaurado.", "ok");
    }
  });

  function toggleDropdown(id, show) {
    ["cardsMenu", "bellMenu"].forEach((m) => { $(m).hidden = m === id ? (show ?? !$(m).hidden) : true; });
  }
  $("cardsBtn").addEventListener("click", (e) => { e.stopPropagation(); toggleDropdown("cardsMenu"); });
  $("bellBtn").addEventListener("click", (e) => { e.stopPropagation(); toggleDropdown("bellMenu"); });
  document.addEventListener("click", (e) => { if (!e.target.closest(".dropdown")) toggleDropdown(null); });

  // ------------------------------------------------------------------ card em foco
  // Como a letra da música: o próprio card (ao vivo) vai para o centro, maior. Pelo botão ⤢ do card, por voz
  // ("foca na memória", "traz a agenda pro centro") ou pelo JARVIS quando a conversa é sobre ele.
  // Os trazidos pelo JARVIS voltam sozinhos em 5 min; os seus ficam até você fechar.
  // focoId: id do card do catálogo, ou "ctx" para um card de contexto (notícias, clima, cards da IA).
  // Os que o JARVIS traz (auto) voltam para a coluna quando ele termina de falar sobre eles.
  let focoId = null, focoEl = null, focoAuto = false, focoFimDaFala = false, focoTimer = null;
  const focoMarcador = (id = focoId) => document.querySelector(`.foco-ph[data-ph="${id}"]`);
  function focarElemento(el, chave, titulo, auto) {
    clearTimeout(focoTimer);
    if (auto && (!$("web").hidden || !$("appw").hidden)) return;   // você está lendo/usando algo: não troca o centro
    if (focoEl === el) {
      focoAuto = focoAuto && auto;
      if (focoAuto) focoTimer = setTimeout(desfocarCard, CTX_MS);
      return;
    }
    desfocarCard();
    if (!auto) { fecharWeb(); fecharAppWeb(); }
    if (lyricsOpen) closeLyrics();
    const ph = document.createElement("div");
    ph.className = "foco-ph"; ph.dataset.ph = chave;
    el.before(ph);
    $("focoBody").appendChild(el);
    focoId = chave; focoEl = el; focoAuto = auto; focoFimDaFala = false;
    if (auto) focoTimer = setTimeout(desfocarCard, CTX_MS);      // segurança, se a fala não terminar
    $("focoTitulo").textContent = titulo.toUpperCase();
    $("foco").hidden = false;
    $("center").classList.add("foco-on");
    showPage("command");
    window.dispatchEvent(new Event("resize"));     // gráficos redesenham no tamanho novo
  }
  function focarCard(id, auto = false) {
    const el = cardEl(id);
    if (!el) return;
    if (layout && layout.ocultos.includes(id)) setHidden(id, false);
    focarElemento(el, id, nomeCard(id), auto);
    if (focoEl === el) { el.querySelector(".ct-foco")?.classList.add("on"); refreshCard(id); }
  }
  // O card de contexto principal (manchetes, clima de agora, o card que a IA acabou de montar) vai para o centro
  function focarContexto(preferirDireita = false) {
    const ultimo = (id) => { const cs = $(id).hidden ? [] : $(id).querySelectorAll(":scope > .ctx-card"); return cs[cs.length - 1]; };
    const el = preferirDireita ? ultimo("ctxRight") || ultimo("ctxLeft") : ultimo("ctxLeft") || ultimo("ctxRight");
    if (el) focarElemento(el, "ctx", el.querySelector("h3 span")?.textContent || "Contexto", true);
  }
  function desfocarCard() {
    clearTimeout(focoTimer);
    if (!focoEl) return;
    const el = focoEl, ph = focoMarcador();
    if (ph) ph.replaceWith(el);
    else if (focoId === "ctx") el.remove();                       // o contexto já foi trocado
    else zoneEl("extra").appendChild(el);
    el.querySelector(".ct-foco")?.classList.remove("on");
    focoId = null; focoEl = null; focoAuto = false; focoFimDaFala = false;
    $("foco").hidden = true;
    $("center").classList.remove("foco-on");
    window.dispatchEvent(new Event("resize"));
  }
  // Chamado quando chega a resposta: o card trazido pelo JARVIS volta quando ele terminar de falar
  function focoAteFimDaFala() {
    if (!focoAuto) return;
    if ($("voiceToggle").checked) focoFimDaFala = true;
    else { clearTimeout(focoTimer); focoTimer = setTimeout(desfocarCard, 25000); }
  }
  $("focoFechar").addEventListener("click", desfocarCard);

  // ------------------------------------------------------------------ apps da web (WhatsApp, Instagram, Facebook)
  // O app roda numa janela de navegador do JARVIS, encaixada em cima do corpo deste painel. A página só diz onde o
  // painel está (e se está visível: um modal ou menu aberto por cima esconde o app).
  let appwAtual = null, appwUltimo = "";
  const APPW_NOME = { whatsapp: "WhatsApp", instagram: "Instagram", facebook: "Facebook", youtube: "YouTube", navegador: "Navegador" };
  function abrirAppWeb(app) {
    app = APPW_NOME[app] ? app : (appwAtual || pref("jarvis.appw", "whatsapp"));
    appwAtual = app;
    try { localStorage.setItem("jarvis.appw", app); } catch (e) { /* sem storage */ }
    desfocarCard(); fecharWeb();
    if (lyricsOpen) closeLyrics();
    showPage("command");
    $("appw").hidden = false;
    $("appw").classList.toggle("web-cheio", pref("jarvis.appwCheio", "1") === "1");   // apps pedem espaço: abre ampliado
    $("center").classList.add("appw-on");
    document.querySelectorAll("[data-appw]").forEach((b) => b.classList.toggle("on", b.dataset.appw === app));
    $("appwNav").hidden = app !== "navegador";
    $("appwMsg").textContent = `Abrindo o ${APPW_NOME[app]}… Na primeira vez, faça o login aqui mesmo (fica salvo).`;
    appwUltimo = "";
    relatarAppWeb();
  }
  function fecharAppWeb() {
    if ($("appw").hidden) return;
    $("appw").hidden = true;
    $("appw").classList.remove("web-cheio");
    $("center").classList.remove("appw-on");
    appwAtual = null; appwUltimo = "";
    relatarAppWeb();
  }
  const retangulo = (r) => [r.left, r.top, r.width, r.height];
  function relatarAppWeb() {
    const slots = {};
    if (appwAtual && !$("appw").hidden) slots[appwAtual] = { rect: retangulo($("appwBody").getBoundingClientRect()) };
    // Cards de apps nas colunas (só a parte visível: a coluna pode estar rolada). Um painel ampliado cobre as colunas.
    const cobreColunas = (!$("appw").hidden && $("appw").classList.contains("web-cheio")) || (!$("web").hidden && $("web").classList.contains("web-cheio"));
    if (!cobreColunas) {
      for (const c of appCards) {
        if (c.tipo !== "app" || slots[c.app]) continue;
        const corpo = cardEl(c.id)?.querySelector(".ac-corpo");
        if (!corpo || !corpo.offsetParent) continue;
        const r = corpo.getBoundingClientRect(), z = corpo.closest(".zone, #focoBody").getBoundingClientRect();
        const x1 = Math.max(r.left, z.left), y1 = Math.max(r.top, z.top), x2 = Math.min(r.right, z.right), y2 = Math.min(r.bottom, z.bottom);
        if (x2 - x1 < 30 || y2 - y1 < 30) continue;
        slots[c.app] = { rect: retangulo(r), clip: [x1, y1, x2 - x1, y2 - y1] };
      }
    }
    const coberto = !!document.querySelector(".modal:not([hidden])") || !$("cardsMenu").hidden || !$("bellMenu").hidden
      || $("page-command").hidden || !!$("boot") || arrastandoCard;
    const json = JSON.stringify({ slots, dpr: devicePixelRatio, vista: [innerWidth, innerHeight], visivel: !coberto });
    if (json === appwUltimo) return;
    appwUltimo = json;
    fetch("/api/apps_web/encaixes", { method: "POST", headers: { "content-type": "application/json" }, body: json }).catch(() => { appwUltimo = ""; });
  }
  document.querySelectorAll(".zone").forEach((z) => z.addEventListener("scroll", relatarAppWeb, { passive: true }));

  // ------------------------------------------------------------------ cards de apps e de leitura (ao vivo, nas colunas)
  // Ex.: o WhatsApp num card para ir respondendo e o Instagram em outro vendo reels, com o resto da tela livre.
  // São cards como os outros: arrastam pelo título entre as colunas, ⤢ foco, 📌 fixar, ✕ fechar; a altura muda
  // puxando a borda de baixo. Os FIXADOS também aparecem na janelinha flutuante quando o JARVIS é minimizado.
  let appCards = [];
  try { appCards = JSON.parse(pref("jarvis.appCards", "[]")) || []; } catch (e) { appCards = []; }
  appCards.forEach((c) => { if (!String(c.id).startsWith("app-")) c.id = "app-" + c.id; });   // formato antigo
  let arrastandoCard = false;
  function salvarAppCards() {
    try { localStorage.setItem("jarvis.appCards", JSON.stringify(appCards)); } catch (e) { /* sem storage */ }
    appwUltimo = ""; relatarAppWeb(); avisarFlutuante();
  }
  const ehAppCard = (id) => String(id).startsWith("app-");
  function montarAppCard(c) {
    const el = document.createElement("div");
    el.className = "card card-app"; el.dataset.card = c.id;
    const nome = c.tipo === "leitor" ? "LEITURA" : APPW_NOME[c.app].toUpperCase();
    el.innerHTML = `<h3><span>${esc(nome)}</span> <small class="muted ac-sub"></small></h3>
      <div class="ac-corpo" style="height:${c.altura}px">${c.tipo === "leitor" ? '<div class="ac-leitor"><p class="empty">Carregando…</p></div>'
        : `<p class="empty">Abrindo o ${APPW_NOME[c.app]}… Na primeira vez, faça o login aqui mesmo.</p>`}</div>
      <div class="ac-borda" title="Arraste para mudar a altura"></div>`;
    // Onde estava (layout salvo) ou no topo da coluna da direita
    const zonaSalva = layout && Object.keys(layout.zonas).find((z) => layout.zonas[z].includes(c.id));
    const zona = zoneEl(zonaSalva || "direita");
    const ctx = zona.querySelector(":scope > .ctx-area");
    if (ctx) ctx.after(el); else zona.prepend(el);
    if (layout) {
      el.classList.toggle("pinned", layout.fixados.includes(c.id));
      const ordem = layout.zonas[zonaSalva] || [];            // respeita a posição salva dentro da coluna
      const depois = ordem.slice(ordem.indexOf(c.id) + 1).map(cardEl).find((x) => x && x.parentElement === zona);
      if (zonaSalva && depois) zona.insertBefore(el, depois);
    }
    decorateCards();
    redimensionavel(el, c);
    if (c.tipo === "leitor") carregarLeitorCard(el, c);
    return el;
  }
  // Borda de baixo: puxar muda a altura (o app encaixado acompanha)
  function redimensionavel(el, c) {
    const borda = el.querySelector(".ac-borda"), corpo = el.querySelector(".ac-corpo");
    borda.addEventListener("pointerdown", (e) => {
      e.preventDefault();
      const y0 = e.clientY, h0 = corpo.offsetHeight;
      borda.setPointerCapture(e.pointerId);
      const mover = (ev) => { corpo.style.height = `${Math.max(160, Math.min(1200, h0 + ev.clientY - y0))}px`; relatarAppWeb(); };
      const fim = () => {
        borda.removeEventListener("pointermove", mover); borda.removeEventListener("pointerup", fim);
        c.altura = corpo.offsetHeight; salvarAppCards();
      };
      borda.addEventListener("pointermove", mover); borda.addEventListener("pointerup", fim);
    });
  }
  async function carregarLeitorCard(el, c) {
    let d;
    try { d = await (await fetch("/api/web/ler?url=" + encodeURIComponent(c.url))).json(); } catch (e) { d = { ok: false, mensagem: "Sem conexão." }; }
    const alvo = el.querySelector(".ac-leitor");
    if (!d.ok) { alvo.innerHTML = `<p class="ctx-warn" style="padding:10px">${esc(d.mensagem)}</p>`; return; }
    el.querySelector(".ac-sub").textContent = `· ${d.site || ""}`;
    alvo.innerHTML = `<article class="leitor"><h1>${esc(d.titulo)}</h1>${d.imagem ? `<img class="hero" src="${esc(d.imagem)}" alt="" referrerpolicy="no-referrer">` : ""}
      ${d.blocos.map(blocoLeitor).join("")}<p class="ctx-muted fonte">${esc(d.url)}</p></article>`;
  }
  function novoAppCard(dados) {
    const ja = dados.tipo === "app" && appCards.find((c) => c.tipo === "app" && c.app === dados.app);
    if (ja) { const el = cardEl(ja.id); if (el) { el.classList.remove("flash-card"); void el.offsetWidth; el.classList.add("flash-card"); el.scrollIntoView({ block: "nearest" }); } return; }
    const c = { id: "app-" + Date.now(), altura: dados.tipo === "leitor" ? 380 : 460, ...dados };
    appCards.unshift(c);
    montarAppCard(c).scrollIntoView({ block: "nearest" });
    showPage("command");
    salvarAppCards();
    layout = readLayout(); saveLayout();
  }
  function fecharAppCard(id) {
    if (focoId === id) desfocarCard();
    appCards = appCards.filter((c) => c.id !== id);
    cardEl(id)?.remove();
    if (layout) { layout.fixados = layout.fixados.filter((x) => x !== id); layout = readLayout(); saveLayout(); }
    salvarAppCards();
  }
  $("appwCard").addEventListener("click", () => { const app = appwAtual; fecharAppWeb(); if (app) novoAppCard({ tipo: "app", app }); });
  $("webCard").addEventListener("click", () => { const url = webUrl; fecharWeb(); if (url) novoAppCard({ tipo: "leitor", url }); });
  setInterval(relatarAppWeb, 400);
  setInterval(() => avisarFlutuante(), 10000);
  addEventListener("resize", relatarAppWeb);
  document.querySelectorAll("[data-appw]").forEach((b) => b.addEventListener("click", () => abrirAppWeb(b.dataset.appw)));
  $("appwFechar").addEventListener("click", fecharAppWeb);
  // Navegador: voltar / avançar / recarregar e a barra de endereço (ou pesquisa no Google)
  const navegar = (acao, url = "") => fetch("/api/apps_web/navegar", { method: "POST", headers: { "content-type": "application/json" },
    body: JSON.stringify({ app: "navegador", acao, url }) }).then((r) => r.json()).then((d) => { if (!d.ok && d.mensagem) toast(d.mensagem, "warn"); }).catch(() => {});
  document.querySelectorAll("[data-nav]").forEach((b) => b.addEventListener("click", () => navegar(b.dataset.nav)));
  $("appwNav").addEventListener("submit", (e) => { e.preventDefault(); navegar("ir", $("appwEndereco").value); $("appwEndereco").blur(); });
  $("appwAmpliar").addEventListener("click", () => {
    const cheio = $("appw").classList.toggle("web-cheio");
    try { localStorage.setItem("jarvis.appwCheio", cheio ? "1" : "0"); } catch (e) { /* sem storage */ }
    relatarAppWeb();
  });
  fetch("/api/apps_web").then((r) => r.json()).then((s) => { $("temaApps").checked = s.tema !== false; }).catch(() => {});
  $("temaApps").addEventListener("change", (e) => {
    fetch("/api/apps_web/tema", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ ativo: e.target.checked }) })
      .then(() => toast(e.target.checked ? "Visual do JARVIS nos apps ligado." : "Visual do JARVIS nos apps desligado.", "ok")).catch(() => {});
  });
  $("avisosVoz").checked = pref("jarvis.avisosVoz", "1") === "1";
  $("avisosVoz").addEventListener("change", (e) => {
    try { localStorage.setItem("jarvis.avisosVoz", e.target.checked ? "1" : "0"); } catch (err) { /* sem storage */ }
  });
  $("appsBtn").addEventListener("click", () => ($("appw").hidden ? abrirAppWeb() : fecharAppWeb()));

  // Mensagem/notificação nova (WhatsApp, Instagram, Facebook): aviso na tela, no log, na janelinha e por voz
  function avisoApp(texto, detalhe) {
    toast(detalhe ? `${texto} ${detalhe}` : texto, "ok");
    addLog("AVISO", detalhe ? `${texto} — ${detalhe}` : texto, "sys");
    avisarFlutuante({ resposta: detalhe ? `${texto}\n${detalhe}` : texto });
    if ($("voiceToggle").checked && pref("jarvis.avisosVoz", "1") === "1" && !isSpeaking && core.state !== "thinking"
        && !awaitingCommand) speak(texto);
    atualizarContagensApps();
    ["whatsapp", "instagram", "facebook"].forEach((id) => { if (cardVisible(id)) refreshCard(id); });
  }
  async function atualizarContagensApps() {
    try {
      const s = await (await fetch("/api/apps_web")).json();
      let total = 0;
      for (const [app, info] of Object.entries(s.apps || {})) {
        total += info.nao_lidas || 0;
        const i = document.querySelector(`[data-cont="${app}"]`);
        if (i) i.textContent = info.nao_lidas ? `(${info.nao_lidas})` : "";
      }
      $("appsCount").hidden = !total; $("appsCount").textContent = total;
    } catch (e) { /* servidor fora */ }
  }
  setInterval(atualizarContagensApps, 15000);

  // ------------------------------------------------------------------ navegador interno
  // Notícias e links abrem aqui dentro, no centro: LEITURA (o texto da página no visual do JARVIS) ou
  // PÁGINA (o site original, sem scripts). ↗ abre no seu navegador; ⤢ amplia para a tela toda.
  let webUrl = "", webHist = [], webModo = pref("jarvis.webModo", "leitura");
  function abrirWeb(url, historico = true) {
    if (!url) return;
    fecharAppWeb();
    if (historico && webUrl && webUrl !== url) webHist.push(webUrl);
    webUrl = url;
    desfocarCard();
    if (lyricsOpen) closeLyrics();
    showPage("command");
    $("web").hidden = false;
    $("center").classList.add("web-on");
    carregarWeb();
  }
  function fecharWeb() {
    if ($("web").hidden) return;
    $("web").hidden = true;
    $("web").classList.remove("web-cheio");
    $("center").classList.remove("web-on");
    $("webBody").innerHTML = "";
    webUrl = ""; webHist = [];
  }
  const blocoLeitor = (b) => b.tipo === "img" ? `<img src="${esc(b.src)}" alt="${esc(b.alt)}" loading="lazy" referrerpolicy="no-referrer">`
    : b.tipo === "h" ? `<h2>${esc(b.texto)}</h2>` : b.tipo === "citacao" ? `<blockquote>${esc(b.texto)}</blockquote>`
    : b.tipo === "item" ? `<p class="item">${esc(b.texto)}</p>` : b.tipo === "legenda" ? `<p class="legenda">${esc(b.texto)}</p>`
    : `<p>${esc(b.texto)}</p>`;
  async function carregarWeb() {
    document.querySelectorAll("#web [data-modo]").forEach((b) => b.classList.toggle("on", b.dataset.modo === webModo));
    $("webVoltar").disabled = !webHist.length;
    const body = $("webBody");
    if (webModo === "pagina") {
      body.innerHTML = `<iframe class="web-frame" sandbox="allow-popups" referrerpolicy="no-referrer" src="/web/pagina?url=${encodeURIComponent(webUrl)}"></iframe>`;
      return;
    }
    body.innerHTML = '<p class="ly-msg web-msg">Carregando a página…</p>';
    $("webTitulo").textContent = "NAVEGADOR"; $("webSite").textContent = "";
    const pedido = webUrl;
    let d;
    try { d = await (await fetch("/api/web/ler?url=" + encodeURIComponent(pedido))).json(); }
    catch (e) { d = { ok: false, mensagem: "Sem conexão com o JARVIS." }; }
    if (pedido !== webUrl || webModo !== "leitura") return;       // trocou de página ou de modo no meio
    if (!d.ok) { body.innerHTML = `<p class="ctx-warn web-msg">${esc(d.mensagem)}</p>`; return; }
    webUrl = d.url || webUrl;                                     // endereço real (links do Google Notícias)
    $("webTitulo").textContent = (d.site || "NAVEGADOR").toUpperCase();
    $("webSite").textContent = d.data || "";
    body.innerHTML = `<article class="leitor"><h1>${esc(d.titulo)}</h1>
      ${d.descricao ? `<p class="lead">${esc(d.descricao)}</p>` : ""}
      ${d.imagem ? `<img class="hero" src="${esc(d.imagem)}" alt="" referrerpolicy="no-referrer">` : ""}
      ${d.blocos.map(blocoLeitor).join("")}
      ${d.pouco_texto ? '<p class="ctx-warn">Esta página quase não tem texto sem o navegador completo: tente o modo PÁGINA ou ↗.</p>' : ""}
      <p class="ctx-muted fonte">${esc(webUrl)}</p></article>`;
    body.scrollTop = 0;
  }
  document.querySelectorAll("#web [data-modo]").forEach((b) => b.addEventListener("click", () => {
    webModo = b.dataset.modo;
    try { localStorage.setItem("jarvis.webModo", webModo); } catch (e) { /* sem storage */ }
    carregarWeb();
  }));
  $("webVoltar").addEventListener("click", () => { if (webHist.length) { webUrl = webHist.pop(); carregarWeb(); } });
  $("webFechar").addEventListener("click", fecharWeb);
  $("webAmpliar").addEventListener("click", () => $("web").classList.toggle("web-cheio"));
  $("webFora").addEventListener("click", () => fetch("/api/web/externo", { method: "POST", headers: { "content-type": "application/json" },
    body: JSON.stringify({ url: webUrl }) }).then(() => toast("Abrindo no seu navegador…", "ok")).catch(() => {}));
  // Links sem a "bolha" com o endereço que o navegador mostra no canto de baixo: o endereço sai do link quando o
  // mouse chega nele, e o clique abre do mesmo jeito (notícias e cards: aqui dentro; Ctrl+clique: no navegador normal)
  document.addEventListener("pointerover", (e) => {
    const a = e.target.closest && e.target.closest("a[href]");
    if (a && /^https?:/i.test(a.href)) { a.dataset.href = a.href; a.removeAttribute("href"); a.classList.add("link-js"); }
  }, true);
  document.addEventListener("click", (e) => {
    const a = e.target.closest("a[data-href]");
    if (!a) return;
    e.preventDefault();
    const url = a.dataset.href;
    if (!e.ctrlKey && !e.metaKey && !e.shiftKey && !a.hasAttribute("data-fora") && a.closest(".zone, #focoBody, #webBody")) { abrirWeb(url); return; }
    window.open(url, "_blank", "noopener");
  });

  async function loadLayout() {
    try {
      const d = await (await fetch("/api/cards")).json();
      catalogo = d.catalogo; applyLayout(d.layout);
      appCards.forEach((c) => { if (!cardEl(c.id)) montarAppCard(c); });
      applyLayout(layout);
      avisarFlutuante();
    } catch (e) { /* servidor ainda subindo: fica a ordem do HTML */ }
  }

  // ------------------------------------------------------------------ abas (Command Center, AI Core, AI Voice, Integrações)
  function showPage(page) {
    document.querySelectorAll(".tab[data-page]").forEach((t) => t.classList.toggle("on", t.dataset.page === page));
    ["command", "core", "voice", "apps"].forEach((p) => { $("page-" + p).hidden = p !== page; });
    if (page === "core") renderCorePage();
    if (page === "voice") renderVoicePage();
    if (page === "apps") loadApps();
    if (page === "command") { sparkCpu.draw(); sparkRam.draw(); sparkNet.draw(); }
  }
  document.querySelectorAll(".tab[data-page]").forEach((t) => t.addEventListener("click", () => showPage(t.dataset.page)));

  // ------------------------------------------------------------------ envio de pedidos pelos botões
  function ask(text) { showPage("command"); send(text); }
  document.addEventListener("click", (e) => {
    const b = e.target.closest("[data-pedido]");
    if (b && !e.target.closest(".dropdown [data-pin]")) { toggleDropdown(null); ask(b.dataset.pedido); }
  });
  $("quick").addEventListener("click", (e) => {
    const b = e.target.closest("[data-acao]");
    if (!b) return;
    if (b.dataset.acao === "ouvir") $("micBtn").click();
    if (b.dataset.acao === "parar") {
      stopSpeaking(); endFollowUp(); setCoreState("idle");
      toast("Fala e escuta interrompidas.", "ok");
    }
  });

  // ------------------------------------------------------------------ cards de dados (agenda, alertas, e-mail, GitHub, Docker...)
  const wxIconOf = (c) => (c === 0 ? "☀️" : c <= 2 ? "🌤️" : c === 3 ? "☁️" : c <= 48 ? "🌫️" : c <= 67 ? "🌧️" : c <= 77 ? "❄️" : c <= 82 ? "🌦️" : "⛈️");
  const conectar = (texto = "Conectar em Integrações") => `<p class="empty"><button type="button" class="linkish" data-ir="apps">${texto}</button></p>`;
  document.addEventListener("click", (e) => { const b = e.target.closest("[data-ir]"); if (b) showPage(b.dataset.ir); });
  const getJSON = async (url) => (await fetch(url)).json();

  function renderAgenda(d) {
    $("agendaSub").textContent = d.ok ? `· ${d.eventos.length}` : "";
    if (!d.conectado) { $("agendaBody").innerHTML = '<p class="empty">Google Agenda não conectada.</p>' + conectar(); return; }
    if (!d.ok) { $("agendaBody").innerHTML = `<p class="empty">${esc(d.mensagem)}</p>`; return; }
    if (!d.eventos.length) { $("agendaBody").innerHTML = '<p class="empty">Nada marcado nos próximos dias.</p>'; return; }
    const agora = new Date(), hoje = agora.toDateString();
    let dia = "", html = "";
    for (const ev of d.eventos.slice(0, 8)) {
      const ini = new Date(ev.inicio.length <= 10 ? ev.inicio + "T00:00:00" : ev.inicio);
      const rotulo = ini.toDateString() === hoje ? "HOJE" : ini.toLocaleDateString("pt-BR", { weekday: "short", day: "2-digit", month: "2-digit" }).toUpperCase();
      if (rotulo !== dia) { html += `${dia ? "</ul>" : ""}<div class="ag-day">${rotulo}</div><ul class="rows">`; dia = rotulo; }
      const fim = ev.fim ? new Date(ev.fim) : null;
      const atual = !ev.dia_inteiro && ini <= agora && fim && fim > agora;
      html += `<li class="${atual ? "atual" : ""}"><span class="ag-time">${ev.dia_inteiro ? "DIA" : ini.toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" })}</span>
        <div class="main"><b>${esc(ev.titulo)}</b>${ev.local ? `<small>${esc(ev.local)}</small>` : ""}</div></li>`;
    }
    $("agendaBody").innerHTML = html + "</ul>";
  }

  function renderAlerts(d) {
    const al = d.alertas || [];
    $("alertsSub").textContent = al.length ? `· ${al.length}` : "";
    $("bellCount").hidden = !al.length; $("bellCount").textContent = al.length;
    const lista = al.length ? `<ul class="rows">${al.map((a) => `<li class="${a.nivel}${a.pedido ? " click" : ""}" ${a.pedido ? `data-pedido="${esc(a.pedido)}"` : ""}>
        <span class="dot ${a.nivel === "info" ? "on" : a.nivel}"></span><div class="main"><b>${esc(a.titulo)}</b><small>${esc(a.detalhe || "")}</small></div></li>`).join("")}</ul>`
      : '<p class="empty">Nenhum alerta. Tudo em ordem.</p>';
    $("alertsBody").innerHTML = lista;
    $("bellMenu").innerHTML = `<h4>ALERTAS</h4>${lista}`;
  }

  function renderEmail(d) {
    $("emailSub").textContent = d.ok ? `· ${d.nao_lidos} não lido(s)` : "";
    if (!d.conectado) { $("emailBody").innerHTML = '<p class="empty">E-mail não conectado.</p>' + conectar(); return; }
    if (!d.ok) { $("emailBody").innerHTML = `<p class="empty">${esc(d.mensagem)}</p>`; return; }
    $("emailBody").innerHTML = d.mensagens.length ? `<ul class="rows">${d.mensagens.map((m) =>
      `<li><div class="main"><b>${esc(m.assunto)}</b><small>${esc(m.de)}</small></div><span class="side">${esc((m.quando || "").split(" ")[1] || m.quando)}</span></li>`).join("")}</ul>`
      : '<p class="empty">Caixa de entrada em dia.</p>';
  }

  function renderGithub(d) {
    $("githubSub").textContent = d.ok ? `· ${d.usuario}` : "";
    if (!d.conectado) { $("githubBody").innerHTML = '<p class="empty">GitHub não conectado.</p>' + conectar(); return; }
    if (!d.ok) { $("githubBody").innerHTML = `<p class="empty">${esc(d.mensagem)}</p>`; return; }
    const prs = [...d.revisar.map((p) => ({ ...p, tipo: "REVISAR" })), ...d.meus_prs.map((p) => ({ ...p, tipo: "MEU" }))].slice(0, 5);
    $("githubBody").innerHTML = `<div class="counters"><div><b>${d.notificacoes}</b><small>NOTIFICAÇÕES</small></div>
      <div><b>${d.total_meus_prs}</b><small>MEUS PRs</small></div><div><b>${d.total_revisar}</b><small>REVISAR</small></div></div>
      <ul class="rows">${prs.map((p) => `<li><div class="main"><b><a href="${esc(p.url)}" target="_blank" rel="noopener" style="color:inherit;text-decoration:none">${esc(p.titulo)}</a></b>
      <small>${esc(p.repo)} #${p.numero}</small></div><span class="tag ${p.tipo === "REVISAR" ? "warn" : ""}">${p.tipo}</span></li>`).join("") || ""}</ul>`;
  }

  function renderDocker(d) {
    $("dockerSub").textContent = d.ok ? `· ${d.rodando}/${d.containers.length} rodando` : "";
    if (!d.ok) { $("dockerBody").innerHTML = `<p class="empty">${esc(d.mensagem)}</p>`; return; }
    $("dockerBody").innerHTML = d.containers.length ? `<ul class="rows">${d.containers.map((c) => {
      const on = c.estado === "running";
      return `<li><span class="dot ${on ? "on" : ""}"></span><div class="main"><b>${esc(c.nome)}</b><small>${esc(c.imagem)} · ${esc(c.status)}</small></div>
        <button type="button" class="ct" title="${on ? "Parar" : "Iniciar"}" data-pedido="${on ? "pare" : "inicie"} o container ${esc(c.nome)}">${on ? "■" : "▶"}</button></li>`;
    }).join("")}</ul>` : '<p class="empty">Nenhum container.</p>';
  }

  function renderServices(d) {
    const sv = d.servicos || [];
    $("svcSub").textContent = `· ${sv.filter((s) => s.conectado).length} online`;
    $("servicesBody").innerHTML = `<ul class="rows">${sv.map((s) => `<li class="click" data-ir="apps"><div class="main"><b>${esc(s.nome)}</b>
      <small>${esc(s.categoria)}</small></div><span class="side" style="color:${s.conectado ? "var(--green)" : "var(--muted)"}">${s.conectado ? "ONLINE" : "—"}</span>
      <span class="dot ${s.conectado ? "on" : ""}"></span></li>`).join("")}</ul>`;
  }

  function renderLlmCard() {
    const lista = brains.filter((b) => b.id !== "local");
    const prontos = lista.filter((b) => b.configured);
    $("llmSub").textContent = `· ${prontos.length} prontos`;
    $("dgProv").textContent = `${prontos.length} prontos`;
    $("llmBody").innerHTML = `<ul class="rows">${lista.map((b) => `<li class="click ${b.id === currentBrain ? "atual" : ""}" data-brain="${b.id}" title="${b.configured ? "Usar este cérebro" : "Configurar"}">
      <div class="main"><b>${esc(b.label)}</b><small>${esc(b.model || "")}</small></div>
      <span class="side" style="color:${b.configured ? "var(--green)" : "var(--muted)"}">${b.id === currentBrain ? "EM USO" : b.configured ? "PRONTO" : "CONFIGURAR"}</span>
      <span class="dot ${b.configured ? "on" : ""}"></span></li>`).join("")}</ul>`;
  }
  document.addEventListener("click", (e) => {
    const li = e.target.closest("[data-brain]");
    if (!li) return;
    const b = brains.find((x) => x.id === li.dataset.brain);
    if (!b) return;
    if (!b.configured) { b.tipo === "local" ? toast("Instale e abra o Ollama (ollama.com) para usar modelos locais.", "warn") : openSettings(b.id); return; }
    ws && ws.send(JSON.stringify({ type: "set_brain", brain: b.id }));
  });

  // ------------------------------------------------------------------ cards do WhatsApp, Instagram e Facebook
  // (dos apps abertos dentro do JARVIS, no painel APPS)
  const abrirNoPainel = (app) => `<div class="rede-bts"><button type="button" class="ghost mini" data-rede-ir="${app}">ABRIR AQUI</button></div>`;
  function renderWhatsapp(d) {
    const conv = d.conversas || [];
    const total = conv.reduce((s, c) => s + (c.nao_lidas || 0), 0);
    $("waSub").textContent = d.aberto && d.estado === "pronto" ? `· ${total} não lida(s)` : "";
    if (!d.aberto) { $("waBody").innerHTML = '<p class="empty">Abra o WhatsApp aqui dentro do JARVIS para ver e responder por aqui.</p>' + abrirNoPainel("whatsapp"); return; }
    if (d.estado === "login") { $("waBody").innerHTML = '<p class="empty">Escaneie o QR code no painel APPS para conectar.</p>' + abrirNoPainel("whatsapp"); return; }
    if (d.estado === "carregando") { $("waBody").innerHTML = '<p class="empty">Carregando as conversas…</p>'; return; }
    $("waBody").innerHTML = conv.length ? `<ul class="rows">${conv.map((c) =>
      `<li class="wa-conv" data-wa-conversa="${esc(c.conversa)}"><div class="main"><b>${esc(c.conversa)}</b><small>${esc(c.previa || "")}</small></div>
       <span class="side"><span class="tag">${esc(c.nao_lidas)}</span> ${esc(c.quando || "")}</span></li>`).join("")}</ul>` + abrirNoPainel("whatsapp")
      : '<p class="empty">Nenhuma mensagem não lida.</p>' + abrirNoPainel("whatsapp");
  }
  function renderRede(app, sub, body, botoes) {
    return (d) => {
      $(sub).textContent = d.aberto && d.nao_lidas ? `· ${d.nao_lidas} nova(s)` : "";
      if (!d.aberto) { $(body).innerHTML = `<p class="empty">Abra o ${d.nome} aqui dentro do JARVIS para receber os avisos.</p>` + abrirNoPainel(app); return; }
      if (d.logado === false) { $(body).innerHTML = `<p class="empty">Entre na sua conta no painel APPS.</p>` + abrirNoPainel(app); return; }
      $(body).innerHTML = `<div class="rede-num"><b class="${d.nao_lidas ? "" : "zero"}">${esc(d.nao_lidas || 0)}</b><small>NOVIDADES (MENSAGENS E NOTIFICAÇÕES)</small></div>
        <div class="rede-bts">${botoes.map(([rot, url]) => `<button type="button" class="ghost mini" data-rede-ir="${app}" data-url="${url}">${rot}</button>`).join("")}</div>`;
    };
  }
  const renderInstagram = renderRede("instagram", "igSub", "igBody", [["ABRIR", ""], ["DIRECT", "https://www.instagram.com/direct/inbox/"],
    ["NOTIFICAÇÕES", "https://www.instagram.com/notifications/"]]);
  const renderFacebook = renderRede("facebook", "fbSub", "fbBody", [["ABRIR", ""], ["MESSENGER", "https://www.facebook.com/messages/"],
    ["NOTIFICAÇÕES", "https://www.facebook.com/notifications/"]]);
  document.addEventListener("click", async (e) => {
    const ir = e.target.closest("[data-rede-ir]");
    if (ir) {
      abrirAppWeb(ir.dataset.redeIr);
      if (ir.dataset.url) fetch("/api/apps_web/ir", { method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify({ app: ir.dataset.redeIr, url: ir.dataset.url }) }).catch(() => {});
      return;
    }
    const conv = e.target.closest("[data-wa-conversa]");
    if (conv) {
      abrirAppWeb("whatsapp");
      try {
        const r = await (await fetch("/api/apps_web/conversa", { method: "POST", headers: { "content-type": "application/json" },
          body: JSON.stringify({ titulo: conv.dataset.waConversa }) })).json();
        if (!r.ok) toast("Não consegui abrir essa conversa; ela está no painel do WhatsApp.", "warn");
        setTimeout(() => refreshCard("whatsapp"), 1500);
      } catch (err) { /* JARVIS fora */ }
    }
  });

  const PAINEL_RENDER = { agenda: renderAgenda, email: renderEmail, github: renderGithub, docker: renderDocker,
    alertas: renderAlerts, servicos: renderServices, whatsapp: renderWhatsapp, instagram: renderInstagram, facebook: renderFacebook };

  async function refreshCard(id) {
    if (!PAINEL_RENDER[id]) return;
    try { PAINEL_RENDER[id](await getJSON("/api/painel/" + id)); } catch (e) { /* tenta no próximo ciclo */ }
  }
  // Atualiza só o que está visível (e os alertas sempre: alimentam o sino)
  const PERIODO = { alertas: 60, agenda: 300, email: 120, github: 180, docker: 30, servicos: 300, whatsapp: 20, instagram: 45, facebook: 45 };
  const ultimoRefresh = {};
  function pollPanels(force = false) {
    const agora = Date.now() / 1000;
    for (const [id, seg] of Object.entries(PERIODO)) {
      if (!force && agora - (ultimoRefresh[id] || 0) < seg) continue;
      if (id !== "alertas" && !cardVisible(id) && !force) continue;
      ultimoRefresh[id] = agora;
      refreshCard(id);
    }
  }
  setInterval(pollPanels, 10000);

  async function loadWeatherMini() {
    try {
      const d = await getJSON("/api/painel/clima");
      if (!d.agora) return;
      $("bbWx").textContent = `${Math.round(d.agora.temperatura_c)}°C ${d.agora.condicao}`;
      $("bbWxIcon").textContent = wxIconOf(d.agora.codigo);
      $("bbWeather").title = d.local || "Previsão do tempo";
    } catch (e) { /* sem internet */ }
  }
  setInterval(loadWeatherMini, 30 * 60 * 1000);
  $("bbWeather").addEventListener("click", () => ask("qual a previsão do tempo?"));

  // ------------------------------------------------------------------ onda da barra "falar com o JARVIS"
  const wave = $("talkWave");
  wave.innerHTML = "<i></i>".repeat(48);
  const waveBars = [...wave.children];
  setInterval(() => {
    const st = core.state;
    // Só aparece na conversa por voz (ele ouvindo você ou falando); some se você for digitar
    const voz = (st === "listening" && (recActive || awaitingCommand)) || st === "speaking";
    const digitando = document.activeElement === $("chatInput") || $("chatInput").value.trim() !== "";
    $("chatForm").classList.toggle("voz", voz && !digitando);
    if (!voz || digitando) return;
    waveBars.forEach((b, i) => {
      const centro = 1 - Math.abs(i - waveBars.length / 2) / (waveBars.length / 2);
      b.style.height = `${(4 + Math.random() * 22 * (0.3 + centro)).toFixed(1)}px`;
    });
    wave.style.opacity = ".75";
  }, 120);

  // ------------------------------------------------------------------ página AI CORE
  const TILE = { claude: "✳", openai: "AI", gemini: "✦", groq: "⚡", openrouter: "OR", ollama: "🦙", "claude-conta": "✳",
    "openai-conta": "AI", "gemini-conta": "✦", local: "⌁" };
  const DESC = {
    claude: "Raciocínio e escrita de primeira. Pago por uso.", openai: "GPT pela API. Confiável e versátil.",
    gemini: "Rápido, com plano grátis generoso.", groq: "Modelos abertos ultrarrápidos, com plano grátis.",
    openrouter: "Mais de 200 modelos com uma chave só.", ollama: "Modelos rodando neste PC. Nada sai da máquina.",
    "claude-conta": "Usa a sua assinatura Claude pela CLI oficial.", "openai-conta": "Usa a sua assinatura ChatGPT (Codex CLI).",
    "gemini-conta": "Login pessoal do Google (o Google encerrou para contas pessoais).", local: "Regras offline: comandos diretos, sem IA.",
  };
  function renderCorePage() {
    const atual = brains.find((b) => b.id === currentBrain) || {};
    $("coreTitle").textContent = atual.label || "—";
    $("coreModel").textContent = atual.model || "—";
    $("coreCount").textContent = `${brains.filter((b) => b.configured && b.id !== "local").length} prontos`;
    $("coreGrid").innerHTML = brains.map((b) => {
      const tipo = b.tipo === "conta" ? "CONTA" : b.tipo === "local" ? "LOCAL" : b.id === "groq" ? "GRÁTIS" : "API";
      const emUso = b.id === currentBrain;
      return `<div class="prov ${emUso ? "on" : ""}"><div class="prov-head"><span class="logo-tile">${TILE[b.id] || "◆"}</span>
        <div><b>${esc(b.label)}</b><small>${tipo === "CONTA" ? "Sua assinatura" : tipo === "LOCAL" ? "Neste PC" : "Chave de API"}</small></div>
        <span class="tag ${tipo === "GRÁTIS" || tipo === "LOCAL" ? "ok" : tipo === "CONTA" ? "warn" : ""}">${tipo}</span></div>
        <p>${esc(DESC[b.id] || "")}</p>
        <div class="prov-model">MODELO ${b.id === "ollama" && b.configured ? `<select data-ollama-model></select>` : `<code>${esc(b.model || "—")}</code>`}</div>
        <div class="status-line ${b.configured ? "" : "off"}">${emUso ? "✓ Em uso agora" : b.configured ? "● Pronto" : "○ Não configurado"}</div>
        <div class="prov-actions">${b.configured && !emUso ? `<button type="button" class="send" data-brain="${b.id}">ATIVAR</button>` : ""}
          ${b.tipo === "api" || b.tipo === "conta" ? `<button type="button" class="ghost" data-config-brain="${b.id}">CONFIGURAR</button>` : ""}
          ${b.id === "ollama" && !b.configured ? '<a class="ghost btn-link" href="https://ollama.com/download" target="_blank" rel="noopener">BAIXAR OLLAMA ↗</a>' : ""}</div></div>`;
    }).join("");
    const sel = document.querySelector("[data-ollama-model]");
    if (sel) {
      fetch("/api/models", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ brain: "ollama", key: "" }) })
        .then((r) => r.json()).then((d) => {
          sel.innerHTML = (d.models || []).map((m) => `<option value="${esc(m.id)}">${esc(m.label)}</option>`).join("");
          sel.value = d.atual || (d.models[0] || {}).id || "";
        }).catch(() => {});
      sel.addEventListener("change", async () => {
        const r = await fetch("/api/brain-key", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ brain: "ollama", key: "", model: sel.value }) });
        const d = await r.json();
        if (d.brains) renderBrains(d.brains);
        toast(d.mensagem || d.detail || "Modelo trocado.", r.ok ? "ok" : "warn");
      });
    }
  }
  document.addEventListener("click", (e) => { const b = e.target.closest("[data-config-brain]"); if (b) openSettings(b.dataset.configBrain); });
  $("coreKeys").addEventListener("click", () => openSettings());

  // ------------------------------------------------------------------ página AI VOICE
  const VOICE_LABEL = { elevenlabs: "ElevenLabs", clonada: "Minha voz (local)", navegador: "Voz do navegador" };
  function renderVoicePage() {
    $("vcListen").textContent = SR ? (recActive ? "Ouvindo agora (pt-BR)" : "Reconhecimento pt-BR pronto") : "Indisponível neste navegador";
    $("vcSpeak").textContent = VOICE_LABEL[voiceMode] || voiceMode;
    $("vcWake").textContent = $("wakeToggle").checked ? 'Ligada — diga "Jarvis"' : "Desligada (use o microfone)";
    document.querySelectorAll("#voiceGrid .prov").forEach((c) => c.classList.toggle("on", c.dataset.voz === voiceMode));
    $("dgVoice").textContent = `${VOICE_LABEL[voiceMode] || voiceMode}${$("wakeToggle").checked ? " · ouvindo" : ""}`;
  }
  $("voiceGrid").addEventListener("click", (e) => {
    const b = e.target.closest("[data-usar-voz]");
    if (b) { setVoiceMode(b.dataset.usarVoz); renderVoicePage(); speak("Esta é a voz que vou usar a partir de agora."); }
    if (e.target.closest("[data-config-voz]")) openSettings();
  });
  $("vcTestVoice").addEventListener("click", () => { getAudioCtx(); speak(`Sistemas de voz operacionais, ${chamar()}.`); });
  $("vcTestMic").addEventListener("click", async () => {
    const meter = $("micMeter");
    meter.innerHTML = "<i></i>".repeat(32);
    let stream;
    try { stream = await navigator.mediaDevices.getUserMedia({ audio: true }); }
    catch (e) { toast("Sem acesso ao microfone (permissão negada).", "warn"); return; }
    const ctx = getAudioCtx(), an = ctx.createAnalyser();
    an.fftSize = 64; ctx.createMediaStreamSource(stream).connect(an);
    const buf = new Uint8Array(an.frequencyBinCount), bars = [...meter.children], fim = Date.now() + 6000;
    toast("Fale algo: as barras devem subir.", "ok");
    (function loop() {
      an.getByteFrequencyData(buf);
      bars.forEach((b, i) => { b.style.height = `${2 + (buf[i % buf.length] / 255) * 26}px`; });
      if (Date.now() < fim) requestAnimationFrame(loop); else { stream.getTracks().forEach((t) => t.stop()); }
    })();
  });
  $("wakeToggle").addEventListener("change", renderVoicePage);
  function salvarPref(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* sem storage */ } }
  $("vcConversa").value = String(conversaSeg());
  $("vcInterromper").checked = pref("jarvis.interromper", "1") === "1";
  $("vcConversa").addEventListener("change", (e) => {
    salvarPref("jarvis.conversa", e.target.value);
    toast(e.target.value === "0" ? "Conversa contínua desligada: chame \"Jarvis\" a cada pedido." :
      `Depois de cada resposta eu fico ${e.target.value} s ouvindo.`, "ok");
  });
  $("vcInterromper").addEventListener("change", (e) => salvarPref("jarvis.interromper", e.target.checked ? "1" : "0"));

  // ------------------------------------------------------------------ página INTEGRAÇÕES
  const APP_TILE = { claude: "✳", openai: "AI", gemini: "✦", groq: "⚡", openrouter: "OR", ollama: "🦙", email: "M", agenda: "31",
    github: "GH", docker: "🐳", spotify: "♫", elevenlabs: "11", mhnet: "MH", statusrobot: "SR" };
  async function loadApps() {
    try { renderApps((await getJSON("/api/integracoes")).integracoes); } catch (e) { toast("Não consegui carregar as integrações.", "warn"); }
  }
  function renderApps(lista) {
    $("appsGrid").innerHTML = lista.map((a) => {
      let acoes = "";
      if (a.config === "campos") {
        acoes = `<button type="button" class="${a.conectado ? "ghost" : "send"}" data-abrir-form>${a.conectado ? "CONFIGURAR" : "CONECTAR"}</button>
          <form class="app-form" data-app="${a.id}" hidden>${a.campos.map((c) => `<label>${esc(c.rotulo.toUpperCase())}</label>
          <input name="${c.var}" type="${c.tipo}" spellcheck="false" placeholder="${c.preenchido ? "Salvo — preencha só para trocar" : esc(c.dica.split("·")[0].slice(0, 60))}">
          <small>${esc(c.dica)}</small>`).join("")}
          <div class="row-btns"><button type="submit" class="send">${a.conectado ? "ATUALIZAR" : "CONECTAR"}</button>
          ${a.conectado && a.id !== "ollama" ? `<button type="button" class="ghost" data-desconectar="${a.id}">DESCONECTAR</button>` : ""}</div>
          <p class="app-msg"></p></form>`;
      } else if (a.config === "ai_core") acoes = `<button type="button" class="ghost" data-ir="core">CONFIGURAR NO AI CORE</button>`;
      else if (a.config === "ai_voice") acoes = `<button type="button" class="ghost" data-ir="voice">CONFIGURAR NO AI VOICE</button>`;
      else if (a.config === "configuracoes") acoes = `<button type="button" class="ghost" data-abrir-config>CONFIGURAR</button>`;
      else if (a.config === "whatsapp") acoes = `<button type="button" class="send" data-abrir-form>CONTATOS E MODO</button>
          <div class="app-form" id="waBox" hidden></div>`;
      return `<div class="app-card ${a.conectado ? "on" : ""}"><span class="logo-tile">${APP_TILE[a.id] || "◆"}</span><b>${esc(a.nome)}</b>
        <span class="tag ${a.categoria === "LLM" || a.categoria === "LLM local" ? "" : a.categoria === "Trabalho" ? "warn" : "ok"}">${esc(a.categoria.toUpperCase())}</span>
        <p>${esc(a.descricao)}</p><div class="status-line ${a.conectado ? "on" : "off"}">${a.conectado ? "● Conectado" : "○ Não conectado"}</div>${acoes}</div>`;
    }).join("");
  }
  $("appsGrid").addEventListener("submit", async (e) => {
    e.preventDefault();
    const form = e.target, msg = form.querySelector(".app-msg");
    const campos = Object.fromEntries([...form.querySelectorAll("input")].map((i) => [i.name, i.value]));
    msg.textContent = "Testando a conexão…"; msg.className = "app-msg";
    try {
      const r = await fetch(`/api/integracoes/${form.dataset.app}`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ campos }) });
      const d = await r.json();
      if (!r.ok) throw new Error(d.detail || `HTTP ${r.status}`);
      renderApps(d.integracoes);
      toast(d.ok ? "Integração conectada." : `Salvo, mas: ${d.mensagem}`, d.ok ? "ok" : "warn");
      pollPanels(true);
    } catch (err) { msg.textContent = err.message; msg.className = "app-msg err"; }
  });
  $("appsGrid").addEventListener("click", async (e) => {
    const b = e.target.closest("[data-desconectar]");
    if (b) {
      const d = await (await fetch(`/api/integracoes/${b.dataset.desconectar}`, { method: "DELETE" })).json();
      renderApps(d.integracoes); pollPanels(true); toast("Integração desconectada.", "ok");
    }
    if (e.target.closest("[data-abrir-config]")) openSettings();
    const abrir = e.target.closest("[data-abrir-form]");
    if (abrir && abrir.nextElementSibling && abrir.nextElementSibling.id === "waBox") {
      abrir.hidden = true; abrir.nextElementSibling.hidden = false; loadWhatsapp(); return;
    }
    if (abrir) {
      const form = abrir.nextElementSibling;
      form.hidden = !form.hidden; abrir.hidden = true;
      const primeiro = form.querySelector("input");
      if (primeiro) primeiro.focus();
    }
  });

  // ------------------------------------------------------------------ WhatsApp: modo e contatos
  async function loadWhatsapp(d) {
    try { d = d || await getJSON("/api/whatsapp"); } catch (e) { return; }
    const box = $("waBox");
    if (!box) return;
    const ext = d.extensao || {};
    const velha = ext.conectada && ext.versao_atual && ext.versao !== ext.versao_atual;
    box.innerHTML = `<label>EXTENSÃO DO NAVEGADOR (usa o WhatsApp que já está aberto)</label>
      ${velha ? `<p class="app-msg err">⚠ Extensão desatualizada (${esc(ext.versao)} → ${esc(ext.versao_atual)}): em chrome://extensions clique em
        ↻ (recarregar) no card do JARVIS e depois recarregue a aba do WhatsApp (F5).</p>` : ""}
      ${ext.conectada ? `<p class="app-msg ok">● Conectada no ${esc(ext.navegador || "navegador")} — o JARVIS usa o seu WhatsApp Web já logado.</p>`
        : `<p class="app-msg">○ Não instalada neste navegador.</p>
      <ol class="steps"><li>Clique em <b>PREPARAR EXTENSÃO</b> (abre a tela de extensões e a pasta).</li>
        <li>Ligue o <b>Modo do desenvolvedor</b> (canto superior direito).</li>
        <li>Clique em <b>Carregar sem compactação</b>, cole este caminho na barra de endereço da janela e clique em
          <b>Selecionar pasta</b>:<br><code>${esc(ext.pasta || "")}</code>
          <button type="button" class="ghost mini" data-wa-copiar="${esc(ext.pasta || "")}">COPIAR CAMINHO</button></li>
        <li>Recarregue a aba do WhatsApp Web (F5).</li></ol>
      <div class="row-btns"><button type="button" class="send" data-wa-extensao>PREPARAR EXTENSÃO</button></div>`}
      <label>SEM A EXTENSÃO, ENVIAR PELO</label>
      <div class="voice-modes">${[["web", "WhatsApp Web (recomendado)"], ["app", "App do PC (só números)"]].map(([v, t]) =>
        `<label><input type="radio" name="waModo" value="${v}" ${d.modo === v ? "checked" : ""}> ${t}</label>`).join("")}</div>
      <small>${d.usando === "app" ? "App do PC: o app não deixa o JARVIS ler a tela, então só manda para números salvos e sem conferir."
        : "WhatsApp Web numa janela do JARVIS: acha contatos do celular e grupos pelo nome e confere o envio. Na 1ª vez, escaneie o QR code."}</small>
      <label>CONTATOS (${d.contatos.length})</label>
      <ul class="rows">${d.contatos.map((c) => `<li><div class="main"><b>${esc(c.nome)}</b><small>${esc(c.numero)}</small></div>
        <button type="button" class="ct" title="Remover" data-wa-remover="${esc(c.nome)}">✕</button></li>`).join("") || '<p class="empty">Nenhum contato ainda.</p>'}</ul>
      <input id="waNome" placeholder="Nome (ex.: Maria)"><input id="waNumero" placeholder="Número com DDD (ex.: 42 99999-0000)">
      <div class="row-btns"><button type="button" class="send" data-wa-salvar>SALVAR CONTATO</button></div>
      <small>Contatos aqui são opcionais (só para mandar por número). Por voz: "manda uma mensagem pra Maria dizendo que chego às 8"
        ou "manda mensagem pro grupo da família".</small>
      <p class="app-msg" id="waMsg"></p>`;
  }
  $("appsGrid").addEventListener("change", async (e) => {
    if (e.target.name !== "waModo") return;
    const r = await fetch("/api/whatsapp/modo", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ modo: e.target.value }) });
    loadWhatsapp(await r.json());
  });
  $("appsGrid").addEventListener("click", async (e) => {
    const copiar = e.target.closest("[data-wa-copiar]");
    if (copiar) {
      try { await navigator.clipboard.writeText(copiar.dataset.waCopiar); toast("Caminho copiado.", "ok"); }
      catch (err) { toast("Selecione o caminho e copie com Ctrl+C.", "warn"); }
    }
    if (e.target.closest("[data-wa-extensao]")) {
      const r = await fetch("/api/whatsapp/extensao", { method: "POST" });
      const d = await r.json();
      toast(r.ok ? "Extensão pronta: siga os passos na tela de extensões que abri." : d.detail, r.ok ? "ok" : "warn");
      setTimeout(() => loadWhatsapp(), 15000);
    }
    const rem = e.target.closest("[data-wa-remover]");
    if (rem) loadWhatsapp(await (await fetch("/api/whatsapp/contatos/" + encodeURIComponent(rem.dataset.waRemover), { method: "DELETE" })).json());
    if (e.target.closest("[data-wa-salvar]")) {
      const r = await fetch("/api/whatsapp/contatos", { method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify({ nome: $("waNome").value, numero: $("waNumero").value }) });
      const d = await r.json();
      if (!r.ok) { $("waMsg").textContent = d.detail; $("waMsg").className = "app-msg err"; return; }
      loadWhatsapp(d);
    }
  });

  // ------------------------------------------------------------------ Spotify (configuração)
  let spPoll = null;
  async function loadSpotify() {
    let s;
    try { s = await (await fetch("/api/spotify")).json(); } catch (e) { return null; }
    $("spRedirect").textContent = s.redirect_uri;
    const st = $("spState");
    st.textContent = s.conectado ? "● CONECTADO" : s.configurado ? "○ FALTA CONECTAR A CONTA" : "○ NÃO CONFIGURADO";
    st.className = "sp-state " + (s.conectado ? "on" : "off");
    $("spConnect").disabled = !s.configurado;
    $("spConnect").textContent = s.conectado ? "RECONECTAR" : "CONECTAR CONTA";
    if (s.configurado && !$("spId").value) $("spId").placeholder = `Salvo (${s.client_id}) — cole outro só para trocar`;
    if (s.erro) { $("spMsg").textContent = s.erro; $("spMsg").className = "key-status err"; }
    return s;
  }

  $("spCopy").addEventListener("click", async () => {
    try { await navigator.clipboard.writeText($("spRedirect").textContent); toast("Redirect URI copiada.", "ok"); }
    catch (e) { toast("Selecione o endereço e copie com Ctrl+C.", "warn"); }
  });

  $("spSave").addEventListener("click", async () => {
    const msg = $("spMsg");
    msg.textContent = "Conferindo com o Spotify…"; msg.className = "key-status wait";
    try {
      const r = await fetch("/api/spotify", { method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify({ client_id: $("spId").value.trim(), client_secret: $("spSecret").value.trim() }) });
      const d = await r.json();
      if (!r.ok) throw new Error(d.detail || `HTTP ${r.status}`);
      $("spId").value = ""; $("spSecret").value = "";
      msg.textContent = d.mensagem; msg.className = "key-status ok";
      loadSpotify();
    } catch (err) { msg.textContent = err.message; msg.className = "key-status err"; }
  });

  $("spConnect").addEventListener("click", async () => {
    const msg = $("spMsg");
    try {
      const r = await fetch("/api/spotify/conectar", { method: "POST" });
      const d = await r.json();
      if (!r.ok) throw new Error(d.detail || `HTTP ${r.status}`);
      msg.textContent = d.mensagem; msg.className = "key-status wait";
      clearInterval(spPoll);
      let tries = 0;
      spPoll = setInterval(async () => {      // espera o login no navegador terminar (até ~3 min)
        const s = await loadSpotify();
        if ((s && s.conectado && !s.login_em_andamento) || ++tries > 60) {
          clearInterval(spPoll);
          if (s && s.conectado) { msg.textContent = "✓ Spotify conectado. Diga: toque Back in Black."; msg.className = "key-status ok"; }
        }
      }, 3000);
    } catch (err) { msg.textContent = err.message; msg.className = "key-status err"; }
  });

  // ------------------------------------------------------------------ contas pessoais (CLIs oficiais)
  let accountPoll = null;
  async function loadAccounts(fresh = false) {
    let list = [];
    try { list = await (await fetch(`/api/contas?fresh=${fresh}`)).json(); } catch (e) { return; }
    $("accountList").innerHTML = list.map((c) => {
      const estado = !c.instalado ? "○ NÃO INSTALADA" : c.logado ? "● CONECTADA" : "○ NÃO CONECTADA";
      const botoes = !c.instalado ? `<button type="button" class="send" data-acc="${c.id}" data-acao="instalar">INSTALAR</button>`
        : !c.logado ? `<button type="button" class="send" data-acc="${c.id}" data-acao="conectar">CONECTAR</button>`
        : `<button type="button" class="ghost" data-acc="${c.id}" data-acao="usar">${currentBrain === c.id ? "EM USO" : "USAR"}</button>`;
      return `<div class="account ${c.logado ? "on" : "off"}">
        <span class="acc-name">${esc(c.nome)} <span class="acc-state">${estado}</span></span>
        <div class="acc-actions">${botoes}</div>
        <span class="acc-plan">${esc(c.logado && c.detalhe ? c.detalhe : "Plano: " + c.plano)}</span></div>`;
    }).join("");
    return list;
  }

  $("accountList").addEventListener("click", async (e) => {
    const btn = e.target.closest("button[data-acc]");
    if (!btn) return;
    const id = btn.dataset.acc, acao = btn.dataset.acao;
    if (acao === "usar") {
      if (ws) ws.send(JSON.stringify({ type: "set_brain", brain: id }));
      setTimeout(() => loadAccounts(), 300);
      return;
    }
    btn.disabled = true;
    try {
      const r = await fetch(`/api/contas/${id}/${acao}`, { method: "POST" });
      const d = await r.json();
      if (!r.ok) throw new Error(d.detail || `HTTP ${r.status}`);
      toast(d.mensagem, "ok");
      // Acompanha a janela de instalação/login até a conta aparecer pronta (até ~3 min)
      clearInterval(accountPoll);
      let tries = 0;
      accountPoll = setInterval(async () => {
        const list = await loadAccounts(true);
        try { renderBrains((await (await fetch("/api/config")).json()).brains); } catch (err) { /* ignora */ }
        const c = (list || []).find((x) => x.id === id);
        if ((c && (acao === "instalar" ? c.instalado : c.logado)) || ++tries > 36) {
          clearInterval(accountPoll);
          if (c && c.logado) toast(`${c.nome} conectada. Clique em USAR para ativar.`, "ok");
        }
      }, 5000);
    } catch (err) {
      toast(err.message, "warn");
    } finally {
      btn.disabled = false;
    }
  });
  $("settingsBtn").addEventListener("click", () => openSettings());
  $("keyBrain").addEventListener("change", fillKeyForm);
  $("keyClose").addEventListener("click", () => { $("settings").hidden = true; });
  $("settings").addEventListener("click", (e) => { if (e.target === $("settings")) $("settings").hidden = true; });

  $("settingsForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const brain = $("keyBrain").value, key = $("keyValue").value.trim(), model = chosenModel();
    const status = $("keyStatus");
    const configured = (brains.find((b) => b.id === brain) || {}).configured;
    if (!key && !configured) { status.textContent = "Cole a chave de API primeiro."; status.className = "key-status err"; return; }
    if (!key && !model) { status.textContent = "Escolha um modelo."; status.className = "key-status err"; return; }
    status.textContent = key ? "Testando a chave…" : "Trocando o modelo…"; status.className = "key-status wait";
    $("keySave").disabled = true;
    try {
      const r = await fetch("/api/brain-key", {
        method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify({ brain, key, model }),
      });
      const data = await r.json();
      if (!r.ok) throw new Error(data.detail || `HTTP ${r.status}`);
      renderBrains(data.brains);
      $("keyValue").value = "";
      status.textContent = data.mensagem; status.className = "key-status ok";
      if (ws) ws.send(JSON.stringify({ type: "set_brain", brain }));
      toast(`${brains.find((b) => b.id === brain).label} ativo com o modelo ${model || "padrão"}.`, "ok");
      setTimeout(() => { $("settings").hidden = true; }, 1200);
    } catch (err) {
      status.textContent = err.message; status.className = "key-status err";
    } finally {
      $("keySave").disabled = false;
    }
  });
  $("resetBtn").addEventListener("click", () => ws && ws.send(JSON.stringify({ type: "reset" })));
  const answer = (approve) => {
    if (ws && pendingConfirmId) ws.send(JSON.stringify({ type: "confirm", id: pendingConfirmId, approve }));
    $("confirm").hidden = true; pendingConfirmId = null;
    avisarFlutuante();
  };
  $("confirmYes").addEventListener("click", () => answer(true));
  $("confirmNo").addEventListener("click", () => answer(false));

  // ------------------------------------------------------------------ inicialização
  async function boot() {
    const lines = [
      "> Inicializando núcleo de processamento........ OK",
      "> Carregando matriz de personalidade........... OK",
      "> Conectando sensores de hardware.............. OK",
      "> Calibrando reconhecimento de voz (pt-BR)..... " + (SR ? "OK" : "INDISPONÍVEL"),
      "> Estabelecendo link com o servidor local......",
    ];
    for (const l of lines) {
      $("bootLog").textContent += l + "\n";
      await new Promise((r) => setTimeout(r, 260));
    }
    let cfg = { brains: [{ id: "local", label: "Offline", configured: true }], default_brain: "local", tratamento: "senhor" };
    try { cfg = await (await fetch("/api/config")).json(); $("bootLog").textContent += "> Link estabelecido. Cérebros configurados: " + cfg.brains.filter((b) => b.configured).map((b) => b.label).join(", ") + "\n"; }
    catch (e) { $("bootLog").textContent += "> Falha no link. Tentando novamente em segundo plano.\n"; }
    tratamento = cfg.tratamento || "senhor";
    userName = cfg.nome || "";
    $("pfNome").value = userName;
    document.querySelectorAll('input[name="janelaModo"]').forEach((r) => { r.checked = r.value === (cfg.janela || "maximizada"); });
    $("cmdName").textContent = userName || tratamento;
    $("cmdAvatar").textContent = (userName || "J").trim().charAt(0).toUpperCase();
    decorateCards();
    loadLayout();
    loadWeatherMini();
    pollPanels(true);
    try { renderVoicePage(); } catch (e) { /* voz ainda iniciando */ }
    $("pfTrat").value = tratamento;
    renderBrains(cfg.brains);
    currentBrain = cfg.default_brain;
    $("brainSelect").value = currentBrain;
    connect();
    const btn = $("bootBtn");
    btn.disabled = false; btn.textContent = "ATIVAR J.A.R.V.I.S.";
    btn.focus();
    // ?bg=1: janela reaberta escondida na bandeja depois que você fechou a principal — só volta a ouvir
    const background = new URLSearchParams(location.search).has("bg");
    btn.addEventListener("click", () => {
      booted = true;
      getAudioCtx();   // o clique libera o áudio da voz clonada
      $("boot").classList.add("hide");
      setTimeout(() => $("boot").remove(), 900);
      if ($("wakeToggle").checked && SR) { wantListening = true; startRecognition(); }   // já ouvindo "Jarvis"
      $("chatInput").focus();
      if (background) { report("janela na bandeja ativa; ouvindo: " + (wantListening ? "sim" : "não")); return; }
      // A saudação vem do servidor (entra no histórico: dá para responder "as notícias" em seguida)
      let tries = 0;
      const greet = () => {
        if (connected) { ws.send(JSON.stringify({ type: "greet" })); return; }
        if (++tries < 15) { setTimeout(greet, 300); return; }
        const h = new Date().getHours();
        const msg = `${h < 12 ? "Bom dia" : h < 18 ? "Boa tarde" : "Boa noite"}, ${chamar()}. Sistemas online, mas sem link com o núcleo.`;
        $("caption").textContent = msg; addLog("JARVIS", msg, "jarvis"); speak(msg);
      };
      greet();
    }, { once: true });
    // Aberto pelo aplicativo (.exe): ativa sozinho, sem precisar clicar (ex.: ao iniciar com o Windows)
    if (new URLSearchParams(location.search).has("auto")) setTimeout(() => btn.click(), 700);
  }
  boot();
})();
