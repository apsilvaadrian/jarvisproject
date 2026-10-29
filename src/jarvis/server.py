"""Servidor HTTP + WebSocket que liga a interface ao cérebro e ao sistema."""
from __future__ import annotations

import asyncio
import os
import re
import time
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from urllib.parse import quote

from . import boas_vindas, cards, contas, integracoes, leitor, letras, report, spotify, voice
from . import system_monitor as sm
from .assistant import Assistant
from .brain import KEY_LINKS, LABELS, BrainError, list_models, validate_key
from .config import BRAIN_ENV, settings
from .logger import log, recent_actions

WEB_DIR = Path(__file__).parent / "web"
STATS_INTERVAL = 1.5

app = FastAPI(title="J.A.R.V.I.S.", version="1.0.0")
app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")


# Só aceita quem fala com o JARVIS por 127.0.0.1/localhost: impede que um site aberto no navegador (ou um
# ataque de "DNS rebinding") use a API e o WebSocket do JARVIS.
HOSTS_LOCAIS = ("127.0.0.1", "localhost", "testserver")


def _origem_local(origem: str) -> bool:
    from urllib.parse import urlparse
    return not origem or urlparse(origem).hostname in HOSTS_LOCAIS


@app.middleware("http")
async def somente_local(request, call_next):
    host = (request.headers.get("host") or "").rsplit(":", 1)[0].strip("[]")
    if host not in HOSTS_LOCAIS or not _origem_local(request.headers.get("origin", "")):
        return Response("Acesso permitido só a partir deste computador.", status_code=403)
    return await call_next(request)


@app.get("/")
def index() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html")


@app.get("/flutuante")
def flutuante_pagina() -> FileResponse:
    """Janelinha flutuante (aparece com a janela principal minimizada ou na bandeja)."""
    return FileResponse(WEB_DIR / "flutuante.html")


@app.get("/health")
def health() -> dict[str, Any]:
    return {"status": "online", "pid": os.getpid()}


@app.get("/api/config")
def config() -> dict[str, Any]:
    return {
        "brains": [{**b, "label": LABELS[b["id"]], "key_url": KEY_LINKS.get(b["id"])} for b in settings.brain_status()],
        "default_brain": settings.default_brain(),
        "janela": settings.janela, "flutuante": settings.flutuante != "0",
        "navegador": settings.navegador,
        "spotify_api": spotify.is_configured(),
        "tratamento": settings.tratamento,
        "nome": settings.nome,
    }


class Autostart(BaseModel):
    ativo: bool


class Janela(BaseModel):
    modo: str


@app.post("/api/janela")
def janela_set(body: Janela) -> dict[str, Any]:
    """Tela cheia, maximizada ou janela: grava e já aplica na janela aberta."""
    from . import desktop
    if body.modo not in desktop.MODOS_JANELA:
        raise HTTPException(400, "Modo inválido: cheia, maximizada ou janela.")
    settings.set_vars({"JARVIS_JANELA": body.modo})
    aplicado = False
    try:
        aplicado = desktop.aplicar_modo_agora(body.modo)
    except Exception as exc:  # sem janela nativa (ex.: aberto num navegador comum)
        log.warning("Não consegui aplicar o modo de janela agora: %s", exc)
    return {"ok": True, "modo": body.modo, "aplicado": aplicado}


# --------------------------------------------------------------------------- navegador interno
@app.get("/api/web/ler")
def web_ler(url: str) -> dict[str, Any]:
    """Modo leitura: o conteúdo principal da página (notícia, artigo)."""
    return leitor.leitura(url)


@app.get("/web/pagina")
def web_pagina(url: str, request: Request) -> Response:
    """Modo página: o site original sem scripts (num iframe sandbox), com os links voltando para cá."""
    html = leitor.pagina(url, f"{str(request.base_url).rstrip('/')}/web/pagina?url=")
    return Response(html, media_type="text/html; charset=utf-8", headers={
        "Content-Security-Policy": "script-src 'none'; object-src 'none'; frame-src 'none'",
        "Referrer-Policy": "no-referrer"})


class WebExterno(BaseModel):
    url: str


@app.post("/api/web/externo")
def web_externo(body: WebExterno) -> dict[str, bool]:
    """↗ do navegador interno: abre a página no navegador padrão do Windows."""
    import webbrowser
    url = leitor.resolver(body.url.strip())
    if not url.startswith(("http://", "https://")):
        raise HTTPException(400, "Endereço inválido.")
    return {"ok": bool(webbrowser.open(url))}


# --------------------------------------------------------------------------- apps da web dentro do JARVIS
class Encaixe(BaseModel):
    app: str
    rect: list[float]               # x, y, largura, altura do painel (px CSS da página)
    dpr: float = 1.0
    vista: list[float]              # innerWidth, innerHeight da página
    visivel: bool = True


