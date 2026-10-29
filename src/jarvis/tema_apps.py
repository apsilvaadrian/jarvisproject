"""Visual do JARVIS dentro dos apps (WhatsApp, Instagram, Facebook, YouTube e o Navegador).

O navegador dos apps abre em modo escuro (os sites que têm tema escuro usam o deles; os que não têm são escurecidos
automaticamente). Por cima, esta folha de estilo troca as cores de fundo de cada site pelo azul-marinho do JARVIS,
com destaques em ciano e barra de rolagem fina. É aplicada pelo próprio JARVIS em cada página (e reaplicada quando
a página recarrega); desligando a opção, ela sai na hora.
"""
from __future__ import annotations

# Paleta do JARVIS (a mesma da interface)
FUNDO, PAINEL, PAINEL_2, DESTAQUE, CIANO = "#020a12", "#04121e", "#072235", "#0b3d55", "#2ee6ff"

# Em qualquer site: seleção, barra de rolagem e realce em ciano. O fundo só muda nos sites conhecidos e quando eles
# estão no tema escuro (em site claro, trocar só o fundo deixaria texto escuro em fundo escuro; esses o próprio
# navegador escurece sozinho).
COMUM = f"""
:root {{ accent-color: {CIANO}; }}
::selection {{ background: rgba(46,230,255,.35) !important; color: #fff !important; }}
* {{ scrollbar-width: thin; scrollbar-color: rgba(46,230,255,.45) transparent; }}
"""

POR_SITE = {
    "web.whatsapp.com": f"""
.dark, body.dark, html.dark {{
  --app-background: {FUNDO} !important; --app-background-deeper: {FUNDO} !important; --app-background-stripe: {PAINEL} !important;
  --panel-background: {PAINEL} !important; --panel-background-rgb: 4, 18, 30 !important; --panel-background-deep: #031019 !important;
  --panel-input-background: {PAINEL_2} !important; --navbar-background: {PAINEL} !important; --WDS-app-wash: {PAINEL} !important;
  --background-default-rgb: 4, 18, 30 !important; --background-default-hover-rgb: 7, 34, 53 !important;
  --incoming-background: {PAINEL_2} !important; --outgoing-background: {DESTAQUE} !important;
  --compose-panel-background-hover: {PAINEL_2} !important; --empty-state-background: {PAINEL} !important;
  --chip-button-background: {PAINEL_2} !important; --picker-background: {PAINEL_2} !important;
}}
body.dark {{ background-color: {FUNDO} !important; }}
/* Visual novo do WhatsApp (tokens WDS, definidos numa classe do <html>) */
html, html body.dark, #app {{
  --WDS-background-wash-plain: {PAINEL} !important; --WDS-background-wash-plain-RGB: 4, 18, 30 !important;
  --WDS-background-wash-inset: {PAINEL} !important; --WDS-background-wash-inset-RGB: 4, 18, 30 !important;
  --WDS-surface-default: {PAINEL} !important; --WDS-systems-chat-surface-tray: {PAINEL} !important;
  --WDS-systems-chat-background-wallpaper: {FUNDO} !important; --WDS-systems-chat-background-wallpaper-RGB: 2, 10, 18 !important;
  --WDS-systems-chat-foreground-wallpaper: rgba(46,230,255,.06) !important;
  --WDS-background-elevated-wash-plain: {PAINEL_2} !important; --WDS-background-elevated-wash-plain-RGB: 7, 34, 53 !important;
  --WDS-background-elevated-wash-inset: {PAINEL_2} !important; --WDS-background-elevated-wash-inset-RGB: 7, 34, 53 !important;
  --WDS-surface-emphasized: {PAINEL_2} !important; --WDS-surface-elevated-default: {PAINEL_2} !important;
  --WDS-components-surface-nav-bar: {PAINEL_2} !important; --WDS-surface-elevated-emphasized: #0a2d45 !important;
  --WDS-systems-chat-surface-composer: #0a2d45 !important; --WDS-systems-bubble-surface-incoming: #0a2d45 !important;
  --WDS-systems-bubble-surface-outgoing: {DESTAQUE} !important; --WDS-systems-bubble-surface-system: {PAINEL_2} !important;
  --WDS-systems-bubble-surface-e2e: {PAINEL_2} !important; --WDS-systems-bubble-surface-business: {PAINEL_2} !important;
  --WDS-components-filter-surface-selected: {DESTAQUE} !important; --WDS-surface-highlight: rgba(46,230,255,.10) !important;
  --WDS-app-wash: {PAINEL} !important;
}}
""",
    "youtube.com": f"""
html[dark], html[dark][darker-dark-theme], [dark] {{
  --yt-spec-base-background: {PAINEL} !important; --yt-spec-raised-background: {PAINEL_2} !important;
  --yt-spec-menu-background: {PAINEL_2} !important; --yt-spec-inverted-background: #cdf6ff !important;
  --yt-spec-general-background-a: {PAINEL} !important; --yt-spec-general-background-b: {PAINEL} !important;
  --yt-spec-general-background-c: {FUNDO} !important; --yt-spec-additive-background: rgba(46,230,255,.10) !important;
  --yt-spec-badge-chip-background: rgba(46,230,255,.12) !important; --yt-spec-call-to-action: {CIANO} !important;
}}
html[dark] body, html[dark] ytd-app, html[dark] #page-manager, html[dark] ytd-browse, html[dark] ytd-two-column-browse-results-renderer,
html[dark] #masthead-container #background, html[dark] ytd-masthead, html[dark] #guide-content, html[dark] tp-yt-app-drawer #contentContainer,
html[dark] ytd-mini-guide-renderer, html[dark] #chips-wrapper, html[dark] ytd-watch-flexy, html[dark] #columns {{ background: {PAINEL} !important; }}
""",
    "instagram.com": f"""
@media (prefers-color-scheme: dark) {{ :root, .__ig-dark-mode {{
  --ig-primary-background: 4, 18, 30 !important; --ig-secondary-background: 7, 34, 53 !important;
  --ig-elevated-background: 7, 34, 53 !important; --ig-banner-background: 7, 34, 53 !important;
  --ig-highlight-background: 11, 61, 85 !important; --ig-primary-button: 46, 230, 255 !important;
}} }}
""",
    "facebook.com": f"""
.__fb-dark-mode, :root.__fb-dark-mode {{
  --web-wash: {FUNDO} !important; --surface-background: {PAINEL} !important; --card-background: {PAINEL} !important;
  --comment-background: {PAINEL_2} !important; --secondary-button-background: {PAINEL_2} !important;
  --hover-overlay: rgba(46,230,255,.08) !important; --primary-button-background: #1f8aa3 !important; --accent: {CIANO} !important;
}}
""",
    "google.": f"""
@media (prefers-color-scheme: dark) {{
  body#gsr, #gsr #main, #gsr .sfbg, #gsr #searchform, #gsr .appbar, #gsr #top_nav, #gsr #cnt {{ background-color: {PAINEL} !important; }}
}}
""",
}

