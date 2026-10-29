"""Card em foco (controlar_cards focar/desfocar) e a janelinha flutuante (página e opção)."""
from fastapi.testclient import TestClient

from jarvis import cards
from jarvis.server import app

client = TestClient(app)


def test_focar_card_mostra_o_card_oculto():
    cards.salvar(cards.PADRAO)                                   # GitHub começa oculto
    r = cards.controlar("focar", "github")
    assert r["ok"] and r["foco"] == "github"
    assert "github" not in r["layout"]["ocultos"]


def test_focar_card_desconhecido_e_desfocar():
    assert not cards.controlar("focar", "geladeira")["ok"]
    r = cards.controlar("desfocar")
    assert r["ok"] and r["foco"] == ""


def test_pagina_flutuante():
    r = client.get("/flutuante")
    assert r.status_code == 200
    assert "<title>JARVIS Flutuante</title>" in r.text      # o flutuante.py acha a janela por este título


def test_opcao_flutuante_liga_e_desliga():
    assert client.post("/api/flutuante", json={"ativo": False}).json()["ativo"] is False
    assert client.get("/api/config").json()["flutuante"] is False
    assert client.post("/api/flutuante", json={"ativo": True}).json()["ativo"] is True


def test_regiao_sem_janela_nao_quebra():
    r = client.post("/api/flutuante/regiao", json={"formas": [[[0, 0], [50, 0], [50, 40], [0, 40]]], "dpr": 1.25})
    assert r.status_code == 200 and r.json() == {"ok": False}


def _ate(ws, tipo):
    for _ in range(20):
        m = ws.receive_json()
        if m.get("type") == tipo:
            return m
    raise AssertionError(f"não chegou {tipo}")


def test_ponte_entre_flutuante_e_janela_principal():
    origem = {"origin": "http://127.0.0.1:8000"}
    with client.websocket_connect("/ws", headers=origem) as principal:
        _ate(principal, "hello")
        with client.websocket_connect("/ws/flutuante", headers=origem) as flut:
            assert _ate(principal, "flutuante")["tipo"] == "ola"           # a flutuante pede o estado
            flut.send_json({"tipo": "voz", "ligado": False})
            m = _ate(principal, "flutuante")
            assert m["tipo"] == "voz" and m["ligado"] is False
            principal.send_json({"type": "flutuante", "tipo": "estado", "estado": "thinking", "voz": False})
            assert flut.receive_json() == {"tipo": "estado", "estado": "thinking", "voz": False}


def test_flutuante_recusa_outro_site():
    import pytest
    from starlette.websockets import WebSocketDisconnect
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws/flutuante", headers={"origin": "https://site-qualquer.com"}) as ws:
            ws.receive_json()


def test_confirmacao_pela_flutuante_chega_na_janela_principal():
    origem = {"origin": "http://127.0.0.1:8000"}
    with client.websocket_connect("/ws", headers=origem) as principal:
        _ate(principal, "hello")
        with client.websocket_connect("/ws/flutuante", headers=origem) as flut:
            _ate(principal, "flutuante")                                     # "ola"
            flut.send_json({"tipo": "confirmar", "aprovar": True})
            m = _ate(principal, "flutuante")
            assert m["tipo"] == "confirmar" and m["aprovar"] is True


def test_link_de_card_na_flutuante_abre_no_navegador_interno():
    origem = {"origin": "http://127.0.0.1:8000"}
    with client.websocket_connect("/ws", headers=origem) as principal:
        _ate(principal, "hello")
        with client.websocket_connect("/ws/flutuante", headers=origem) as flut:
            _ate(principal, "flutuante")                                     # "ola"
            flut.send_json({"tipo": "abrir_web", "url": "javascript:alert(1)"})
            assert _ate(principal, "flutuante")["url"] == ""                 # só http/https passa
            flut.send_json({"tipo": "abrir_web", "url": "https://g1.globo.com/x"})
            assert _ate(principal, "flutuante")["url"] == "https://g1.globo.com/x"
