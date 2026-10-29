"""Integração com o Spotify.

Modo completo (recomendado): Spotify Web API via spotipy, com SPOTIPY_CLIENT_ID e
SPOTIPY_CLIENT_SECRET no .env. Permite buscar e tocar músicas, playlists e artistas,
ver o que está tocando e ajustar o volume (controle de reprodução exige Spotify Premium).

Modo básico (sem credenciais): abre o app do Spotify na busca e usa as teclas de mídia.
"""
from __future__ import annotations

import os
import shutil
import socket
import time
import urllib.parse
import webbrowser
from typing import Any

from . import os_control
from .config import settings
from .logger import log

_client = None
_client_error = ""
_auth = None
_login = {"em_andamento": False, "erro": ""}
SCOPE = "user-read-playback-state user-modify-playback-state user-read-currently-playing"


def _token_path():
    return settings.data_dir / ".spotify_token"


def _auth_manager():
    global _auth
    if _auth is None:
        from spotipy.oauth2 import SpotifyOAuth

        settings.ensure_dirs()
        _auth = SpotifyOAuth(client_id=settings.spotify_client_id, client_secret=settings.spotify_client_secret,
                             redirect_uri=settings.spotify_redirect_uri, scope=SCOPE,
                             cache_path=str(_token_path()), open_browser=True)
    return _auth


def _get_client():
    global _client, _client_error
    if _client is not None:
        return _client
    if not is_configured():
        _client_error = "Spotify não configurado (⚙ Configurações → Spotify)."
        return None
    if not is_connected():
        # Sem login feito, o spotipy abriria o navegador no meio de um comando de voz; pedimos pela tela
        _client_error = "Conta Spotify não conectada (⚙ Configurações → Spotify → Conectar conta)."
        return None
    try:
        import spotipy
    except ImportError:
        _client_error = "Pacote 'spotipy' não instalado."
        return None
    _client = spotipy.Spotify(auth_manager=_auth_manager(), requests_timeout=8)
    return _client


def is_configured() -> bool:
    return bool(settings.spotify_client_id and settings.spotify_client_secret)


def is_connected() -> bool:
    return is_configured() and _token_path().is_file()


def reset() -> None:
    """Esquece cliente e login em memória (depois de trocar Client ID/Secret)."""
    global _client, _auth
    _client, _auth = None, None
    _token_path().unlink(missing_ok=True)


def validate_credentials(client_id: str, client_secret: str) -> tuple[bool, str]:
    """Confere Client ID/Secret pedindo um token de aplicativo ao Spotify (não precisa de login)."""
    import httpx

    try:
        r = httpx.post("https://accounts.spotify.com/api/token", data={"grant_type": "client_credentials"},
                       auth=(client_id, client_secret), timeout=10)
    except httpx.HTTPError as exc:
        return False, f"Sem conexão com o Spotify ({exc.__class__.__name__})."
    if r.status_code == 200:
        return True, "Credenciais válidas."
    return False, "O Spotify recusou o Client ID/Secret. Confira se copiou os dois do mesmo app."


def start_login() -> str:
    """Abre o login do Spotify no navegador e espera o retorno em 127.0.0.1:8888 (numa thread)."""
    if not is_configured():
        raise RuntimeError("Salve o Client ID e o Client Secret primeiro.")
    if _login["em_andamento"]:
        return "O login já está aberto no navegador."

    def run():
        _login.update(em_andamento=True, erro="")
        try:
            _auth_manager().get_access_token(as_dict=False)   # abre o navegador e captura o código
        except Exception as exc:
            _login["erro"] = str(exc)[:200]
            log.warning("Login do Spotify falhou: %s", exc)
        finally:
            _login["em_andamento"] = False

    import threading
    threading.Thread(target=run, daemon=True).start()
    return "Abri o Spotify no navegador: entre na sua conta e clique em Concordar."


def status() -> dict[str, Any]:
    return {"configurado": is_configured(), "conectado": is_connected(), "redirect_uri": settings.spotify_redirect_uri,
            "client_id": settings.spotify_client_id[:6] + "…" if settings.spotify_client_id else "",
            "login_em_andamento": _login["em_andamento"], "erro": _login["erro"]}


# --------------------------------------------------------------------------- app do PC
def _web_url(uri: str) -> str:
    """spotify:track:ID -> https://open.spotify.com/track/ID"""
    partes = uri.split(":")
    return "https://open.spotify.com/" + ("/".join(partes[1:3]) if len(partes) >= 3 else "")