@app.post("/api/apps_web/encaixe")
def apps_web_encaixe(body: Encaixe) -> dict[str, Any]:
    """A página diz onde está o painel APPS; a janela do app (WhatsApp, Instagram, Facebook) vai para cima dele."""
    from .embutidos import EMBUTIDOS
    if len(body.rect) < 4 or len(body.vista) < 2:
        raise HTTPException(400, "Encaixe inválido.")
    return EMBUTIDOS.encaixar(body.app, body.rect[:4], body.dpr, body.vista[:2], body.visivel)


class Encaixes(BaseModel):
    slots: dict[str, dict[str, Any]] = {}     # app -> {"rect": [x, y, w, h], "clip": [x, y, w, h]} (px CSS)
    dpr: float = 1.0
    vista: list[float]
    visivel: bool = True


@app.post("/api/apps_web/encaixes")
def apps_web_encaixes(body: Encaixes) -> dict[str, Any]:
    """Todas as vagas de apps da janela principal (painel do centro e cards): cada app vai para a sua."""
    from .embutidos import EMBUTIDOS
    if len(body.vista) < 2:
        raise HTTPException(400, "Encaixe inválido.")
    return EMBUTIDOS.encaixar_varios("principal", body.slots, body.dpr, body.vista, body.visivel)


@app.post("/api/apps_web/fechar")
def apps_web_fechar() -> dict[str, bool]:
    from .embutidos import EMBUTIDOS
    EMBUTIDOS.fechar_painel()
    return {"ok": True}


@app.get("/api/apps_web")
def apps_web_status() -> dict[str, Any]:
    from .embutidos import EMBUTIDOS
    return {**EMBUTIDOS.status(), "tema": settings.apps_tema != "0"}


class TemaApps(BaseModel):
    ativo: bool


@app.post("/api/apps_web/tema")
def apps_web_tema(body: TemaApps) -> dict[str, Any]:
    """Visual do JARVIS nos apps: troca na hora nas páginas abertas (o modo escuro vale ao reabrir os apps)."""
    import threading
    from .embutidos import EMBUTIDOS
    settings.set_vars({"JARVIS_APPS_TEMA": "1" if body.ativo else "0"})
    threading.Thread(target=EMBUTIDOS.tematizar, daemon=True).start()
    return {"ok": True, "tema": body.ativo}


class Navegar(BaseModel):
    app: str = "navegador"
    acao: str
    url: str = ""


@app.post("/api/apps_web/navegar")
def apps_web_navegar(body: Navegar) -> dict[str, Any]:
    """Voltar, avançar, recarregar e a barra de endereço/pesquisa do Navegador do JARVIS."""
    from .embutidos import EMBUTIDOS
    return EMBUTIDOS.navegar(body.app, body.acao, body.url)


class IrApp(BaseModel):
    app: str
    url: str = ""


@app.post("/api/apps_web/ir")
def apps_web_ir(body: IrApp) -> dict[str, Any]:
    from .embutidos import EMBUTIDOS
    return EMBUTIDOS.ir(body.app, body.url)


class AbrirConversa(BaseModel):
    titulo: str


@app.post("/api/apps_web/conversa")
def apps_web_conversa(body: AbrirConversa) -> dict[str, Any]:
    """Card do WhatsApp: abre a conversa clicada no WhatsApp de dentro do JARVIS."""
    from .embutidos import EMBUTIDOS
    return EMBUTIDOS.whatsapp({"type": "abrir_conversa", "titulo": body.titulo[:200]}, timeout=25)


def avisar_app(texto: str, detalhe: str) -> None:
    """Mensagem/notificação nova num app embutido: aparece (e é falada) na janela do JARVIS."""
    log.info("Aviso: %s %s", texto, detalhe)
    for loop, send, _assistant in list(_sessoes):
        asyncio.run_coroutine_threadsafe(send({"type": "aviso_app", "texto": texto, "detalhe": detalhe}), loop)


class Flutuante(BaseModel):
    ativo: bool


class RegiaoFlutuante(BaseModel):
    formas: list[list[list[float]]]   # polígonos [[x, y], ...] em px CSS
    dpr: float = 1.0
    vista: list[float] = []      # tamanho da página (largura, altura) em px CSS


@app.get("/api/flutuante")
def flutuante_status() -> dict[str, Any]:
    from . import flutuante
    return {"ativo": settings.flutuante != "0", "disponivel": flutuante.FLUTUANTE is not None}


@app.post("/api/flutuante")
def flutuante_set(body: Flutuante) -> dict[str, Any]:
    settings.set_vars({"JARVIS_FLUTUANTE": "1" if body.ativo else "0"})
    return flutuante_status()


def _aplicar_regiao(formas: Any, dpr: Any, vista: Any, redesenhar: Any = True) -> bool:
    """A página diz onde estão as peças (polígonos em px CSS); o resto da janela fica vazado."""
    from . import flutuante
    try:
        limpas = [[[float(p[0]), float(p[1])] for p in f[:32]] for f in list(formas)[:24]]
        limpas = [f for f in limpas if len(f) >= 3]
        v = (float(vista[0]), float(vista[1])) if vista and len(vista) >= 2 else None
        return bool(flutuante.FLUTUANTE and limpas and flutuante.FLUTUANTE.regiao(limpas, float(dpr or 1), v,
                                                                                  redesenhar is not False))
    except (TypeError, ValueError, IndexError):
        return False


