"""Controle dos apps abertos no PC pela automação de interface do Windows (UI Automation).

O cérebro do JARVIS "enxerga" um app pela lista de controles que ele expõe (botões, menus, campos, abas...)
e age neles pelo nome: clicar, digitar, atalhos de teclado, focar/minimizar/maximizar/fechar a janela.

Segurança:
- fechar janela, clicar em controles como "Excluir", "Enviar", "Pagar", "Desinstalar" e atalhos como Alt+F4
  ou Delete pedem a sua confirmação (o resto é executado direto);
- nunca digita em campo de senha;
- a própria janela do JARVIS fica de fora.
"""
from __future__ import annotations

import ctypes
import os
import re
import time
import unicodedata
from contextlib import contextmanager
from typing import Any, Iterator

from .logger import log

IS_WINDOWS = os.name == "nt"
# Controles que podem ter consequência: o JARVIS pergunta antes de clicar
RISCO = re.compile(r"(exclu|apag|delet|remov|envi|send|compr|pag|buy|pay|desinstal|uninstall|format|descart|"
                   r"nao salvar|não salvar|don.?t save|limpar tudo|resetar|redefinir|transfer|confirm)", re.I)
TECLAS_RISCO = re.compile(r"(alt\+f4|\bdel\b|delete|ctrl\+w|ctrl\+shift\+del)", re.I)
TIPOS_UTEIS = {"ButtonControl", "MenuItemControl", "EditControl", "TabItemControl", "ListItemControl",
               "HyperlinkControl", "CheckBoxControl", "RadioButtonControl", "ComboBoxControl", "TextControl",
               "TreeItemControl", "SplitButtonControl", "DocumentControl", "DataItemControl"}
NOMES_TIPO = {"ButtonControl": "botão", "MenuItemControl": "menu", "EditControl": "campo", "TabItemControl": "aba",
              "ListItemControl": "item", "HyperlinkControl": "link", "CheckBoxControl": "caixa de seleção",
              "RadioButtonControl": "opção", "ComboBoxControl": "lista", "TextControl": "texto",
              "TreeItemControl": "item", "SplitButtonControl": "botão", "DocumentControl": "documento",
              "DataItemControl": "item"}


def _norm(t: str) -> str:
    t = unicodedata.normalize("NFKD", (t or "").lower())
    return " ".join("".join(c for c in t if not unicodedata.combining(c)).split())


@contextmanager
def _uia() -> Iterator[Any]:
    """A UI Automation usa COM: cada thread precisa inicializar o seu."""
    import uiautomation as auto
    with auto.UIAutomationInitializerInThread(debug=False):
        yield auto


def _processo(pid: int) -> str:
    try:
        import psutil
        return psutil.Process(pid).name()
    except Exception:
        return ""


# --------------------------------------------------------------------------- janelas
def janelas() -> list[dict[str, Any]]:
    from .desktop import WINDOW_TITLE
    user32 = ctypes.windll.user32
    out: list[dict[str, Any]] = []
    Enum = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    def cb(hwnd, _):
        n = user32.GetWindowTextLengthW(hwnd)
        if n and user32.IsWindowVisible(hwnd) and not user32.GetWindow(hwnd, 4):     # 4 = GW_OWNER (pula diálogos-filho)
            buf = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(hwnd, buf, n + 1)
            titulo = buf.value.strip()
            pid = ctypes.c_ulong()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if titulo and not titulo.startswith((WINDOW_TITLE, "JARVIS Flutuante")) and titulo not in ("Program Manager", "Microsoft Text Input Application"):
                out.append({"hwnd": int(hwnd), "titulo": titulo, "app": _processo(pid.value),
                            "minimizada": bool(user32.IsIconic(hwnd))})
        return True

    user32.EnumWindows(Enum(cb), 0)
    return out


def listar() -> dict[str, Any]:
    if not IS_WINDOWS:
        return {"ok": False, "mensagem": "O controle de apps funciona só no Windows."}
    return {"ok": True, "janelas": [{k: v for k, v in j.items() if k != "hwnd"} for j in janelas()]}


def achar_janela(alvo: str) -> dict[str, Any] | None:
    """Pelo título ou pelo programa ("bloco de notas", "chrome", "calculadora"); a mais parecida vence."""
    a = _norm(alvo)
    if not a:
        return None
    apelidos = {"bloco de notas": "notepad", "calculadora": "calculator", "explorador": "explorer",
                "explorador de arquivos": "explorer", "word": "winword", "excel": "excel", "navegador": "chrome"}
    proc = apelidos.get(a, a).replace(" ", "")
    lista = janelas()
    for criterio in (lambda j: _norm(j["titulo"]) == a,
                     lambda j: _norm(j["app"]).removesuffix(".exe") == proc,
                     lambda j: a in _norm(j["titulo"]),
                     lambda j: proc in _norm(j["app"]).replace(" ", ""),
                     lambda j: all(p in _norm(j["titulo"]) for p in a.split())):
        achadas = [j for j in lista if criterio(j)]
        if achadas:
            return achadas[0]
    return None