def desktop_app_available() -> bool:
    """Existe um app do Spotify registrado para links spotify:? (instalador normal e versão da Store).

    Não dá para descobrir tentando abrir: sem app, o Windows não dá erro — mostra "Escolher um
    aplicativo" ou o convite da Microsoft Store. Por isso olhamos o registro."""
    if os.name != "nt":
        return bool(shutil.which("spotify"))
    return os_control.protocol_registered("spotify")


def _spotify_process_running() -> bool:
    import psutil
    return any((p.info["name"] or "").lower() in ("spotify.exe", "spotify")
               for p in psutil.process_iter(["name"]))


def _wake_desktop_app(uri: str = "") -> str:
    """Abre a música no app do Spotify do PC (com uma faixa, o app já toca). Se o PC não tem o app,
    abre no Spotify Web Player. Retorna "app" ou "web"."""
    if desktop_app_available():
        try:
            os_control._launch_target(uri or "spotify:")
            # Registro pode ter sobrado de uma desinstalação: confere se o app abriu de fato
            for _ in range(12):
                if _spotify_process_running():
                    return "app"
                time.sleep(0.5)
        except OSError:
            pass
    webbrowser.open(_web_url(uri))
    return "web"


def _is_premium_error(exc: Exception) -> bool:
    text = str(exc).upper()
    return "PREMIUM_REQUIRED" in text or ("403" in text and "PREMIUM" in text)


# --------------------------------------------------------------------------- reprodução
def _first_track(sp, kind: str, item: dict) -> str | None:
    """Primeira faixa de um álbum/playlist/artista (para iniciar a sessão no app do PC)."""
    try:
        if kind == "album":
            tracks = sp.album_tracks(item["id"], limit=1, market="BR").get("items", [])
        elif kind == "playlist":
            tracks = [i.get("track") or i.get("item") for i in sp.playlist_items(item["id"], limit=1, market="BR")
                      .get("items", [])]
        else:  # artista
            tracks = sp.search(q=f'artist:"{item["name"]}"', type="track", limit=1, market="BR")["tracks"]["items"]
    except Exception:
        return None
    tracks = [t for t in tracks if t and t.get("uri", "").startswith("spotify:track:")]
    return tracks[0]["uri"] if tracks else None


