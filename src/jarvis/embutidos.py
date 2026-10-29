"""Apps da web dentro do JARVIS: WhatsApp, Instagram e Facebook.

Esses sites não deixam ser abertos dentro de outra página, então cada um roda numa janela de navegador de verdade
(modo app, perfil próprio em %APPDATA%\\JARVIS\\navegador-apps), sem moldura e encaixada no painel APPS da
interface: a página do JARVIS diz onde está o painel, e a janela do app é posicionada em cima dele, "dona" da
janela do JARVIS (acompanha, minimiza junto) e recortada só na área da página. O login é feito uma vez em cada
site, ali mesmo, e fica salvo no perfil.

Como a janela é do JARVIS (com o protocolo de depuração do navegador), ele também consegue:
- avisar quando chega mensagem/notificação nova (pelo "(3)" do título e, no WhatsApp, pela lista de conversas);
- ler e enviar mensagens do WhatsApp por ela (o mesmo script da extensão, rodando num "mundo isolado" da página).
"""
from __future__ import annotations

import ctypes
import json
import os
import re
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable

import httpx

from .config import USER_DIR, settings
from .logger import log

IS_WINDOWS = os.name == "nt"
APPS: dict[str, dict[str, str]] = {
    # "acordado": escondido fora da tela em vez de oculto (oculta, a página para de desenhar e a busca do WhatsApp
    # não mostra resultados até alguém abrir o app)
    "whatsapp": {"nome": "WhatsApp", "url": "https://web.whatsapp.com/", "host": "web.whatsapp.com", "acordado": "1"},
    "instagram": {"nome": "Instagram", "url": "https://www.instagram.com/", "host": "instagram.com"},
    "facebook": {"nome": "Facebook", "url": "https://www.facebook.com/", "host": "facebook.com"},
    "youtube": {"nome": "YouTube", "url": "https://www.youtube.com/", "host": "youtube.com", "sem_avisos": "1"},
    # Navegador de verdade (Google): pode ir para qualquer site, então é achado pela aba, não pelo título/endereço
    "navegador": {"nome": "Navegador", "url": "https://www.google.com/", "host": "google.", "livre": "1", "sem_avisos": "1"},
}
PERFIL = USER_DIR / "navegador-apps"
# Sem a composição direta da GPU o Windows respeita o recorte da janela (SetWindowRgn); o resto continua na GPU
FLAGS = ["--disable-direct-composition", "--disable-gpu-compositing", "--no-first-run", "--no-default-browser-check",
         "--disable-background-timer-throttling", "--disable-renderer-backgrounding",
         "--disable-backgrounding-occluded-windows", "--autoplay-policy=no-user-gesture-required"]
CONTENT_JS = Path(__file__).parent / "extensao_whatsapp" / "content.js"
_CONTADOR = re.compile(r"^\((\d+)\)")
GWLP_HWNDPARENT = -8


def _tirar_da_barra(hwnd: int) -> None:
    """Remove o botão da janela na barra de tarefas (ITaskbarList::DeleteTab). O estilo "janela de ferramenta"
    já evita o botão, mas o Chrome às vezes o recoloca (ao abrir, ou quando o site pede atenção)."""
    if not IS_WINDOWS or not hwnd:
        return
    import ctypes.wintypes as wt
    ole = ctypes.WinDLL("ole32")
    ole.CoCreateInstance.argtypes = [ctypes.c_void_p, ctypes.c_void_p, wt.DWORD, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]

    def guid(texto: str) -> Any:
        g = (ctypes.c_byte * 16)()
        ole.CLSIDFromString(ctypes.c_wchar_p(texto), ctypes.byref(g))
        return g

    ole.CoInitialize(None)
    obj = ctypes.c_void_p()
    try:
        if ole.CoCreateInstance(guid("{56FDF344-FD6D-11d0-958A-006097C9A090}"), None, 1,      # CLSID_TaskbarList
                                guid("{56FDF342-FD6D-11d0-958A-006097C9A090}"), ctypes.byref(obj)) != 0 or not obj:
            return
        vt = ctypes.cast(ctypes.cast(obj, ctypes.POINTER(ctypes.c_void_p))[0], ctypes.POINTER(ctypes.c_void_p))
        metodo = lambda i, *tipos: ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *tipos)(vt[i])   # noqa: E731
        metodo(3)(obj)                                                   # HrInit
        metodo(5, ctypes.c_void_p)(obj, ctypes.c_void_p(hwnd))            # DeleteTab
        metodo(2)(obj)                                                   # Release
    except OSError:
        pass


def _alias(app: str) -> str | None:
    a = re.sub(r"[^a-z]", "", (app or "").lower())
    for chave, info in APPS.items():
        if a in (chave, info["nome"].lower()) or (a and chave.startswith(a[:4])):
            return chave
    return {"zap": "whatsapp", "wpp": "whatsapp", "insta": "instagram", "face": "facebook", "fb": "facebook",
            "yt": "youtube", "you": "youtube", "google": "navegador", "chrome": "navegador", "edge": "navegador",
            "browser": "navegador", "pesquisa": "navegador"}.get(a)


