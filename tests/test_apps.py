"""Controle de apps (UI Automation): achar janela, atalhos e quais ações pedem confirmação — sem mexer em app real."""
import pytest

from jarvis import apps
from jarvis.tools import ToolExecutor

JANELAS = [{"hwnd": 1, "titulo": "Sem título - Bloco de Notas", "app": "Notepad.exe", "minimizada": False},
           {"hwnd": 2, "titulo": "Calculadora", "app": "ApplicationFrameHost.exe", "minimizada": False},
           {"hwnd": 3, "titulo": "Relatório.xlsx - Excel", "app": "EXCEL.EXE", "minimizada": True}]


@pytest.fixture(autouse=True)
def janelas_falsas(monkeypatch):
    monkeypatch.setattr(apps, "janelas", lambda: JANELAS)


@pytest.mark.parametrize("pedido, hwnd", [("bloco de notas", 1), ("calculadora", 2), ("excel", 3),
                                          ("relatorio", 3), ("notepad", 1)])
def test_acha_a_janela_pelo_nome_ou_programa(pedido, hwnd):
    assert apps.achar_janela(pedido)["hwnd"] == hwnd


def test_janela_inexistente():
    assert apps.achar_janela("photoshop") is None


@pytest.mark.parametrize("combo, vks", [("ctrl+s", [0x11, ord("S")]), ("alt+tab", [0x12, 0x09]), ("Enter", [0x0D]),
                                        ("ctrl+shift+t", [0x11, 0x10, ord("T")]), ("f5", [0x74])])
def test_atalhos(combo, vks):
    assert apps._combo(combo) == vks


def test_atalho_invalido():
    assert apps._combo("ctrl+banana") is None


@pytest.mark.parametrize("args, pede", [
    ({"acao": "fechar", "janela": "excel"}, True),
    ({"acao": "clicar", "janela": "x", "elemento": "Excluir"}, True),
    ({"acao": "clicar", "janela": "x", "elemento": "Enviar e-mail"}, True),
    ({"acao": "clicar", "janela": "x", "elemento": "Não salvar"}, True),
    ({"acao": "clicar", "janela": "x", "elemento": "Negrito"}, False),
    ({"acao": "teclas", "janela": "x", "texto": "alt+f4"}, True),
    ({"acao": "teclas", "janela": "x", "texto": "ctrl+s"}, False),
    ({"acao": "digitar", "janela": "x", "texto": "olá"}, False),
    ({"acao": "minimizar", "janela": "x"}, False),
])
def test_quais_acoes_pedem_confirmacao(args, pede):
    texto = apps.confirmacao(args)
    assert (texto is not None and texto.endswith("?")) == pede


def test_acao_comum_executa_direto_e_arriscada_espera_o_sim(monkeypatch):
    feitas = []
    monkeypatch.setattr(apps, "controlar", lambda acao, janela, elemento="", texto="": feitas.append(acao) or {"ok": True})
    ex = ToolExecutor(lambda e: None)
    assert ex.run("app_controlar", {"acao": "minimizar", "janela": "excel"})["ok"] and feitas == ["minimizar"]
    r = ex.run("app_controlar", {"acao": "fechar", "janela": "excel"})
    assert r["status"] == "aguardando_confirmacao" and feitas == ["minimizar"]
    ex.resolve_latest(True)
    assert feitas == ["minimizar", "fechar"]