def _wait_playing(sp, track_uri: str, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        time.sleep(0.8)
        cur = sp.current_playback() or {}
        if cur.get("is_playing") and (cur.get("item") or {}).get("uri") == track_uri:
            return True
    return False


def _queue_and_skip(sp, track_uri: str) -> bool:
    """Põe a faixa na fila e pula para ela (funciona mesmo onde a API ignora "tocar X")."""
    try:
        sp.add_to_queue(track_uri)
        sp.next_track()
        if _wait_playing(sp, track_uri, 4):
            return True
        cur = sp.current_playback() or {}
        if (cur.get("item") or {}).get("uri") == track_uri and not cur.get("is_playing"):
            sp.start_playback()                          # estava pausado: pula e fica parado; retoma
            return _wait_playing(sp, track_uri, 3)
    except Exception as exc:
        log.info("Fila + próxima falhou: %s", exc)
    return False


def _reuse_open_device(sp) -> dict | None:
    """Ativa um Spotify que já está aberto (prefere o Web Player/este PC) e devolve a sessão, se surgir."""
    try:
        devices = [d for d in sp.devices().get("devices", []) if not d.get("is_restricted")]
    except Exception:
        return None
    if not devices:
        return None
    host = socket.gethostname().lower()
    devices.sort(key=lambda d: (d.get("is_active"), "web player" in d.get("name", "").lower(),
                                d.get("name", "").lower() == host, d.get("type") == "Computer"), reverse=True)
    try:
        sp.transfer_playback(devices[0]["id"], force_play=True)
    except Exception as exc:
        log.info("Transferir para %s falhou: %s", devices[0].get("name"), exc)
        return None
    for _ in range(6):
        time.sleep(0.7)
        cur = sp.current_playback() or {}
        if cur.get("item"):
            return cur
    return None


def _play_track(sp, track_uri: str) -> bool | str:
    """Toca uma faixa e confirma. Testado numa conta em que a Web API ignora "tocar X":
    - já tocando (em qualquer dispositivo: PC, celular, web): fila + próxima troca na hora;
    - Spotify parado: abrir a faixa no app do PC cria a sessão e já toca;
    - PC sem o app: abre no Web Player; o navegador exige um clique no play da 1ª vez.
    Retorna True (tocando), False (não confirmou) ou "web" (aberto no Web Player, aguardando o play)."""
    cur = sp.current_playback() or {}
    if not cur.get("item"):
        # Nenhuma sessão carregada, mas pode haver um Spotify aberto (guia do Web Player, app, celular):
        # reaproveita ele em vez de abrir outra guia/janela
        cur = _reuse_open_device(sp) or {}
    if cur.get("item"):
        if _queue_and_skip(sp, track_uri):
            return True
        try:
            sp.pause_playback()
        except Exception:
            pass
    onde = _wake_desktop_app(track_uri)
    if onde == "web":
        # O Web Player abre a página da música, mas o navegador só toca depois de um clique
        return True if _wait_playing(sp, track_uri, 3) else "web"
    return _wait_playing(sp, track_uri, 8)


def play(consulta: str = "", tipo: str = "musica") -> dict[str, Any]:
    sp = _get_client()
    if sp is None:
        return _fallback_play(consulta)
    try:
        if not consulta:
            return control("continuar")

        kind = {"musica": "track", "playlist": "playlist", "artista": "artist", "album": "album"}.get(tipo, "track")
        res = sp.search(q=consulta, type=kind, limit=1, market="BR")
        items = [i for i in res.get(kind + "s", {}).get("items", []) if i]
        if not items:
            return {"ok": False, "mensagem": f"Não encontrei '{consulta}' no Spotify."}
        item = items[0]
        desc = (f"{item['name']}, de {', '.join(a['name'] for a in item['artists'])}" if kind == "track"
                else {"playlist": "a playlist ", "album": "o álbum ", "artist": ""}[kind] + item["name"])
        # A Web API aceita "tocar esta música" mas, em contas grátis/apps em modo de desenvolvimento,
        # ignora o pedido; por isso _play_track usa o app do PC e a fila (ver lá)
        faixa = item["uri"] if kind == "track" else _first_track(sp, kind, item)
        if not faixa:
            _wake_desktop_app(item["uri"])
            return {"ok": False, "mensagem": f"Abri {desc} no app do Spotify; o Spotify não me deixa ver as faixas "
                                             "dessa lista, então dê o play por lá."}
        resultado = _play_track(sp, faixa)
        if resultado == "web":
            return {"ok": True, "player": True,
                    "mensagem": f"Este PC não tem o app do Spotify, então abri {desc} no Spotify Web. Clique no play "
                                "uma vez no navegador; depois disso eu controlo por aqui."}
        if not resultado:
            return {"ok": False, "mensagem": f"Abri {desc} no app do Spotify, mas não consegui confirmar que "
                                             "começou a tocar. Confira se o app está aberto e logado."}
        if kind != "track":
            nome = ((sp.current_playback() or {}).get("item") or {}).get("name", "")
            desc += f", começando por {nome}" if nome else ""
        return {"ok": True, "mensagem": f"Tocando {desc}.", "uri": item["uri"], "player": True}
    except Exception as exc:  # spotipy levanta SpotifyException (sem Premium, sem dispositivo, etc.)
        log.warning("Spotify play falhou: %s", exc)
        if _is_premium_error(exc):
            if consulta:
                _fallback_play(consulta)
            return {"ok": False, "mensagem": "Controlar a reprodução pelo JARVIS exige Spotify Premium. "
                                             "Abri a busca no app para você dar o play."}
        return {"ok": False, "mensagem": f"O Spotify recusou o comando: {str(exc)[:200]}"}


def _fallback_play(consulta: str) -> dict[str, Any]:
    if consulta:
        uri = "spotify:search:" + urllib.parse.quote(consulta)
        try:
            if not desktop_app_available():
                raise OSError("sem app do Spotify")
            os_control._launch_target(uri)
        except OSError:
            os_control.open_website("https://open.spotify.com/search/" + urllib.parse.quote(consulta))
        return {"ok": True, "mensagem": f"Abri a busca por '{consulta}' no Spotify. "
                                        "Conecte o Spotify em ⚙ Configurações para eu dar o play sozinho."}
    os_control.media_control("play_pause")
    return {"ok": True, "mensagem": "Play/pause enviado."}


def control(acao: str, valor: int | None = None) -> dict[str, Any]:
    """acao: pausar | continuar | alternar | proxima | anterior | aleatorio | repetir | volume | posicao"""
    sp = _get_client()
    if sp is None:
        key = {"pausar": "play_pause", "continuar": "play_pause", "alternar": "play_pause",
               "proxima": "proxima", "anterior": "anterior"}.get(acao)
        if not key:
            return {"ok": False, "mensagem": _client_error}
        return os_control.media_control(key)
    try:
        cur = sp.current_playback() or {}
        if acao == "alternar":
            acao = "pausar" if cur.get("is_playing") else "continuar"
        if acao in ("pausar", "continuar", "proxima", "anterior") and not cur.get("item"):
            # Nenhuma sessão (app parado ou em outro lugar): as teclas de mídia controlam o app do PC
            key = {"pausar": "play_pause", "continuar": "play_pause", "proxima": "proxima", "anterior": "anterior"}[acao]
            os_control.media_control(key)
            return {"ok": True, "mensagem": "Comando enviado ao app do Spotify.", "player": True}
        if acao == "pausar":
            sp.pause_playback()
        elif acao == "continuar":
            sp.start_playback()
        elif acao == "proxima":
            sp.next_track()
        elif acao == "anterior":
            sp.previous_track()
        elif acao == "aleatorio":
            sp.shuffle(not (sp.current_playback() or {}).get("shuffle_state", False))
        elif acao == "repetir":
            atual = (sp.current_playback() or {}).get("repeat_state", "off")
            sp.repeat({"off": "context", "context": "track", "track": "off"}[atual])
        elif acao == "volume" and valor is not None:
            sp.volume(max(0, min(100, int(valor))))
        elif acao == "posicao" and valor is not None:
            sp.seek_track(max(0, int(valor)))
        else:
            return {"ok": False, "mensagem": f"Ação desconhecida: {acao}"}
        nomes = {"pausar": "Pausado", "continuar": "Retomando", "proxima": "Próxima música", "anterior": "Música anterior",
                 "aleatorio": "Modo aleatório alternado", "repetir": "Modo repetir alternado",
                 "volume": f"Volume em {valor}%", "posicao": "Posição ajustada"}
        return {"ok": True, "mensagem": nomes.get(acao, "Feito") + ".", "player": True}
    except Exception as exc:
        # A API recusou (conta grátis/restrição): as teclas de mídia controlam o app do PC mesmo assim
        tecla = {"pausar": "play_pause", "continuar": "play_pause", "proxima": "proxima", "anterior": "anterior"}.get(acao)
        if tecla:
            os_control.media_control(tecla)
            return {"ok": True, "mensagem": "Comando enviado ao app do Spotify.", "player": True}
        if _is_premium_error(exc) or "restriction" in str(exc).lower():
            return {"ok": False, "mensagem": "O Spotify não permite essa ação pela sua conta (exige Premium)."}
        return {"ok": False, "mensagem": f"O Spotify recusou o comando: {str(exc)[:200]}"}


def player_state() -> dict[str, Any]:
    """Estado completo do player, usado pelo card do Spotify na interface."""
    if not is_connected():
        return {"conectado": False}
    sp = _get_client()
    if sp is None:
        return {"conectado": False, "erro": _client_error}
    try:
        cur = sp.current_playback()
    except Exception as exc:
        return {"conectado": True, "ativo": False, "erro": str(exc)[:200]}
    if not cur or not cur.get("item"):
        return {"conectado": True, "ativo": False}
    item, dev = cur["item"], cur.get("device") or {}
    imagens = (item.get("album") or {}).get("images") or (item.get("images") or [])
    return {
        "conectado": True, "ativo": True,
        "tocando": bool(cur.get("is_playing")),
        "musica": item.get("name"),
        "artistas": ", ".join(a["name"] for a in item.get("artists", [])) or (item.get("show") or {}).get("name", ""),
        "album": (item.get("album") or {}).get("name", ""),
        "imagem": (imagens[min(1, len(imagens) - 1)]["url"] if imagens else ""),
        "progresso_ms": cur.get("progress_ms") or 0,
        "duracao_ms": item.get("duration_ms") or 0,
        "aleatorio": bool(cur.get("shuffle_state")),
        "repetir": cur.get("repeat_state", "off"),
        "volume": dev.get("volume_percent"),
        "dispositivo": dev.get("name", ""),
        "link": (item.get("external_urls") or {}).get("spotify", ""),
    }


def now_playing() -> dict[str, Any]:
    s = player_state()
    if not s.get("conectado"):
        return {"ok": False, "mensagem": s.get("erro") or _client_error or "Spotify não conectado."}
    if not s.get("ativo"):
        return {"ok": True, "tocando": False, "mensagem": "Nada tocando no momento."}
    return {"ok": True, "tocando": s["tocando"], "musica": s["musica"], "artistas": s["artistas"],
            "album": s["album"], "volume": s["volume"], "dispositivo": s["dispositivo"]}


def volume(percent: int) -> dict[str, Any]:
    return control("volume", max(0, min(100, int(percent))))

