"""Modo aplicativo (usado pelo JARVIS.exe): sobe o servidor e abre a interface numa janela própria.

A janela é o Edge (ou Chrome) em modo aplicativo, com um perfil só do JARVIS em
%APPDATA%\\JARVIS\\navegador — assim a permissão do microfone fica salva e o processo da janela
fica sob controle do JARVIS. Fechar a janela não desliga o JARVIS: ele reabre a interface escondida
e continua na bandeja, ouvindo "Jarvis" e falando; "Sair" na bandeja encerra tudo.
"""
from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path


# Segundos sem nenhuma janela conectada para considerar que ela foi fechada (F5 reconecta em ~1 s)
CLOSE_GRACE = 4


def _port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) != 0


def _jarvis_running(port: int) -> bool:
    import httpx
    try:
        r = httpx.get(f"http://127.0.0.1:{port}/health", timeout=1)
        return r.status_code == 200 and r.json().get("status") == "online"
    except (httpx.HTTPError, ValueError):
        return False


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def browsers() -> list[str]:
    """Navegadores para a janela do JARVIS: Google Chrome primeiro (reconhecimento de voz do Google, padrão no
    trabalho), Microsoft Edge como reserva (vem em todo Windows)."""
    from .config import settings
    if os.name == "nt":
        bases = [os.environ.get(v) for v in ("LOCALAPPDATA", "PROGRAMFILES", "PROGRAMFILES(X86)")]
        out = []
        for rel in ("Google/Chrome/Application/chrome.exe", "Microsoft/Edge/Application/msedge.exe"):
            achado = next((str(Path(b) / rel) for b in bases if b and (Path(b) / rel).is_file()), None)
            if achado:
                out.append(achado)
    else:
        out = [shutil.which(n) for n in ("google-chrome", "chromium", "chromium-browser", "microsoft-edge") if shutil.which(n)]
    if settings.navegador == "edge":                  # escolhido nas Configurações: Edge primeiro, Chrome de reserva
        out.sort(key=lambda exe: "edge" not in exe.lower())
    return out


def tipo_navegador(exe: str | None) -> str:
    return "edge" if exe and "edge" in exe.lower() else "chrome" if exe else ""


def _browser() -> str | None:
    lista = browsers()
    return lista[0] if lista else None


def perfil(base: Path, exe: str | None) -> Path:
    """Cada navegador com a sua pasta de perfil (misturar Chrome e Edge na mesma pasta corrompe o perfil).
    O Edge fica com a pasta original, que já tem a permissão do microfone de quem usava antes."""
    return base if not exe or "msedge" in exe.lower() or "edge" in Path(exe).name.lower() else \
        base.with_name(base.name + "-chrome")


def open_window(url: str, profile: Path, oculta: bool = False, ajustar: bool = True) -> subprocess.Popen | None:
    """Abre a interface. oculta=True: abre fora da tela e esconde (fica só na bandeja, ouvindo)."""
    exe = _browser()
    if not exe:
        if not oculta:
            webbrowser.open(url)
        return None
    profile.mkdir(parents=True, exist_ok=True)
    from . import desktop
    from .config import settings
    if oculta:
        posicao = ["--window-position=-32000,-32000", "--window-size=1440,900"]
    elif settings.janela == "cheia":
        posicao = ["--window-position=0,0", "--start-fullscreen"]
    elif settings.janela == "janela":
        x, y, w, h = desktop.tamanho_janela()
        posicao = [f"--window-position={x},{y}", f"--window-size={w},{h}"]
    else:
        posicao = ["--window-position=0,0", "--start-maximized", "--window-size=1440,900"]
    proc = subprocess.Popen([
        exe, f"--app={url}?auto=1{'&bg=1' if oculta else ''}", f"--user-data-dir={profile}", "--no-first-run",
        "--no-default-browser-check", *posicao,
        # Minimizada/oculta a página continua ativa: o Windows/Edge não "congela" o microfone, a fala e os timers
        "--disable-background-timer-throttling", "--disable-renderer-backgrounding",
        "--disable-backgrounding-occluded-windows",
        # Fala sem precisar de clique (o JARVIS pode iniciar sozinho com o Windows)
        "--autoplay-policy=no-user-gesture-required",
    ])
    if oculta:
        threading.Thread(target=desktop.hide_window, daemon=True).start()
    elif ajustar and os.name == "nt":
        threading.Thread(target=desktop.ajustar_ao_abrir, daemon=True).start()
    return proc


def abrir_flutuante(url: str, profile: Path) -> None:
    """Abre a página da janelinha flutuante (fora da tela; o flutuante.py a esconde, tira a moldura e põe no
    canto). Perfil e processo próprios, com desenho por software: com a composição da GPU o Windows ignora
    o recorte da janela (SetWindowRgn) e apareceria o retângulo inteiro; sem ela (mas pintando com a GPU) o recorte
    funciona e arrastar continua leve."""
    exe = _browser()
    if not exe:
        return
    subprocess.Popen([exe, f"--app={url}/flutuante", f"--user-data-dir={profile}", "--no-first-run",
                      "--no-default-browser-check", "--window-position=-32000,-32000", "--window-size=420,660",
                      "--disable-background-timer-throttling", "--disable-renderer-backgrounding",
                      "--disable-backgrounding-occluded-windows", "--disable-direct-composition", "--disable-gpu-compositing"])