@app.get("/api/flutuante/telas")
def flutuante_telas() -> dict[str, Any]:
    """Onde está cada monitor (área de trabalho), em px CSS da página da janelinha: para arrastar entre monitores."""
    from . import flutuante
    return {"telas": flutuante.FLUTUANTE.telas() if flutuante.FLUTUANTE else []}


@app.post("/api/flutuante/regiao")
def flutuante_regiao(body: RegiaoFlutuante) -> dict[str, bool]:
    return {"ok": _aplicar_regiao(body.formas, body.dpr, body.vista)}


class Navegador(BaseModel):
    escolha: str


def _navegadores() -> dict[str, Any]:
    from .app import _browser, browsers, tipo_navegador
    return {"escolha": settings.navegador, "instalados": [tipo_navegador(b) for b in browsers()],
            "em_uso": tipo_navegador(_browser())}


@app.get("/api/navegador")
def navegador_status() -> dict[str, Any]:
    return _navegadores()


@app.post("/api/navegador")
def navegador_set(body: Navegador) -> dict[str, Any]:
    """Chrome, Edge ou automático; no JARVIS.exe a janela já reabre no navegador escolhido."""
    import threading
    if body.escolha not in ("auto", "chrome", "edge"):
        raise HTTPException(400, "Escolha inválida: auto, chrome ou edge.")
    settings.set_vars({"JARVIS_NAVEGADOR": body.escolha})
    r = _navegadores()
    reabrir = ganchos.get("reabrir")
    r["reabrindo"] = bool(reabrir)
    if reabrir:
        threading.Timer(1.0, reabrir).start()          # responde antes de fechar esta própria janela
    return r


@app.get("/api/autostart")
def autostart_status() -> dict[str, bool]:
    from . import desktop
    return {"disponivel": desktop.IS_WINDOWS, "ativo": desktop.autostart_enabled()}


@app.post("/api/autostart")
def autostart_set(body: Autostart) -> dict[str, bool]:
    from . import desktop
    try:
        return {"disponivel": True, "ativo": desktop.set_autostart(body.ativo)}
    except (OSError, RuntimeError) as exc:
        raise HTTPException(400, f"Não consegui alterar o início automático: {exc}")


class Perfil(BaseModel):
    nome: str = ""
    tratamento: str = "senhor"


@app.post("/api/perfil")
def set_profile(body: Perfil) -> dict[str, str]:
    nome = re.sub(r"[^\wÀ-ÿ' -]", "", body.nome).strip()[:40]
    tratamento = re.sub(r"[^\wÀ-ÿ' -]", "", body.tratamento).strip()[:30] or "senhor"
    settings.set_perfil(nome, tratamento)
    return {"nome": nome, "tratamento": tratamento,
            "mensagem": f"Certo, {nome}." if nome else f"Certo, {tratamento}."}


class BrainKey(BaseModel):
    brain: str
    key: str = ""
    model: str = ""


@app.post("/api/models")
def models(body: BrainKey) -> dict[str, Any]:
    """Lista os modelos de chat disponíveis para a chave digitada (ou a já salva)."""
    if body.brain == "ollama":
        try:
            return {"models": list_models("ollama", ""), "atual": settings.ollama_model}
        except (BrainError, httpx.HTTPError):
            raise HTTPException(503, "O Ollama não está rodando neste PC.")
    if body.brain not in BRAIN_ENV:
        raise HTTPException(400, "Cérebro inválido.")
    key = body.key.strip() or settings.brain_key(body.brain)
    if not key and body.brain != "openai":
        raise HTTPException(400, "Informe a chave de API para listar os modelos.")
    try:
        found = list_models(body.brain, key)
    except BrainError as exc:
        raise HTTPException(400, str(exc))
    except httpx.HTTPError as exc:
        raise HTTPException(503, f"Sem conexão com o provedor ({exc.__class__.__name__}).")
    return {"models": found, "atual": getattr(settings, BRAIN_ENV[body.brain][2])}


@app.post("/api/brain-key")
def set_brain_key(body: BrainKey) -> dict[str, Any]:
    """Recebe a chave digitada na interface, testa e grava no .env.
    Sem chave, só troca o modelo de um cérebro já configurado."""
    key = body.key.strip()
    if body.brain == "ollama" and body.model.strip():
        settings.set_brain_model("ollama", body.model.strip())
        return {"ok": True, "validada": True, "mensagem": f"Ollama usando {body.model.strip()}.", **config()}
    if body.brain not in BRAIN_ENV:
        raise HTTPException(400, "Cérebro inválido.")
    if not key and body.model.strip() and settings.brain_key(body.brain):
        settings.set_brain_model(body.brain, body.model.strip())
        log.info("Modelo do cérebro %s trocado para %s", body.brain, body.model.strip())
        return {"ok": True, "validada": True, "mensagem": f"Modelo trocado para {body.model.strip()}.", **config()}
    if len(key) < 10 or any(c.isspace() for c in key):
        raise HTTPException(400, "Chave em formato inválido.")
    ok, msg = validate_key(body.brain, key)
    if ok is False:
        raise HTTPException(400, msg)
    settings.set_brain_key(body.brain, key, body.model.strip())
    log.info("Chave do cérebro %s atualizada pela interface (validada=%s)", body.brain, ok)
    return {"ok": True, "validada": ok, "mensagem": msg, **config()}