def _para_frente(hwnd: int) -> bool:
    from .desktop import _para_frente as frente
    user32 = ctypes.windll.user32
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, 9)
        time.sleep(0.3)
    return frente(hwnd)


# --------------------------------------------------------------------------- ler a janela
def _controles(auto: Any, hwnd: int, limite: int = 120, segundos: float = 4.0) -> list[tuple[Any, str, str, str]]:
    janela = auto.ControlFromHandle(hwnd)
    fim = time.monotonic() + segundos
    out: list[tuple[Any, str, str, str]] = []
    for c, _prof in auto.WalkControl(janela, maxDepth=14):
        if time.monotonic() > fim or len(out) >= limite:
            break
        tipo = c.ControlTypeName
        if tipo not in TIPOS_UTEIS:
            continue
        nome = (c.Name or "").strip()
        valor = ""
        if tipo == "EditControl" or tipo == "DocumentControl":
            try:
                if not c.IsPassword:
                    valor = (c.GetValuePattern().Value or "")[:300]
            except Exception:
                valor = ""
        if not nome and not valor:
            continue
        try:
            if c.IsOffscreen and tipo not in ("EditControl", "DocumentControl"):
                continue
        except Exception:
            pass
        out.append((c, tipo, nome[:120], valor))
    return out


def ler(janela: str, limite: int = 80) -> dict[str, Any]:
    """O que a janela mostra: título e os controles com nome (e o texto dos campos)."""
    if not IS_WINDOWS:
        return {"ok": False, "mensagem": "O controle de apps funciona só no Windows."}
    j = achar_janela(janela)
    if j is None:
        return {"ok": False, "mensagem": f"Não achei uma janela aberta de '{janela}'. Janelas: "
                + ", ".join(x["titulo"][:40] for x in janelas()[:10])}
    with _uia() as auto:
        itens = _controles(auto, j["hwnd"], limite=max(10, min(int(limite or 80), 200)))
    controles = [{"tipo": NOMES_TIPO.get(t, t), "nome": n, **({"texto": v} if v else {})} for _c, t, n, v in itens]
    return {"ok": True, "janela": j["titulo"], "app": j["app"], "controles": controles}


# --------------------------------------------------------------------------- agir
def _achar_controle(auto: Any, hwnd: int, nome: str, tipos: set[str] | None = None) -> tuple[Any, str] | None:
    alvo = _norm(nome)
    itens = _controles(auto, hwnd, limite=400, segundos=6)
    if tipos:
        itens = [i for i in itens if i[1] in tipos]
    for criterio in (lambda n: n == alvo, lambda n: n.startswith(alvo), lambda n: alvo in n):
        for c, tipo, n, _v in itens:
            if n and criterio(_norm(n)):
                return c, n
    return None


def _digitar_unicode(texto: str) -> None:
    """Digita qualquer caractere (acentos, emoji) como se fosse o teclado. \\n vira Enter."""
    user32 = ctypes.windll.user32

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [("wVk", ctypes.c_ushort), ("wScan", ctypes.c_ushort), ("dwFlags", ctypes.c_ulong),
                    ("time", ctypes.c_ulong), ("dwExtraInfo", ctypes.c_size_t)]

    class INPUT(ctypes.Structure):
        class _U(ctypes.Union):
            _fields_ = [("ki", KEYBDINPUT), ("_pad", ctypes.c_byte * 32)]
        _anonymous_ = ("u",)
        _fields_ = [("type", ctypes.c_ulong), ("u", _U)]

    def enviar(vk: int, scan: int, flags: int) -> None:
        i = INPUT(type=1)
        i.ki = KEYBDINPUT(vk, scan, flags, 0, 0)
        user32.SendInput(1, ctypes.byref(i), ctypes.sizeof(INPUT))

    for ch in texto:
        if ch == "\n":
            enviar(0x0D, 0, 0)
            enviar(0x0D, 0, 2)
            continue
        bruto = ch.encode("utf-16-le")                       # emoji = 2 unidades (par substituto)
        codigos = [int.from_bytes(bruto[k:k + 2], "little") for k in range(0, len(bruto), 2)]
        for cod in codigos:
            enviar(0, cod, 0x0004)                 # KEYEVENTF_UNICODE
            enviar(0, cod, 0x0004 | 0x0002)
        time.sleep(0.005)


VK = {"ctrl": 0x11, "control": 0x11, "shift": 0x10, "alt": 0x12, "win": 0x5B, "enter": 0x0D, "tab": 0x09,
      "esc": 0x1B, "escape": 0x1B, "backspace": 0x08, "delete": 0x2E, "del": 0x2E, "space": 0x20, "espaco": 0x20,
      "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27, "cima": 0x26, "baixo": 0x28, "esquerda": 0x25,
      "direita": 0x27, "home": 0x24, "end": 0x23, "pageup": 0x21, "pagedown": 0x22}