def _navegador() -> str | None:
    """O primeiro navegador (Chrome, senão Edge) em que a empresa não bloqueou o protocolo de depuração."""
    from . import desktop
    from .app import browsers
    return next((exe for exe in browsers() if not desktop.depuracao_bloqueada(exe)), None)


class Embutidos:
    def __init__(self) -> None:
        self.porta = 0
        self.fontes: dict[str, dict[str, Any]] = {}     # "principal"/"flutuante" -> vagas dos apps naquela página
        self._abrindo: set[str] = set()
        self.titulos: dict[str, int] = {}              # janelas já preparadas (hwnd -> barra do Chrome em px)
        self.contagem: dict[str, int | None] = {}      # "(3)" do título de cada app (None = ainda não lido)
        self.ao_avisar: Callable[[str, str], None] | None = None
        self._lock = threading.RLock()                 # janelas (mostrar, esconder, posicionar)
        self._lock_wa = threading.Lock()                # conversa com o WhatsApp de dentro (pode demorar)
        self._wa_visto: dict[str, int] | None = None   # não lidas por conversa, na última olhada (para os avisos)
        self._avisos: threading.Thread | None = None
        self._cdp: dict[str, Any] = {}
        self._mundo: dict[str, int] = {}
        self._vigia: threading.Thread | None = None
        self._ultimo_pos: dict[int, tuple] = {}
        self._pids_cache: tuple[float, set[int]] = (0.0, set())
        self._hwnds: dict[str, int] = {}
        self._aba: dict[str, str] = {}                  # app -> id da aba (o navegador muda de site, a aba não)
        self._lock_pos = threading.Lock()
        self._lock_aba = threading.Lock()               # um de cada vez procurando a janela pela aba
        self._tema: dict[str, tuple[Any, str, bool]] = {}  # aba -> (sessão, -, ligado)
        self._tema_thread: threading.Thread | None = None
        self._geo: dict[int, dict[str, Any]] = {}       # janela do app -> deslocamentos já medidos (para mover rápido)
        self._origem: dict[str, tuple[int, int, float, int]] = {}   # fonte -> canto da página na tela, dpr e dona
        self._sem_botao: dict[int, float] = {}          # janela -> quando conferimos que não tem botão na barra de tarefas

    # ------------------------------------------------------------------ navegador e janelas
    def _rodando(self) -> bool:
        if not self.porta:
            # O navegador dos apps pode ter ficado aberto de antes (o JARVIS reiniciou): a porta está num arquivo do perfil
            try:
                self.porta = int((PERFIL / "DevToolsActivePort").read_text(encoding="utf-8").split()[0])
            except (OSError, ValueError, IndexError):
                return False
        try:
            httpx.get(f"http://127.0.0.1:{self.porta}/json/version", timeout=1)
            return True
        except httpx.HTTPError:
            return False

    def _alvos(self) -> list[dict[str, Any]]:
        try:
            return [a for a in httpx.get(f"http://127.0.0.1:{self.porta}/json", timeout=2).json() if a.get("type") == "page"]
        except (httpx.HTTPError, ValueError):
            return []

    def _alvo(self, app: str) -> dict[str, Any] | None:
        alvos = self._alvos()
        aba = self._aba.get(app)
        if aba:
            a = next((a for a in alvos if a.get("id") == aba), None)
            if a:
                return a
        outras = {v for k, v in self._aba.items() if k != app}
        host = APPS[app]["host"]
        a = next((a for a in alvos if host in a.get("url", "") and a.get("id") not in outras), None)
        if a:
            self._aba[app] = a["id"]
        return a

    def abrir(self, app: str, esperar: float = 25) -> bool:
        """Garante a janela do app aberta (escondida até ser encaixada). Lembra o app para os próximos inícios."""
        if not IS_WINDOWS:
            return False
        with self._lock:
            if self._rodando() and self._alvo(app):
                return True
            exe = _navegador()
            if not exe:
                log.warning("Apps embutidos: nenhum navegador com o protocolo de depuração liberado")
                return False
            if not self._rodando():
                from .whatsapp import _porta_livre
                self.porta = _porta_livre()
            PERFIL.mkdir(parents=True, exist_ok=True)
            # Mesmo perfil e porta: se o navegador já está aberto, ele só abre mais uma janela de app
            # Visual do JARVIS: modo escuro (os sites sem tema escuro são escurecidos automaticamente)
            escuro = ["--force-dark-mode", "--enable-features=WebContentsForceDark"] if settings.apps_tema != "0" else []
            subprocess.Popen([exe, f"--app={APPS[app]['url']}", f"--user-data-dir={PERFIL}",
                              f"--remote-debugging-port={self.porta}", "--window-position=-32000,-32000",
                              "--window-size=1100,800", *FLAGS, *escuro])
            lembrados = [a for a in settings.apps_web.split(",") if a in APPS]
            if app not in lembrados:
                settings.set_vars({"JARVIS_APPS_WEB": ",".join(lembrados + [app])})
        fim = time.monotonic() + esperar
        while time.monotonic() < fim:
            time.sleep(0.5)
            h = self._alvo(app) and self._hwnd(app)
            if h:
                from . import flutuante as fl
                with self._lock:
                    if not any(app in (f.get("slots") or {}) for f in self.fontes.values()):
                        self._esconder(app, h, fl)        # recém-aberta: sai da barra de tarefas na hora
                self._garantir_vigia()
                return True
        return False

    def _pids(self) -> set[int]:
        """Processos do nosso navegador (guardados por 5 s: percorrer todos os processos é lento)."""
        import psutil
        if time.monotonic() - self._pids_cache[0] < 5:
            return self._pids_cache[1]
        marca = str(PERFIL).lower()
        pids = set()
        for p in psutil.process_iter(["pid", "cmdline"]):
            try:
                if marca in " ".join(p.info["cmdline"] or []).lower():
                    pids.add(p.info["pid"])
            except (psutil.Error, TypeError):
                continue
        self._pids_cache = (time.monotonic(), pids)
        return pids

    def _hwnd(self, app: str) -> int:
        """A janela (de nível superior) do app: pelo nome do site no título, entre as do nosso navegador."""
        from .flutuante import _classe, _titulo
        nome = APPS[app]["nome"].lower()
        h = self._hwnds.get(app, 0)
        if h and ctypes.windll.user32.IsWindow(ctypes.c_void_p(h)) and (APPS[app].get("livre") or nome in _titulo(h).lower()):
            return h
        if APPS[app].get("livre"):
            return self._hwnd_pela_aba(app)
        pids, achado = self._pids(), []
        Enum = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

        def cb(h, _):
            pid = ctypes.c_ulong()
            ctypes.windll.user32.GetWindowThreadProcessId(ctypes.c_void_p(h), ctypes.byref(pid))
            if pid.value in pids and _classe(h).startswith("Chrome_WidgetWin") and nome in _titulo(h).lower():
                achado.append(h)
                return False
            return True

        ctypes.windll.user32.EnumWindows(Enum(cb), 0)
        self._hwnds[app] = achado[0] if achado else 0
        return self._hwnds[app]

    def _hwnd_pela_aba(self, app: str) -> int:
        """Janela de um app "livre" (o título muda a cada site): manda a janela da aba para um ponto marcado da tela
        (pelo protocolo do navegador) e procura qual das nossas janelas está lá."""
        with self._lock_aba:
            h = self._hwnds.get(app, 0)
            if h and ctypes.windll.user32.IsWindow(ctypes.c_void_p(h)):
                return h                                  # outro já achou enquanto esperávamos
            return self._procurar_pela_aba(app)

    def _procurar_pela_aba(self, app: str) -> int:
        from .flutuante import _classe, _rect
        from .whatsapp import _CDP
        alvo = self._alvo(app)
        if not alvo:
            return 0
        try:
            v = httpx.get(f"http://127.0.0.1:{self.porta}/json/version", timeout=2).json()
            b = _CDP(v["webSocketDebuggerUrl"])
            try:
                win = b.call("Browser.getWindowForTarget", targetId=alvo["id"])["windowId"]
                b.call("Browser.setWindowBounds", windowId=win, bounds={"windowState": "normal"})
                b.call("Browser.setWindowBounds", windowId=win, bounds={"left": -26000, "top": -26000, "width": 900, "height": 700})
            finally:
                b.close()
        except Exception as exc:
            log.info("Não achei a janela do %s: %s", app, exc)
            return 0
        time.sleep(0.3)
        pids, outras, achado = self._pids(), {v for k, v in self._hwnds.items() if k != app}, []
        Enum = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

        def cb(h, _):
            pid = ctypes.c_ulong()
            ctypes.windll.user32.GetWindowThreadProcessId(ctypes.c_void_p(h), ctypes.byref(pid))
            if pid.value in pids and h not in outras and _classe(h).startswith("Chrome_WidgetWin"):
                x, y, _x2, _y2 = _rect(h)
                if -80000 < x <= -20000 and -80000 < y <= -20000 and x > -31000:
                    achado.append(h)
                    return False
            return True

        ctypes.windll.user32.EnumWindows(Enum(cb), 0)
        self._hwnds[app] = achado[0] if achado else 0
        self._ultimo_pos.pop(self._hwnds[app], None)       # foi movida para o ponto marcado: reposicionar
        return self._hwnds[app]

    # ------------------------------------------------------------------ encaixe nos cards/painel da interface
    # Cada "fonte" (a janela principal ou a janelinha flutuante) diz onde estão as vagas dos apps na página dela:
    # {app: {"rect": [x, y, w, h], "clip": [x, y, w, h]}} em px CSS ("clip" = a parte visível, ex.: coluna rolada),
    # mais o dpr, o tamanho da página e se ela está visível (sem modal/menu por cima). Vários apps ao mesmo tempo.
    def encaixar_varios(self, fonte: str, slots: dict[str, Any], dpr: float, vista: list[float], visivel: bool) -> dict[str, Any]:
        validos: dict[str, Any] = {}
        for app, s in (slots or {}).items():
            chave = _alias(app)
            rect = (s or {}).get("rect") or []
            if chave in APPS and len(rect) >= 4:
                clip = (s.get("clip") or rect)[:4]
                validos[chave] = {"rect": [float(v) for v in rect[:4]], "clip": [float(v) for v in clip]}
        with self._lock:
            antes = set((self.fontes.get(fonte) or {}).get("slots", {})) if (self.fontes.get(fonte) or {}).get("visivel") else set()
            self.fontes[fonte] = {"slots": validos, "dpr": float(dpr or 1.0), "vista": [float(v) for v in vista[:2]],
                                  "visivel": bool(visivel), "nome": fonte}
        self._esconder_ja([a for a in antes if a not in validos or not visivel])
        abrindo = []
        for app in validos:
            if not (self._rodando() and self._alvo(app)):
                abrindo.append(app)
                if app not in self._abrindo:
                    self._abrindo.add(app)
                    threading.Thread(target=self._abrir_e_soltar, args=(app,), daemon=True).start()
        self._garantir_vigia()
        self._posicionar()
        return {"ok": True, "abrindo": abrindo}

    def _esconder_ja(self, apps: list[str]) -> None:
        """Esconde na hora (só com o que já sabemos: nada de procurar janelas, que pode demorar)."""
        if not IS_WINDOWS:
            return
        from . import flutuante as fl
        for app in apps:
            h = self._hwnds.get(app)
            if h:
                self._esconder(app, h, fl)

    def _abrir_e_soltar(self, app: str) -> None:
        try:
            self.abrir(app)
        finally:
            self._abrindo.discard(app)

    def encaixar(self, app: str, rect: list[float], dpr: float, vista: list[float], visivel: bool) -> dict[str, Any]:
        """Um app só, no painel do centro (compatibilidade)."""
        if (_alias(app) or "") not in APPS:
            return {"ok": False, "mensagem": "App desconhecido: use WhatsApp, Instagram, Facebook ou YouTube."}
        return self.encaixar_varios("principal", {app: {"rect": rect}}, dpr, vista, visivel)

    def fechar_painel(self) -> None:
        with self._lock:
            antes = set((self.fontes.pop("principal", None) or {}).get("slots", {}))
        self._esconder_ja(list(antes))
        self._posicionar()

    @property
    def ativo(self) -> list[str]:
        return sorted({a for f in self.fontes.values() for a in f["slots"]})

    def mover_rapido(self, fonte: str, app: str, rect: list[float], dpr: float) -> bool:
        """Arrastando um card de app: move a janela do app na hora, com as medidas já conhecidas (sem procurar
        janelas nem perguntar nada ao navegador). Se o tamanho mudou, fica para o caminho normal."""
        if not IS_WINDOWS:
            return False
        from . import flutuante as fl
        app = _alias(app) or ""
        h = self._hwnds.get(app, 0)
        g, o = self._geo.get(h), self._origem.get(fonte)
        if not (h and g and o) or abs(o[2] - (dpr or 1.0)) > 0.01:
            return False
        ox, oy, d, dona = o
        alvo = (ox + round(rect[0] * d), oy + round(rect[1] * d), round(rect[2] * d), round(rect[3] * d))
        if (alvo[2], alvo[3]) != g["tam"]:
            return False
        fl._dpi_real()
        fl._u.SetWindowPos(h, None, alvo[0] - g["dx"], alvo[1] - g["dy"], 0, 0, fl.SWP_NOSIZE | fl.SWP_NOZORDER | fl.SWP_NOACTIVATE)
        c = g["corte"]
        self._ultimo_pos[h] = (alvo, (alvo[0] + c[0], alvo[1] + c[1], alvo[0] + c[2], alvo[1] + c[3]), g["barra"], dona)
        with self._lock:                                    # a vigia não "puxa de volta" para a posição antiga
            slot = (self.fontes.get(fonte) or {}).get("slots", {}).get(app)
            if slot:
                dx, dy = rect[0] - slot["rect"][0], rect[1] - slot["rect"][1]
                slot["rect"] = [float(v) for v in rect[:4]]
                slot["clip"] = [slot["clip"][0] + dx, slot["clip"][1] + dy, slot["clip"][2], slot["clip"][3]]
        return True

    def _janela_principal(self) -> int:
        from . import desktop
        u = ctypes.windll.user32
        return next((w for w in desktop._find_windows() if not u.IsIconic(w) and not desktop._fora_da_tela(w)), 0)

    def _fonte_ativa(self) -> tuple[int, dict[str, Any] | None]:
        """Quem mostra os apps agora: a janela principal (se estiver na tela) ou a janelinha flutuante."""
        from . import flutuante as fl
        p = self.fontes.get("principal")
        if p and p["visivel"]:
            dona = self._janela_principal()
            if dona:
                return dona, p
        f = self.fontes.get("flutuante")
        flut = fl.FLUTUANTE.hwnd if fl.FLUTUANTE else 0
        if f and f["visivel"] and flut and fl._u.IsWindowVisible(flut) and not self._janela_principal():
            return flut, f
        return 0, None

    def _barra_chrome(self, app: str, hwnd: int) -> int:
        """Altura da barra que o Chrome desenha dentro da área cliente (fica de fora do recorte)."""
        if hwnd in self.titulos:
            return self.titulos[hwnd]
        from .flutuante import _cliente
        alvo = self._alvo(app)
        if not alvo:
            return 0
        from .whatsapp import _CDP
        c = _CDP(alvo["webSocketDebuggerUrl"])
        try:
            alt, dpr = c.eval("[innerHeight, devicePixelRatio]")
        finally:
            c.close()
        cli = _cliente(hwnd)
        self.titulos[hwnd] = max(0, (cli[3] - cli[1]) - round(alt * dpr))
        return self.titulos[hwnd]

    def _preparar(self, hwnd: int, dona: int) -> None:
        from . import flutuante as fl
        u = fl._u
        est = u.GetWindowLongPtrW(hwnd, fl.GWL_STYLE)
        if est & fl.WS_CAPTION:
            u.SetWindowLongPtrW(hwnd, fl.GWL_STYLE, est & ~(fl.WS_CAPTION | fl.WS_THICKFRAME | fl.WS_SYSMENU | fl.WS_MINIMIZEBOX | fl.WS_MAXIMIZEBOX))
            ex = u.GetWindowLongPtrW(hwnd, fl.GWL_EXSTYLE)
            u.SetWindowLongPtrW(hwnd, fl.GWL_EXSTYLE, (ex | fl.WS_EX_TOOLWINDOW) & ~fl.WS_EX_APPWINDOW)
            u.SetWindowPos(hwnd, None, 0, 0, 0, 0, fl.SWP_NOMOVE | fl.SWP_NOSIZE | fl.SWP_NOZORDER | fl.SWP_NOACTIVATE | fl.SWP_FRAMECHANGED)
            self.titulos.pop(hwnd, None)
        u.SetWindowLongPtrW(hwnd, GWLP_HWNDPARENT, dona)          # fica por cima da dona e some/minimiza com ela

    FORA = -32000

    def _esconder(self, app: str, h: int, fl: Any) -> None:
        """Tira o app da vista. O WhatsApp fica "aberto" fora da tela (continua desenhando, pronto para buscar);
        os outros ficam ocultos de verdade (não gastam nada)."""
        u = fl._u
        self._ultimo_pos.pop(h, None)
        if not APPS[app].get("acordado"):
            if u.IsWindowVisible(h):
                u.ShowWindow(h, fl.SW_HIDE)
            return
        if (u.IsWindowVisible(h) and fl._rect(h)[0] <= self.FORA + 100 and u.GetWindowLongPtrW(h, fl.GWL_EXSTYLE) & fl.WS_EX_TOOLWINDOW
                and not u.GetWindowLongPtrW(h, GWLP_HWNDPARENT)):
            if time.monotonic() - self._sem_botao.get(h, 0) > 3:      # já está estacionado: só garante sem botão
                self._sem_botao[h] = time.monotonic()
                _tirar_da_barra(h)
            return
        if u.IsWindowVisible(h):
            u.ShowWindow(h, fl.SW_HIDE)                   # oculta um instante: o Windows só tira da barra de tarefas assim
        self._preparar(h, 0)                              # sem dona (minimizar o JARVIS não a oculta) e fora da barra de tarefas
        u.SetWindowPos(h, None, self.FORA, self.FORA, 0, 0, fl.SWP_NOSIZE | fl.SWP_NOZORDER | fl.SWP_NOACTIVATE)
        u.ShowWindow(h, fl.SW_SHOWNOACTIVATE)
        _tirar_da_barra(h)
        self._sem_botao[h] = time.monotonic()

    def _posicionar(self) -> None:
        """Mostra cada app em cima da sua vaga (card ou painel) e esconde os que não têm vaga."""
        if not IS_WINDOWS or not self.porta:
            return
        from . import flutuante as fl
        fl._dpi_real()
        u = fl._u
        if not self._lock_pos.acquire(blocking=False):
            return                                        # já tem alguém posicionando; a vigia aplica em seguida
        try:
            self._posicionar_agora(fl, u)
        finally:
            self._lock_pos.release()

    def _posicionar_agora(self, fl: Any, u: Any) -> None:
        with self._lock:
            dona, fonte = self._fonte_ativa()
        slots = fonte["slots"] if fonte else {}
        for app in APPS:
            if app not in slots and not self._hwnds.get(app):
                continue                                      # nunca aberto e sem vaga: nada a fazer
            h = self._hwnd(app)
            if not h:
                continue
            s = slots.get(app)
            if not s or not dona:
                self._esconder(app, h, fl)
                continue
            self._preparar(h, dona)
            # Onde está a página da dona na tela (a barra do Chrome dela fica em cima, dentro da área cliente)
            dcli, d = fl._cliente(dona), fonte["dpr"]
            ox, oy = dcli[0], dcli[3] - round(fonte["vista"][1] * d)
            x, y, w, hh = s["rect"]
            alvo = (ox + round(x * d), oy + round(y * d), round(w * d), round(hh * d))
            cx, cy, cw, ch = s["clip"]
            corte = (ox + round(cx * d), oy + round(cy * d), ox + round((cx + cw) * d), oy + round((cy + ch) * d))
            barra = self._barra_chrome(app, h)
            if self._ultimo_pos.get(h) != (alvo, corte, barra, dona) or not u.IsWindowVisible(h) or fl._rect(h)[0] < -10000:
                jan, cli = fl._rect(h), fl._cliente(h)
                topo = cli[1] + barra
                u.SetWindowPos(h, None, alvo[0] - (cli[0] - jan[0]), alvo[1] - (topo - jan[1]),
                               alvo[2] + (jan[2] - jan[0]) - (cli[2] - cli[0]), alvo[3] + (jan[3] - jan[1]) - (cli[3] - topo),
                               fl.SWP_NOZORDER | fl.SWP_NOACTIVATE)
                jan, cli = fl._rect(h), fl._cliente(h)
                topo = cli[1] + barra
                # Só a página (sem a barra do Chrome) e só a parte visível da vaga
                x1, y1 = max(cli[0], corte[0]) - jan[0], max(topo, corte[1]) - jan[1]
                x2, y2 = min(cli[2], corte[2]) - jan[0], min(cli[3], corte[3]) - jan[1]
                rg = fl._g.CreateRectRgn(x1, y1, max(x1, x2), max(y1, y2))
                u.SetWindowRgn(h, rg, True)
                u.ShowWindow(h, fl.SW_SHOWNOACTIVATE)
                self._ultimo_pos[h] = (alvo, corte, barra, dona)
                # Guardado para o arrasto: mover depois é só um SetWindowPos (o recorte é relativo à janela)
                self._geo[h] = {"dx": cli[0] - jan[0], "dy": topo - jan[1], "tam": (alvo[2], alvo[3]),
                                "corte": (corte[0] - alvo[0], corte[1] - alvo[1], corte[2] - alvo[0], corte[3] - alvo[1]), "barra": barra}
            self._origem[fonte["nome"]] = (ox, oy, d, dona)

    # ------------------------------------------------------------------ vigia: posição e avisos
    def _garantir_vigia(self) -> None:
        if self._vigia is None or not self._vigia.is_alive():
            self._vigia = threading.Thread(target=self._vigiar, daemon=True)
            self._vigia.start()
        if self._avisos is None or not self._avisos.is_alive():          # avisos em outra thread: não atrasam a janela
            self._avisos = threading.Thread(target=self._vigiar_avisos, daemon=True)
            self._avisos.start()
        if self._tema_thread is None or not self._tema_thread.is_alive():
            self._tema_thread = threading.Thread(target=self._vigiar_tema, daemon=True)
            self._tema_thread.start()

    def _vigiar(self) -> None:
        while True:
            try:
                self._posicionar()
            except Exception:
                log.exception("Apps embutidos")
                time.sleep(3)
            time.sleep(0.12)

    def _vigiar_avisos(self) -> None:
        while True:
            try:
                self._conferir_avisos()
            except Exception:
                log.exception("Avisos dos apps")
            time.sleep(5)

    def tematizar(self) -> None:
        """Visual do JARVIS em cada página dos apps. O script é idempotente: roda de novo a cada passada (a página
        pode ter recarregado ou navegado) e só mexe no <style> se precisar."""
        from . import tema_apps
        from .whatsapp import _CDP
        if not self._rodando():
            return
        ligado = settings.apps_tema != "0"
        abas = {a["id"]: a for a in self._alvos()}
        navegador = self._aba.get("navegador")
        for aba in [k for k in self._tema if k not in abas]:          # aba fechada
            try:
                self._tema.pop(aba)[0].close()
            except Exception:
                pass
        for aba, alvo in abas.items():
            atual = self._tema.get(aba)
            if atual and not ligado and not atual[2]:
                continue                                              # desligado e já tirado desta página
            try:
                c = atual[0] if atual else _CDP(alvo["webSocketDebuggerUrl"])
                c.call("Runtime.evaluate", expression=tema_apps.script(ligado, tinta=aba == navegador), tempo=5)
                self._tema[aba] = (c, "", ligado)
            except Exception as exc:
                log.info("Tema dos apps (%s): %s", alvo.get("url", "")[:40], exc)
                if atual:
                    try:
                        atual[0].close()
                    except Exception:
                        pass
                self._tema.pop(aba, None)

    def _vigiar_tema(self) -> None:
        while True:
            try:
                self.tematizar()
            except Exception:
                log.exception("Tema dos apps")
            time.sleep(1.5)

    def _conferir_avisos(self) -> None:
        from .flutuante import _titulo
        if self._hwnd("whatsapp"):
            self._avisos_whatsapp()
        from . import avisos
        for app, info in APPS.items():
            if app == "whatsapp" or info.get("sem_avisos") or avisos.app_silenciado(app):
                continue
            h = self._hwnd(app)
            if not h:
                continue
            m = _CONTADOR.match(_titulo(h))
            n = int(m.group(1)) if m else 0
            antes = self.contagem.get(app)
            self.contagem[app] = n
            if antes is None or n <= antes or not self.ao_avisar:
                continue                                # primeira leitura (o que já estava lá) não vira aviso
            novas = n - antes
            self.ao_avisar(f"{novas} {'notificação nova' if novas == 1 else 'notificações novas'} no {info['nome']}.", "")

    def _avisos_whatsapp(self) -> None:
        """Olha a lista de conversas (sem abrir nenhuma): avisa quando uma conversa NÃO silenciada ganha mensagem nova.
        Pessoas e grupos silenciados no WhatsApp são ignorados, como no celular."""
        from . import avisos
        r = self.whatsapp({"type": "espiar", "limite": 30}, timeout=10)
        if not r.get("ok"):
            return
        atuais = {c["conversa"]: c for c in r.get("conversas") or []}
        self.contagem["whatsapp"] = sum(c.get("nao_lidas", 0) for c in atuais.values() if not c.get("silenciada"))
        visto, self._wa_visto = self._wa_visto, {k: c.get("nao_lidas", 0) for k, c in atuais.items()}
        if visto is None or not self.ao_avisar:
            return                                      # primeira olhada: o que já estava lá não vira aviso
        if avisos.app_silenciado("whatsapp"):
            return
        novas = [c for k, c in atuais.items() if not c.get("silenciada") and c.get("nao_lidas", 0) > visto.get(k, 0)
                 and not avisos.conversa_silenciada(k)]
        if not novas:
            return
        if len(novas) == 1:
            c = novas[0]
            self.ao_avisar(f"Mensagem nova de {avisos.apelido_de(c['conversa'])}.", c.get("previa", ""))
        else:
            nomes = [avisos.apelido_de(c["conversa"]) for c in novas]
            texto = ", ".join(nomes[:-1]) + " e " + nomes[-1] if len(nomes) <= 3 else f"{', '.join(nomes[:3])} e mais {len(nomes) - 3}"
            self.ao_avisar(f"Mensagens novas de {texto}.", "")

    # ------------------------------------------------------------------ WhatsApp por dentro (mesmo script da extensão)
    def whatsapp(self, msg: dict[str, Any], timeout: float = 30) -> dict[str, Any]:
        """Executa um pedido da extensão (buscar, enviar_para, nao_lidas, ler_conversa, espiar...) no WhatsApp embutido."""
        from .whatsapp import _CDP
        with self._lock_wa:
            alvo = self._alvo("whatsapp") if self._rodando() else None
            if not alvo:
                return {"ok": False, "motivo": "sem_aba"}
            try:
                c = self._cdp.get("whatsapp")
                if c is None:
                    c = self._cdp["whatsapp"] = _CDP(alvo["webSocketDebuggerUrl"])
                ctx = self._mundo.get("whatsapp")
                for tentativa in range(2):
                    if ctx is None:
                        quadro = c.call("Page.getFrameTree")["frameTree"]["frame"]["id"]
                        ctx = c.call("Page.createIsolatedWorld", frameId=quadro, worldName="jarvis")["executionContextId"]
                        shim = ("window.chrome = {runtime: {onMessage: {addListener(fn) { window.__jarvis = fn; }}, "
                                "sendMessage() { return Promise.resolve(); }}};")
                        c.call("Runtime.evaluate", expression=shim + CONTENT_JS.read_text(encoding="utf-8"), contextId=ctx)
                        self._mundo["whatsapp"] = ctx
                    r = c.call("Runtime.evaluate", contextId=ctx, awaitPromise=True, returnByValue=True, tempo=timeout + 5,
                               expression=f"new Promise((ok) => window.__jarvis({json.dumps(msg)}, null, ok))")
                    valor = (r.get("result") or {}).get("value")
                    if "result" in r and "exceptionDetails" not in r and isinstance(valor, dict):
                        return valor
                    ctx = None                                   # a página recarregou (o contexto sumiu): cria de novo
                    self._mundo.pop("whatsapp", None)
                return {"ok": False, "motivo": "erro"}
            except Exception as exc:
                log.info("WhatsApp embutido: %s", exc)
                if self._cdp.get("whatsapp") is not None:
                    self._cdp.pop("whatsapp").close()
                self._mundo.pop("whatsapp", None)
                return {"ok": False, "motivo": "erro", "erro": str(exc)}

    def painel(self, app: str) -> dict[str, Any]:
        """Dados do card do app (Command Center): aberto/logado, contador do título e, no WhatsApp, as não lidas."""
        from .flutuante import _titulo
        info = APPS[app]
        if not (self._rodando() and self._alvo(app)):
            return {"ok": True, "aberto": False, "nome": info["nome"]}
        h = self._hwnd(app)
        m = _CONTADOR.match(_titulo(h)) if h else None
        dados: dict[str, Any] = {"ok": True, "aberto": True, "nome": info["nome"], "nao_lidas": int(m.group(1)) if m else 0}
        if app == "whatsapp":
            r = self.whatsapp({"type": "espiar", "limite": 6}, timeout=10)
            dados["estado"] = r.get("motivo") or ("pronto" if r.get("ok") else "erro")
            dados["conversas"] = r.get("conversas") or []
        else:
            alvo = self._alvo(app) or {}
            dados["logado"] = not re.search(r"/(accounts/login|login|checkpoint)", alvo.get("url", ""))
        return dados

    def ir(self, app: str, url: str = "") -> dict[str, Any]:
        """Leva o app a uma página dele mesmo (ex.: Direct do Instagram, notificações do Facebook)."""
        from .whatsapp import _CDP
        app = _alias(app) or ""
        if app not in APPS:
            return {"ok": False, "mensagem": "App desconhecido."}
        url = url or APPS[app]["url"]
        if APPS[app].get("livre"):
            from .leitor import seguro
            if not seguro(url):
                return {"ok": False, "mensagem": "Esse endereço eu não abro (só sites da internet)."}
        else:
            dominio = re.escape(APPS[app]["host"].removeprefix("web."))
            if not re.match(rf"^https://([a-z0-9-]+\.)*{dominio}/", url):
                return {"ok": False, "mensagem": "Endereço fora do app."}
        alvo = self._alvo(app)
        if not alvo:
            return {"ok": False, "mensagem": f"Abra o {APPS[app]['nome']} no painel APPS primeiro."}
        c = _CDP(alvo["webSocketDebuggerUrl"])
        try:
            c.call("Page.navigate", url=url)
        finally:
            c.close()
        return {"ok": True}

    def navegar(self, app: str, acao: str, url: str = "") -> dict[str, Any]:
        """Botões do Navegador: voltar | avancar | recarregar | ir (endereço ou pesquisa no Google)."""
        import urllib.parse
        from .whatsapp import _CDP
        app = _alias(app) or ""
        if app not in APPS:
            return {"ok": False, "mensagem": "App desconhecido."}
        if acao == "ir":
            texto = (url or "").strip()
            if not texto:
                return {"ok": False, "mensagem": "Digite um endereço ou o que pesquisar."}
            if not re.match(r"^https?://", texto):
                texto = ("https://" + texto) if re.fullmatch(r"[\w-]+(\.[\w-]+)+(/\S*)?", texto) else \
                    "https://www.google.com/search?q=" + urllib.parse.quote_plus(texto)
            return self.ir(app, texto)
        alvo = self._alvo(app) if self._rodando() else None
        if not alvo:
            return {"ok": False, "mensagem": f"O {APPS[app]['nome']} não está aberto."}
        c = _CDP(alvo["webSocketDebuggerUrl"])
        try:
            if acao == "recarregar":
                c.call("Page.reload")
            elif acao in ("voltar", "avancar"):
                h = c.call("Page.getNavigationHistory")
                i = h.get("currentIndex", 0) + (-1 if acao == "voltar" else 1)
                entradas = h.get("entries") or []
                if not 0 <= i < len(entradas):
                    return {"ok": False, "mensagem": "Não há para onde ir."}
                c.call("Page.navigateToHistoryEntry", entryId=entradas[i]["id"])
            else:
                return {"ok": False, "mensagem": "Ação inválida."}
        finally:
            c.close()
        return {"ok": True}

    def ler_navegador(self, limite: int = 6000) -> dict[str, Any]:
        """O que está aberto no Navegador do JARVIS agora (título, endereço e o texto da página)."""
        from .whatsapp import _CDP
        alvo = self._alvo("navegador") if self._rodando() else None
        if not alvo:
            return {"ok": False, "mensagem": "O Navegador do JARVIS não está aberto."}
        c = _CDP(alvo["webSocketDebuggerUrl"])
        try:
            texto = c.eval(f"(document.body ? document.body.innerText : '').slice(0, {max(500, min(int(limite), 12000))})") or ""
        finally:
            c.close()
        return {"ok": True, "titulo": alvo.get("title", ""), "url": alvo.get("url", ""), "texto": texto}

    def whatsapp_pronto(self) -> bool:
        return self.whatsapp({"type": "estado"}, timeout=8).get("estado") == "pronto"

    def status(self) -> dict[str, Any]:
        rodando = self._rodando()
        return {"apps": {a: {"nome": i["nome"], "aberto": bool(rodando and self._alvo(a)),
                             "nao_lidas": self.contagem.get(a) or 0} for a, i in APPS.items()},
                "ativo": self.ativo}

    def iniciar(self) -> None:
        """No início do JARVIS: reabre (escondidos) os apps que você já usou, para os avisos funcionarem."""
        for app in [a for a in settings.apps_web.split(",") if a in APPS]:
            self.abrir(app)