class TTSRequest(BaseModel):
    text: str
    engine: str = "local"       # "elevenlabs" (com reserva local) ou "local"
    previous: str = ""          # frase anterior/seguinte: entonação contínua no ElevenLabs
    next: str = ""


class ElevenLabsConfig(BaseModel):
    key: str = ""
    voice_id: str = ""
    model: str = ""


@app.get("/api/voz")
def voice_status() -> dict[str, Any]:
    """Estado dos motores de voz: servidor local (processo separado) e ElevenLabs."""
    try:
        data = httpx.get(settings.voz_url + "/health", timeout=1).json()
        local = {"disponivel": bool(data.get("pronto")), "carregando": not data.get("pronto") and not data.get("erro"),
                 "erro": data.get("erro")}
    except (httpx.HTTPError, ValueError):
        local = {"disponivel": False, "carregando": False, "erro": "servidor de voz desligado"}
    eleven = {"configurado": voice.elevenlabs_configured(), "tem_chave": bool(settings.elevenlabs_key),
              "voice_id": settings.elevenlabs_voice_id, "modelo": settings.elevenlabs_model}
    # "disponivel"/"carregando"/"erro" no topo mantêm compatibilidade com quem só olha a voz local
    return {**local, "local": local, "elevenlabs": eleven}


@app.post("/api/tts")
async def tts(body: TTSRequest) -> Response:
    """Gera a fala e devolve o áudio (mesma origem para o navegador). O cabeçalho X-Voz-Motor
    diz qual motor respondeu; X-Voz-Aviso explica por que o preferido falhou."""
    text = body.text.strip()[:1000]
    if not text:
        raise HTTPException(400, "Texto vazio.")
    try:
        audio, mime, motor, aviso = await voice.synthesize(text, body.engine, body.previous, body.next)
    except voice.VoiceError as exc:
        raise HTTPException(503, str(exc))
    headers = {"X-Voz-Motor": motor}
    if aviso:
        headers["X-Voz-Aviso"] = quote(aviso)
    return Response(audio, media_type=mime, headers=headers)


class SpotifyConfig(BaseModel):
    client_id: str = ""
    client_secret: str = ""


@app.get("/api/spotify")
def spotify_status() -> dict[str, Any]:
    return spotify.status()


@app.post("/api/spotify")
def spotify_save(body: SpotifyConfig) -> dict[str, Any]:
    """Valida Client ID/Secret com o próprio Spotify e grava no .env."""
    cid, secret = body.client_id.strip(), body.client_secret.strip()
    if not re.fullmatch(r"[0-9a-fA-F]{32}", cid) or not re.fullmatch(r"[0-9a-fA-F]{32}", secret):
        raise HTTPException(400, "Client ID e Client Secret têm 32 caracteres (letras a-f e números).")
    ok, msg = spotify.validate_credentials(cid, secret)
    if not ok:
        raise HTTPException(400, msg)
    settings.set_spotify(cid, secret)
    spotify.reset()
    log.info("Spotify configurado pela interface")
    return {"mensagem": "Credenciais salvas. Agora clique em Conectar conta.", **spotify.status()}


class SpotifyControl(BaseModel):
    acao: str
    valor: int | None = None


@app.get("/api/spotify/player")
async def spotify_player() -> dict[str, Any]:
    """Estado do player para o card (capa, música, progresso, aleatório, volume)."""
    return await asyncio.to_thread(spotify.player_state)


@app.get("/api/spotify/letra")
def spotify_letra() -> dict[str, Any]:
    """Letra (sincronizada quando existe) da música que está tocando, para o quadro de letra."""
    return letras.da_musica_atual()


@app.post("/api/spotify/controle")
async def spotify_control(body: SpotifyControl) -> dict[str, Any]:
    acoes = {"alternar", "pausar", "continuar", "proxima", "anterior", "aleatorio", "repetir", "volume", "posicao"}
    if body.acao not in acoes:
        raise HTTPException(400, "Ação desconhecida.")
    r = await asyncio.to_thread(spotify.control, body.acao, body.valor)
    await asyncio.sleep(0.4)                     # o Spotify leva um instante para refletir a mudança
    return {**r, "player": await asyncio.to_thread(spotify.player_state)}