def _combo(teclas: str) -> list[int] | None:
    vks = []
    for parte in _norm(teclas).replace(" ", "").split("+"):
        if parte in VK:
            vks.append(VK[parte])
        elif re.fullmatch(r"f([1-9]|1[0-2])", parte):
            vks.append(0x70 + int(parte[1:]) - 1)
        elif len(parte) == 1 and parte.isalnum():
            vks.append(ord(parte.upper()))
        else:
            return None
    return vks or None


def confirmacao(args: dict[str, Any]) -> str | None:
    """None = executa direto; texto terminado em '?' = pede confirmação; outro texto = recusa."""
    acao = str(args.get("acao", ""))
    if acao == "fechar":
        return f"Se houver algo não salvo, o app vai perguntar. Fechar a janela “{args.get('janela')}”?"
    if acao == "clicar" and RISCO.search(_norm(str(args.get("elemento", "")))):
        return f"Clicar em “{args.get('elemento')}” em “{args.get('janela')}”?"
    if acao == "teclas" and TECLAS_RISCO.search(str(args.get("texto", ""))):
        return f"Apertar {args.get('texto')} em “{args.get('janela')}”?"
    return None


def controlar(acao: str, janela: str, elemento: str = "", texto: str = "") -> dict[str, Any]:
    if not IS_WINDOWS:
        return {"ok": False, "mensagem": "O controle de apps funciona só no Windows."}
    j = achar_janela(janela)
    if j is None:
        return {"ok": False, "mensagem": f"Não achei uma janela aberta de '{janela}'. Abra o app antes (abrir_aplicativo)."}
    hwnd = j["hwnd"]
    user32 = ctypes.windll.user32
    log.info("Controle de app: %s em %s (%s)", acao, j["titulo"], elemento or texto[:40])
    if acao in ("focar", "minimizar", "maximizar", "restaurar"):
        if acao == "focar":
            _para_frente(hwnd)
        else:
            user32.ShowWindow(hwnd, {"minimizar": 6, "maximizar": 3, "restaurar": 9}[acao])
        return {"ok": True, "mensagem": f"{j['titulo']}: {acao} feito."}
    if acao == "fechar":
        user32.PostMessageW(hwnd, 0x0010, 0, 0)                # WM_CLOSE: o app pergunta se tiver algo não salvo
        return {"ok": True, "mensagem": f"Pedi para fechar {j['titulo']}."}
    with _uia() as auto:
        if acao == "clicar":
            achado = _achar_controle(auto, hwnd, elemento)
            if achado is None:
                return {"ok": False, "mensagem": f"Não achei '{elemento}' em {j['titulo']}. Use app_ler para ver os nomes."}
            c, nome = achado
            try:
                c.GetInvokePattern().Invoke()
            except Exception:
                try:
                    c.GetSelectionItemPattern().Select()
                except Exception:
                    _para_frente(hwnd)
                    c.Click(simulateMove=False)
            return {"ok": True, "mensagem": f"Cliquei em “{nome}” em {j['titulo']}."}
        if acao == "digitar":
            if not texto:
                return {"ok": False, "mensagem": "Faltou o texto para digitar."}
            if elemento:
                achado = _achar_controle(auto, hwnd, elemento, {"EditControl", "DocumentControl", "ComboBoxControl"})
                if achado is None:
                    return {"ok": False, "mensagem": f"Não achei o campo '{elemento}' em {j['titulo']}."}
                campo = achado[0]
                if campo.IsPassword:
                    return {"ok": False, "mensagem": "Esse é um campo de senha; senhas você digita."}
                _para_frente(hwnd)
                campo.SetFocus()
            else:
                _para_frente(hwnd)
                foco = auto.GetFocusedControl()
                if foco is not None and getattr(foco, "IsPassword", False):
                    return {"ok": False, "mensagem": "O cursor está num campo de senha; senhas você digita."}
            time.sleep(0.2)
            if ctypes.windll.user32.GetForegroundWindow() != hwnd and not elemento:
                return {"ok": False, "mensagem": f"Não consegui trazer {j['titulo']} para a frente para digitar."}
            _digitar_unicode(texto)
            return {"ok": True, "mensagem": f"Digitei em {j['titulo']}."}
        if acao == "teclas":
            vks = _combo(texto)
            if not vks:
                return {"ok": False, "mensagem": f"Não entendi as teclas '{texto}' (ex.: ctrl+s, alt+tab, enter, f5)."}
            _para_frente(hwnd)
            time.sleep(0.2)
            if user32.GetForegroundWindow() != hwnd:
                return {"ok": False, "mensagem": f"Não consegui trazer {j['titulo']} para a frente."}
            for vk in vks:
                user32.keybd_event(vk, 0, 0, 0)
            for vk in reversed(vks):
                user32.keybd_event(vk, 0, 2, 0)
            return {"ok": True, "mensagem": f"Apertei {texto} em {j['titulo']}."}
    return {"ok": False, "mensagem": "Ação inválida: focar, minimizar, maximizar, restaurar, fechar, clicar, digitar ou teclas."}