EMBUTIDOS = Embutidos()


def navegador_pesquisar(consulta: str = "", url: str = "") -> dict[str, Any]:
    """Abre o Navegador do JARVIS (Google) numa pesquisa ou num endereço, e mostra no centro da tela."""
    import urllib.parse
    if not (consulta or url):
        return {"ok": False, "mensagem": "O que devo pesquisar (ou qual endereço abrir)?"}
    if not EMBUTIDOS.abrir("navegador"):
        return {"ok": False, "mensagem": "Não consegui abrir o Navegador do JARVIS."}
    destino = url if url.startswith(("http://", "https://")) else \
        "https://www.google.com/search?q=" + urllib.parse.quote_plus(consulta or url)
    r = EMBUTIDOS.ir("navegador", destino)
    if not r.get("ok"):
        return r
    return {"ok": True, "app": "navegador", "onde": "painel", "url": destino,
            "mensagem": "Pesquisa aberta no Navegador do JARVIS. Para saber o que apareceu, use navegador_ler."}


def navegador_ler() -> dict[str, Any]:
    return EMBUTIDOS.ler_navegador()


def abrir_app(app: str, onde: str = "painel") -> dict[str, Any]:
    """Ferramenta do cérebro: mostra o app no painel APPS da interface (quem encaixa é a página)."""
    chave = _alias(app)
    if chave is None:
        return {"ok": False, "mensagem": "Consigo abrir aqui dentro: WhatsApp, Instagram, Facebook, YouTube ou o Navegador."}
    onde = "card" if str(onde).lower().startswith("card") else "painel"
    return {"ok": True, "app": chave, "onde": onde,
            "mensagem": f"{APPS[chave]['nome']} aberto {'num card' if onde == 'card' else 'no centro'} da tela do JARVIS."}