@app.post("/api/spotify/conectar")
def spotify_connect() -> dict[str, Any]:
    try:
        return {"mensagem": spotify.start_login(), **spotify.status()}
    except RuntimeError as exc:
        raise HTTPException(400, str(exc))


@app.get("/api/contas")
async def accounts(fresh: bool = False) -> list[dict[str, Any]]:
    """Estado das contas pessoais (CLIs oficiais): instalada? logada?"""
    return await asyncio.to_thread(contas.todas, fresh)


@app.post("/api/contas/{conta_id}/{acao}")
def account_action(conta_id: str, acao: str) -> dict[str, str]:
    """Abre, numa janela de console visível, a instalação da CLI ou o login no navegador."""
    if conta_id not in contas.CONTAS or acao not in ("instalar", "conectar"):
        raise HTTPException(404, "Ação desconhecida.")
    try:
        msg = contas.abrir_instalacao(conta_id) if acao == "instalar" else contas.abrir_login(conta_id)
    except RuntimeError as exc:
        raise HTTPException(400, str(exc))
    log.info("Conta %s: %s", conta_id, acao)
    return {"mensagem": msg}


@app.post("/api/elevenlabs/opcoes")
async def elevenlabs_options(body: ElevenLabsConfig) -> dict[str, Any]:
    """Vozes e modelos da conta (com a chave digitada ou a já salva)."""
    key = body.key.strip() or settings.elevenlabs_key
    if not key:
        raise HTTPException(400, "Informe a chave de API do ElevenLabs.")
    try:
        voices = await voice.list_voices(key)
        try:
            models = await voice.list_models(key)
        except voice.VoiceError:          # chaves com permissão restrita podem não listar modelos
            models = []
    except voice.VoiceError as exc:
        raise HTTPException(400, str(exc))
    return {"vozes": voices, "modelos": models, "voice_id": settings.elevenlabs_voice_id,
            "modelo": settings.elevenlabs_model}


@app.post("/api/elevenlabs")
async def elevenlabs_save(body: ElevenLabsConfig) -> dict[str, Any]:
    """Testa a chave + voz e grava no .env. Campos vazios mantêm o valor já salvo."""
    key = body.key.strip() or settings.elevenlabs_key
    voice_id = body.voice_id.strip() or settings.elevenlabs_voice_id
    if not key or len(key) < 10 or any(c.isspace() for c in key):
        raise HTTPException(400, "Chave de API do ElevenLabs inválida.")
    if not voice_id:
        raise HTTPException(400, "Escolha a voz (Voice ID).")
    try:
        await voice.check_voice(key, voice_id)
        await voice.test_tts(key, voice_id, body.model.strip() or settings.elevenlabs_model)
    except voice.VoiceError as exc:
        raise HTTPException(400, str(exc))
    settings.set_elevenlabs(body.key.strip(), voice_id, body.model.strip())
    log.info("ElevenLabs configurado pela interface (voz %s, modelo %s)", voice_id, settings.elevenlabs_model)
    return {"ok": True, "mensagem": "ElevenLabs conectado.", **voice_status()}


@app.get("/api/stats")
def stats() -> dict[str, Any]:
    return sm.snapshot()


@app.get("/api/processes")
def processes(sort: str = "cpu", limit: int = 15, filtro: str = "") -> list[dict[str, Any]]:
    return sm.list_processes(sort, limit, filtro)


@app.get("/api/logs")
def logs(limit: int = 20) -> list[dict[str, Any]]:
    return recent_actions(limit)


@app.post("/api/report")
def make_report() -> dict[str, str]:
    return {"arquivo": str(report.generate_report())}


# --------------------------------------------------------------------------- integrações e cards
class IntegracaoConfig(BaseModel):
    campos: dict[str, str] = {}


@app.get("/api/integracoes")
def integracoes_status() -> dict[str, Any]:
    return {"integracoes": integracoes.status()}


@app.post("/api/integracoes/{iid}")
def integracao_salvar(iid: str, body: IntegracaoConfig) -> dict[str, Any]:
    """Grava os campos da integração (token, endereço iCal, senha de app...) e testa a conexão."""
    try:
        r = integracoes.configurar(iid, body.campos)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {**r, "integracoes": integracoes.status()}


class Contato(BaseModel):
    nome: str = ""
    numero: str = ""


@app.get("/api/whatsapp")
def whatsapp_status() -> dict[str, Any]:
    from . import whatsapp
    ponte = whatsapp.PONTE
    nav = ponte.info.get("navegador", "")
    return {"modo": "app" if settings.whatsapp_modo == "app" else "web", "usando": whatsapp.modo(),
            "extensao": {"conectada": ponte.conectada(), "versao": ponte.info.get("versao"),
                         "versao_atual": whatsapp.versao_extensao(),
                         "navegador": "Edge" if "Edg/" in nav else "Chrome" if nav else "",
                         "pasta": str(whatsapp.EXTENSAO_DESTINO)},
            "contatos": whatsapp.contato("listar")["contatos"]}