def main() -> int:
    if "--preencher-console" in sys.argv:      # JARVIS.exe usado como "digitador" do StatusRobot
        from .console_input import main as preencher
        i = sys.argv.index("--preencher-console")
        return preencher(sys.argv[i + 1:])
    # .exe sem console: stdout/stderr não existem, e o uvicorn/logging tentariam escrever neles
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")

    from .config import USER_DIR, settings
    from . import desktop

    minimizado = "--minimizado" in sys.argv
    profile = perfil(USER_DIR / "navegador", _browser())

    # Já existe um JARVIS rodando? Traz a janela dele para a frente (ou abre uma, se estiver só na bandeja)
    if _jarvis_running(settings.port):
        import httpx
        try:        # pede ao JARVIS que já está rodando para mostrar a janela dele (mesmo a da bandeja)
            if httpx.post(f"http://127.0.0.1:{settings.port}/api/mostrar", timeout=5).status_code == 200:
                return 0
        except httpx.HTTPError:
            pass
        if not desktop.focus_window():
            open_window(f"http://127.0.0.1:{settings.port}", profile)
        return 0
    if not _port_free(settings.port):
        settings.port = _free_port()

    from .logger import log

    try:
        import uvicorn

        from .server import app
    except Exception:
        log.exception("Falha ao carregar o JARVIS")
        return 1

    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=settings.port, log_level="warning",
                                           log_config=None))

    def serve() -> None:
        try:
            server.run()
        except BaseException:   # sem console, só o log mostra por que o servidor não subiu
            log.exception("Servidor do JARVIS parou com erro")

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    for _ in range(300):
        if server.started or not thread.is_alive():
            break
        time.sleep(0.1)
    url = f"http://127.0.0.1:{settings.port}"
    if not server.started:
        log.error("O servidor não iniciou em %s; veja o erro acima neste log.", url)
        return 1
    log.info("JARVIS (aplicativo) em %s — dados em %s", url, USER_DIR)
    try:
        desktop.repair_autostart()
    except OSError as exc:
        log.warning("Não consegui conferir o início automático: %s", exc)

    open_window(url, profile, ajustar=not minimizado)
    if minimizado:          # iniciou com o Windows: abre minimizado, já ouvindo "Jarvis"
        threading.Thread(target=desktop.minimize_window, daemon=True).start()

    from .server import clients, ganchos, iniciar_vigias
    iniciar_vigias()

    sair = threading.Event()

    def abrir() -> None:
        if not desktop.focus_window():
            open_window(url, profile)

    def encerrar() -> None:
        desktop.close_browser_windows(profile)
        desktop.close_browser_windows(USER_DIR / "navegador-flutuante")
        desktop.close_browser_windows(USER_DIR / "navegador-apps")
        sair.set()

    def reabrir() -> None:
        """Trocou o navegador nas Configurações: fecha a janela atual e abre no navegador novo."""
        nonlocal profile
        antigo = profile
        profile = perfil(USER_DIR / "navegador", _browser())
        if profile != antigo:
            desktop.close_browser_windows(antigo)
            time.sleep(1.5)
            open_window(url, profile)
            log.info("Interface reaberta no navegador %s", tipo_navegador(_browser()))

    ganchos["mostrar"] = abrir
    ganchos["reabrir"] = reabrir

    # Janelinha flutuante: só com a janela principal minimizada/na bandeja (e a página dela rodando)
    if os.name == "nt":
        from . import flutuante
        perfil_flutuante = USER_DIR / "navegador-flutuante"
        flutuante.FLUTUANTE = flutuante.Flutuante(lambda: abrir_flutuante(url, perfil_flutuante))
        mostrar_flutuante = lambda: (settings.flutuante != "0" and clients["ativos"] > 0  # noqa: E731
                                     and not desktop.janela_visivel())
        threading.Thread(target=flutuante.FLUTUANTE.vigiar, args=(mostrar_flutuante, sair), daemon=True).start()
    icon = Path(__file__).parent / "web" / "icon.png"
    tray = desktop.Tray(icon, abrir, encerrar)
    na_bandeja = tray.start()

    inicio, avisou = time.monotonic(), False
    relancada_em, tentativas = 0.0, 0
    while thread.is_alive() and not sair.is_set():
        time.sleep(1)
        if clients["ativos"] > 0:
            tentativas = 0
            continue
        nunca_conectou = clients["ultimo_em"] == 0.0
        if na_bandeja:
            # Fechou a janela: o JARVIS reabre a interface escondida, só na bandeja, e continua ouvindo
            # "Jarvis" e falando. "Abrir" na bandeja mostra essa mesma janela (sem reiniciar).
            agora = time.monotonic()
            fechada_ha = agora - clients["ultimo_em"]
            if not nunca_conectou and fechada_ha > CLOSE_GRACE and agora - relancada_em > 45 and tentativas < 3:
                log.info("Janela fechada: continuando na bandeja (janela oculta)")
                open_window(url, profile, oculta=True)
                relancada_em, tentativas = agora, tentativas + 1
                if not avisou:
                    tray.notify("Continuo aqui na bandeja, ouvindo. Diga \"Jarvis\" quando precisar; "
                                "clique no ícone para abrir a janela ou em Sair para me desligar.")
                    avisou = True
            continue
        # Sem bandeja (ex.: Linux sem suporte): encerra quando todas as janelas fecham.
        # (Não dá para confiar no processo do navegador: se já houver um Edge com esse perfil aberto,
        # o processo novo entrega a janela para ele e termina na hora.)
        if nunca_conectou and time.monotonic() - inicio > 120:
            log.warning("Nenhuma janela conectou em 2 min; encerrando o JARVIS.")
            break
        if not nunca_conectou and time.monotonic() - clients["ultimo_em"] > CLOSE_GRACE:
            break
    server.should_exit = True
    thread.join(timeout=5)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
