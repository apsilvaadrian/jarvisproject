"""Navegador da janela e do WhatsApp Web: Chrome primeiro, Edge de reserva, perfis separados e a escolha nas Configurações."""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import jarvis.config as cfg
from jarvis import app, desktop, server, whatsapp

CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"


@pytest.fixture
def instalados(monkeypatch):
    def com(*exes):
        monkeypatch.setattr(app.os, "name", "nt")
        monkeypatch.setattr(app.Path, "is_file", lambda self: str(self).replace("/", "\\") in exes)
        monkeypatch.setenv("PROGRAMFILES", r"C:\Program Files")
        monkeypatch.setenv("PROGRAMFILES(X86)", r"C:\Program Files (x86)")
        monkeypatch.setenv("LOCALAPPDATA", r"C:\Users\x\AppData\Local")
    return com


def test_chrome_primeiro_edge_de_reserva(instalados, monkeypatch):
    monkeypatch.setattr(cfg.settings, "navegador", "auto")
    instalados(CHROME, EDGE)
    assert [Path(b).name for b in app.browsers()] == ["chrome.exe", "msedge.exe"]
    instalados(EDGE)                                                          # PC sem Chrome
    assert [Path(b).name for b in app.browsers()] == ["msedge.exe"]


def test_escolha_edge_nas_configuracoes(instalados, monkeypatch):
    instalados(CHROME, EDGE)
    monkeypatch.setattr(cfg.settings, "navegador", "edge")
    assert [Path(b).name for b in app.browsers()] == ["msedge.exe", "chrome.exe"]
    monkeypatch.setattr(cfg.settings, "navegador", "auto")


def test_cada_navegador_com_seu_perfil():
    base = Path("dados") / "navegador"
    assert app.perfil(base, EDGE) == base                                     # Edge mantém a pasta antiga
    assert app.perfil(base, CHROME) == Path("dados") / "navegador-chrome"
    assert app.tipo_navegador(CHROME) == "chrome" and app.tipo_navegador(EDGE) == "edge"


def test_fechar_um_perfil_nao_fecha_o_outro(monkeypatch):
    fechados = []

    class Proc:
        def __init__(self, cmd):
            self.info = {"name": "chrome.exe", "cmdline": cmd.split()}

        def terminate(self):
            fechados.append(" ".join(self.info["cmdline"]))

    import psutil
    procs = [Proc(r"chrome.exe --user-data-dir=C:\j\navegador-chrome --app=x"),
             Proc(r"msedge.exe --user-data-dir=C:\j\navegador --app=x"),
             Proc(r"msedge.exe --type=renderer --user-data-dir=C:\j\navegador")]
    monkeypatch.setattr(psutil, "process_iter", lambda attrs: procs)
    desktop.close_browser_windows(Path(r"C:\j\navegador"))
    assert fechados == [r"msedge.exe --user-data-dir=C:\j\navegador --app=x"]


def test_whatsapp_tenta_o_edge_se_o_chrome_nao_conectar(monkeypatch):
    abertos = []
    monkeypatch.setattr(app, "browsers", lambda: [CHROME, EDGE])
    monkeypatch.setattr(desktop, "depuracao_bloqueada", lambda exe: False)
    monkeypatch.setattr(desktop, "close_browser_windows", lambda p: None)

    def abrir(self, exe, limite):
        abertos.append((Path(exe).name, self.perfil.name))
        return "msedge" in exe                                               # o Chrome "não conecta"

    monkeypatch.setattr(whatsapp._JanelaWeb, "_abrir", abrir)
    with whatsapp._JanelaWeb("https://web.whatsapp.com", 60) as j:
        assert j.perfil.name == "whatsapp-web"
    assert abertos == [("chrome.exe", "whatsapp-web-chrome"), ("msedge.exe", "whatsapp-web")]


def test_whatsapp_pula_navegador_bloqueado_pela_empresa(monkeypatch):
    abertos = []
    monkeypatch.setattr(app, "browsers", lambda: [CHROME, EDGE])
    monkeypatch.setattr(desktop, "depuracao_bloqueada", lambda exe: "chrome" in exe)
    monkeypatch.setattr(desktop, "close_browser_windows", lambda p: None)
    monkeypatch.setattr(whatsapp._JanelaWeb, "_abrir", lambda self, exe, limite: abertos.append(Path(exe).name) or True)
    with whatsapp._JanelaWeb("https://web.whatsapp.com", 60):
        pass
    assert abertos == ["msedge.exe"]


def test_escolha_do_navegador_pela_tela(monkeypatch):
    monkeypatch.setattr(app, "browsers", lambda: [CHROME, EDGE])
    reaberto = []
    monkeypatch.setitem(server.ganchos, "reabrir", lambda: reaberto.append(True))
    client = TestClient(server.app)
    r = client.post("/api/navegador", json={"escolha": "edge"})
    assert r.status_code == 200 and r.json()["reabrindo"] and r.json()["instalados"] == ["chrome", "edge"]
    assert cfg.settings.navegador == "edge" and "JARVIS_NAVEGADOR=edge" in cfg.ENV_FILE.read_text(encoding="utf-8")
    assert client.post("/api/navegador", json={"escolha": "firefox"}).status_code == 400
    monkeypatch.setattr(cfg.settings, "navegador", "auto")