@app.websocket("/ws/whatsapp")
async def ws_whatsapp(ws: WebSocket) -> None:
    """Conexão da extensão do JARVIS no navegador (só aceita origem de extensão)."""
    from . import whatsapp
    if not ws.headers.get("origin", "").startswith(("chrome-extension://", "extension://")):
        await ws.close(code=1008)
        return
    await ws.accept()
    whatsapp.PONTE.ligar(ws, asyncio.get_running_loop())

    async def pings() -> None:                        # mantém o service worker da extensão acordado
        while True:
            await asyncio.sleep(20)
            await ws.send_json({"type": "ping"})

    tarefa = asyncio.create_task(pings())
    try:
        while True:
            msg = await ws.receive_json()
            if msg.get("type") == "ola":
                whatsapp.PONTE.info = {"versao": msg.get("versao"), "navegador": msg.get("navegador", "")[:200]}
                log.info("Extensão do WhatsApp conectada (versão %s)", msg.get("versao"))
            elif msg.get("type") == "resultado":
                whatsapp.PONTE.resultado(msg)
    except WebSocketDisconnect:
        pass
    except Exception:
        log.exception("Erro na conexão da extensão do WhatsApp")
    finally:
        tarefa.cancel()
        whatsapp.PONTE.desligar(ws)


@app.post("/api/whatsapp/extensao")
def whatsapp_extensao() -> dict[str, Any]:
    from . import whatsapp
    try:
        return whatsapp.instalar_extensao()
    except OSError as exc:
        raise HTTPException(500, f"Não consegui preparar a extensão: {exc}")


@app.post("/api/whatsapp/modo")
def whatsapp_modo(body: dict[str, str]) -> dict[str, Any]:
    if body.get("modo") not in ("auto", "app", "web"):
        raise HTTPException(400, "Modo inválido: web ou app.")
    settings.set_vars({"WHATSAPP_MODO": body["modo"]})
    return whatsapp_status()


@app.post("/api/whatsapp/contatos")
def whatsapp_salvar(body: Contato) -> dict[str, Any]:
    from . import whatsapp
    r = whatsapp.salvar_contato(body.nome, body.numero)
    if not r["ok"]:
        raise HTTPException(400, r["mensagem"])
    return whatsapp_status()


@app.delete("/api/whatsapp/contatos/{nome}")
def whatsapp_remover(nome: str) -> dict[str, Any]:
    from . import whatsapp
    whatsapp.remover_contato(nome)
    return whatsapp_status()


@app.delete("/api/integracoes/{iid}")
def integracao_desconectar(iid: str) -> dict[str, Any]:
    try:
        integracoes.desconectar(iid)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"ok": True, "integracoes": integracoes.status()}


def _app_painel(app: str) -> dict[str, Any]:
    from .embutidos import EMBUTIDOS
    return EMBUTIDOS.painel(app)


@app.get("/api/painel/{nome}")
def painel(nome: str) -> dict[str, Any]:
    """Dados dos cards: agenda, email, github, docker, alertas, servicos."""
    fontes = {"agenda": lambda: integracoes.agenda(7), "email": lambda: integracoes.email(6),
              "github": integracoes.github, "docker": integracoes.docker, "alertas": integracoes.alertas,
              "servicos": integracoes.servicos, "clima": lambda: _clima_cacheado(),
              "whatsapp": lambda: _app_painel("whatsapp"), "instagram": lambda: _app_painel("instagram"),
              "facebook": lambda: _app_painel("facebook")}
    if nome not in fontes:
        raise HTTPException(404, "Card desconhecido.")
    return fontes[nome]()


_clima: dict[str, Any] = {"em": 0.0, "dados": None}


def _clima_cacheado() -> dict[str, Any]:
    """Clima da barra inferior (cache de 20 min; não aparece como ação no log)."""
    from . import web_tools
    if not _clima["dados"] or time.monotonic() - _clima["em"] > 1200:
        dados = web_tools.previsao_tempo("", 3)
        if dados.get("ok"):
            _clima.update(em=time.monotonic(), dados=dados)
        return dados
    return _clima["dados"]


@app.get("/api/apps/janelas")
def apps_janelas() -> dict[str, Any]:
    from . import apps
    return apps.listar()


@app.get("/api/apps/ler")
def apps_ler(janela: str, limite: int = 80) -> dict[str, Any]:
    """Só leitura: os controles de uma janela (ajuda a conferir o controle de apps)."""
    from . import apps
    return apps.ler(janela, limite)


@app.get("/api/cards")
def cards_layout() -> dict[str, Any]:
    return {"catalogo": cards.CATALOGO, "layout": cards.carregar()}


@app.post("/api/cards")
def cards_salvar(layout: dict[str, Any]) -> dict[str, Any]:
    """A interface avisa o layout atual (arrastar, fixar, ocultar) para o JARVIS saber onde está cada card."""
    if layout.get("restaurar"):
        return {"layout": cards.salvar(cards.PADRAO)}
    return {"layout": cards.salvar(layout)}