# Navegador (qualquer site): um "véu" azul-marinho em modo tela (screen) — o preto vira o azul do JARVIS, o branco
# continua branco e as cores quase não mudam. Funciona em todo site escuro (os claros o navegador já escurece).
TINTA = f"""
html.jv-tinta::after {{ content: ""; position: fixed; inset: 0; pointer-events: none; z-index: 2147483647;
  background: {PAINEL}; mix-blend-mode: screen; }}
"""

# Roda em cada página (agora e a cada recarga): põe (ou tira) o <style id="jarvis-tema"> do site
SCRIPT = r"""
(() => {
  const ligado = %(ligado)s, regras = %(regras)s, tinta = %(tinta)s;
  const aplicar = () => {
    let st = document.getElementById("jarvis-tema");
    if (!ligado) { if (st) st.remove(); return; }
    const host = location.host, css = regras.comum + Object.entries(regras.sites).filter(([h]) => host.includes(h)).map(([, c]) => c).join("")
      + (tinta ? regras.tinta : "");
    if (!st) { st = document.createElement("style"); st.id = "jarvis-tema"; (document.head || document.documentElement).appendChild(st); }
    if (st.textContent !== css) st.textContent = css;
    if (st.nextSibling) st.parentNode.appendChild(st);           // por último: ganha das folhas do site
  };
  // Navegador: os fundos cinza-escuros do site viram o azul-marinho do JARVIS (o mesmo dos outros apps), mantendo a
  // diferença de tons entre eles. Fotos, vídeos e cores do site não mudam. Site claro (escurecido pelo navegador):
  // só o véu azul. Cada elemento é olhado uma vez; desligando, tudo volta.
  const recolorir = () => {
    if (!tinta || !document.body) return;
    const vistos = window.__jvVistos || (window.__jvVistos = new WeakSet());
    const cinza = (cor) => {
      const m = /rgba?\((\d+), (\d+), (\d+)(?:, ([\d.]+))?\)/.exec(cor || "");
      if (!m || (m[4] !== undefined && +m[4] < .5)) return null;
      const r = +m[1], g = +m[2], b = +m[3];
      return Math.max(r, g, b) - Math.min(r, g, b) <= 16 && (r + g + b) / 3 <= 64 ? (r + g + b) / 3 : null;
    };
    if (window.__jvEscuro === undefined) {
      window.__jvEscuro = cinza(getComputedStyle(document.body).backgroundColor) !== null
        || cinza(getComputedStyle(document.documentElement).backgroundColor) !== null;
    }
    document.documentElement.classList.toggle("jv-tinta", ligado && !window.__jvEscuro);
    for (const el of [document.documentElement, document.body, ...document.body.querySelectorAll("*")]) {
      if (vistos.has(el)) continue;
      vistos.add(el);
      const v = cinza(getComputedStyle(el).backgroundColor);
      if (v === null) continue;
      el.style.setProperty("background-color", `rgb(${Math.round(v * .25)}, ${Math.round(v * .85 + 4)}, ${Math.round(v * 1.35 + 8)})`, "important");
      el.dataset.jvCor = "1";
    }
  };
  const desfazer = () => {
    document.documentElement.classList.remove("jv-tinta");
    document.querySelectorAll("[data-jv-cor]").forEach((el) => { el.style.removeProperty("background-color"); delete el.dataset.jvCor; });
    window.__jvVistos = new WeakSet();
  };
  aplicar();
  if (ligado) recolorir(); else desfazer();
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", () => { aplicar(); if (ligado) recolorir(); }, { once: true });
})();
"""


def script(ligado: bool, tinta: bool = False) -> str:
    """tinta=True: o véu azul do JARVIS (para o Navegador, que abre qualquer site)."""
    import json
    return SCRIPT % {"ligado": "true" if ligado else "false", "tinta": "true" if tinta else "false",
                     "regras": json.dumps({"comum": COMUM, "sites": POR_SITE, "tinta": TINTA})}