# Janelas da interface conectadas agora (o .exe se encerra quando todas fecham)
clients = {"ativos": 0, "ultimo_em": 0.0}
# Sessões abertas, da mais antiga para a mais nova: (loop, send, assistant) — para o JARVIS falar sozinho
_sessoes: list[tuple[asyncio.AbstractEventLoop, Any, Assistant]] = []


def anunciar(texto: str, contexto: str) -> bool:
    """Faz o JARVIS falar por iniciativa própria na janela mais recente (ex.: boas-vindas)."""
    if not _sessoes:
        log.info("Nada a anunciar (nenhuma janela aberta): %s", texto)
        return False
    loop, send, assistant = _sessoes[-1]
    assistant.announce(texto, contexto)
    asyncio.run_coroutine_threadsafe(send({"type": "reply", "text": texto, "brain": assistant.brain_name}), loop)
    return True


# O aplicativo (.exe) registra aqui como mostrar a janela (inclusive a que está oculta na bandeja)
ganchos: dict[str, Any] = {}


@app.post("/api/mostrar")
def mostrar_janela() -> dict[str, bool]:
    """Chamado quando você abre o JARVIS.exe de novo: mostra a janela que já existe."""
    abrir = ganchos.get("mostrar")
    if abrir is None:
        raise HTTPException(404, "Sem janela do aplicativo para mostrar.")
    abrir()
    return {"ok": True}


def oferecer_report() -> bool:
    """Chamado quando o StatusRobot termina: o JARVIS fala e já deixa o envio pronto para o "sim"."""
    if not _sessoes:
        return False
    _loop, _send, assistant = _sessoes[-1]
    return anunciar(assistant.oferecer_report(), "O StatusRobot terminou o status report do dia.")


def iniciar_vigias() -> None:
    """Tarefas de fundo do aplicativo (não rodam nos testes)."""
    from . import trabalho
    trabalho.ao_ficar_pronto = oferecer_report
    from . import whatsapp
    try:
        whatsapp.preparar_pasta_extensao()          # a pasta da extensão sempre existe e está atualizada
    except OSError as exc:
        log.warning("Não consegui preparar a pasta da extensão do WhatsApp: %s", exc)
    boas_vindas.vigiar_bloqueio(anunciar)
    import threading
    from .embutidos import EMBUTIDOS
    EMBUTIDOS.ao_avisar = avisar_app
    threading.Thread(target=EMBUTIDOS.iniciar, daemon=True).start()   # reabre (escondidos) os apps já usados


@app.websocket("/ws")
async def websocket(ws: WebSocket) -> None:
    if not _origem_local(ws.headers.get("origin", "")):
        await ws.close(code=1008)                      # outro site tentando falar com o JARVIS
        return
    await ws.accept()
    clients["ativos"] += 1
    try:
        await _session(ws)
    finally:
        clients["ativos"] -= 1
        clients["ultimo_em"] = time.monotonic()


async def _session(ws: WebSocket) -> None:
    loop = asyncio.get_running_loop()
    send_lock = asyncio.Lock()

    async def send(data: dict[str, Any]) -> None:
        async with send_lock:
            await ws.send_json(data)

    def emit(event: dict[str, Any]) -> None:
        # Chamado a partir da thread do cérebro: agenda o envio no loop do servidor
        asyncio.run_coroutine_threadsafe(send(event), loop)

    assistant = Assistant(emit)
    await send({"type": "hello", "brain": assistant.brain_name, "label": assistant.brain_label})
    sessao = (loop, send, assistant)
    _sessoes.append(sessao)

    async def stats_loop() -> None:
        while True:
            data = await asyncio.to_thread(sm.snapshot)
            await send({"type": "stats", "data": data})
            await asyncio.sleep(STATS_INTERVAL)

    busy = asyncio.Lock()

    async def handle_chat(text: str) -> None:
        # Uma pergunta por vez; o loop de recepção continua livre (ex.: para cliques de confirmação)
        async with busy:
            await send({"type": "state", "value": "thinking"})
            try:
                reply = await asyncio.to_thread(assistant.chat, text)
                await send({"type": "reply", "text": reply, "brain": assistant.brain_name})
            except Exception as exc:
                log.exception("Erro ao processar mensagem")
                await send({"type": "warning", "mensagem": str(exc)})
            finally:
                await send({"type": "state", "value": "idle"})

    stats_task = asyncio.create_task(stats_loop())
    tasks: set[asyncio.Task] = set()
    try:
        while True:
            msg = await ws.receive_json()
            kind = msg.get("type")
            if kind == "chat":
                task = asyncio.create_task(handle_chat(str(msg.get("text", ""))))
                tasks.add(task)
                task.add_done_callback(tasks.discard)
            elif kind == "confirm":
                reply = await asyncio.to_thread(assistant.resolve, msg.get("id"), bool(msg.get("approve")))
                await send({"type": "reply", "text": reply, "brain": assistant.brain_name})
            elif kind == "set_brain":
                wanted = str(msg.get("brain", "local"))
                name = assistant.set_brain(wanted)
                if name == wanted and settings.brain != name:
                    settings.set_vars({"JARVIS_BRAIN": name})      # volta com ele na próxima abertura
                    log.info("Cérebro padrão agora: %s", name)
                if name != wanted:
                    await send({"type": "warning", "mensagem": f"{LABELS.get(wanted, wanted)} não está configurado. "
                                                              "Adicione a chave em ⚙ Configurar cérebros."})
                await send({"type": "hello", "brain": name, "label": assistant.brain_label})
            elif kind == "greet":
                texto = boas_vindas.inicio()
                assistant.announce(texto, "O JARVIS acabou de iniciar e cumprimentou o usuário.")
                await send({"type": "reply", "text": texto, "brain": assistant.brain_name})
            elif kind == "flutuante":
                await _para_flutuantes({k: v for k, v in msg.items() if k != "type"})
            elif kind == "cliente":
                log.info("Interface: %s", str(msg.get("texto", ""))[:300])
            elif kind == "reset":
                assistant.reset()
                await send({"type": "reply", "text": "Memória da conversa apagada.", "brain": assistant.brain_name})
    except WebSocketDisconnect:
        pass
    except Exception:
        log.exception("Erro no WebSocket")
    finally:
        stats_task.cancel()
        if sessao in _sessoes:
            _sessoes.remove(sessao)


# Janelinha flutuante: é outro processo do navegador, então a conversa com a janela principal passa por aqui.
_flutuantes: set[WebSocket] = set()


async def _para_flutuantes(msg: dict[str, Any]) -> None:
    for fws in list(_flutuantes):
        try:
            await fws.send_json(msg)
        except Exception:
            _flutuantes.discard(fws)


async def _para_principal(msg: dict[str, Any]) -> None:
    if _sessoes:
        await _sessoes[-1][1]({**msg, "type": "flutuante"})


@app.websocket("/ws/flutuante")
async def ws_flutuante(ws: WebSocket) -> None:
    """Janelinha flutuante: recebe o estado do JARVIS e manda os pedidos (escrever, microfone, voz)."""
    if not _origem_local(ws.headers.get("origin", "")):
        await ws.close(code=1008)
        return
    await ws.accept()
    _flutuantes.add(ws)
    # Arrastando, chegam dezenas de recortes por segundo: aplica só o mais recente (os velhos já não valem)
    pendente: dict[str, Any] = {}
    aplicando: list[asyncio.Task] = []

    async def aplicar_ultimo() -> None:
        while pendente:
            m = pendente.pop("regiao")
            await asyncio.to_thread(_aplicar_regiao, m.get("formas"), m.get("dpr"), m.get("vista"), m.get("redesenhar", True))

    try:
        await _para_principal({"tipo": "ola"})
        while True:
            msg = await ws.receive_json()
            if msg.get("tipo") == "regiao":            # arrastando uma peça: muitas por segundo, sem HTTP
                pendente["regiao"] = msg
                if not aplicando or aplicando[0].done():
                    aplicando[:] = [asyncio.create_task(aplicar_ultimo())]
            elif msg.get("tipo") == "app_mover":       # arrastando um card de app: move na hora (sem fila)
                from .embutidos import EMBUTIDOS
                try:
                    EMBUTIDOS.mover_rapido("flutuante", str(msg.get("app", "")), [float(v) for v in msg.get("rect", [])[:4]],
                                           float(msg.get("dpr") or 1))
                except (TypeError, ValueError, IndexError):
                    pass
            elif msg.get("tipo") == "apps":            # vagas dos apps nos cards da janelinha
                from .embutidos import EMBUTIDOS
                vista = msg.get("vista") or [0, 0]
                await asyncio.to_thread(EMBUTIDOS.encaixar_varios, "flutuante", msg.get("slots") or {}, msg.get("dpr") or 1,
                                        vista if len(vista) >= 2 else [0, 0], True)
            elif msg.get("tipo") == "fechar_card_app":
                await _para_principal({"tipo": "fechar_card_app", "id": str(msg.get("id", ""))[:40]})
            elif msg.get("tipo") in ("ola", "enviar", "mic", "voz", "confirmar", "abrir_web"):
                url = str(msg.get("url", ""))
                await _para_principal({"tipo": msg["tipo"], "texto": str(msg.get("texto", ""))[:2000],
                                       "ligado": bool(msg.get("ligado")), "aprovar": bool(msg.get("aprovar")),
                                       "url": url if url.startswith(("http://", "https://")) else ""})
    except WebSocketDisconnect:
        pass
    except Exception:
        log.exception("Erro no WebSocket da janelinha flutuante")
    finally:
        _flutuantes.discard(ws)


def run() -> None:
    import uvicorn
    log.info("JARVIS iniciando em http://%s:%s (PID %s)", settings.host, settings.port, os.getpid())
    iniciar_vigias()
    uvicorn.run(app, host=settings.host, port=settings.port, log_level="warning")
