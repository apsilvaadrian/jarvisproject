"""Extensão do WhatsApp (ponte por WebSocket) e o bloqueio de acesso que não venha deste computador."""
import threading

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import jarvis.config as cfg
from jarvis import server, whatsapp

client = TestClient(server.app)


def test_site_de_fora_nao_usa_a_api():
    assert client.get("/api/config").status_code == 200
    assert client.get("/api/config", headers={"host": "evil.com"}).status_code == 403              # DNS rebinding
    assert client.get("/api/config", headers={"origin": "https://evil.com"}).status_code == 403
    assert client.get("/api/config", headers={"origin": "http://127.0.0.1:8000"}).status_code == 200


def test_site_de_fora_nao_abre_o_websocket():
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws", headers={"origin": "https://evil.com"}) as ws:
            ws.receive_json()
    with client.websocket_connect("/ws", headers={"origin": "http://127.0.0.1:8000"}) as ws:
        assert ws.receive_json()["type"] == "hello"


def test_ponte_so_aceita_extensao():
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws/whatsapp", headers={"origin": "https://web.whatsapp.com"}) as ws:
            ws.receive_json()


def test_ponte_ida_e_volta_com_a_extensao(monkeypatch):
    monkeypatch.setattr(cfg.settings, "data_dir", cfg.settings.data_dir)
    respostas = {}
    with client.websocket_connect("/ws/whatsapp", headers={"origin": "chrome-extension://abc"}) as ext:
        ext.send_json({"type": "ola", "versao": "1.0.0", "navegador": "Mozilla Chrome/156"})
        for _ in range(50):                                                   # espera a ponte registrar
            if whatsapp.PONTE.conectada():
                break
            threading.Event().wait(0.05)
        assert client.get("/api/whatsapp").json()["extensao"]["conectada"]

        t = threading.Thread(target=lambda: respostas.update(r=whatsapp._enviar_extensao("Marine", "chego às 8")))
        t.start()
        pedido = ext.receive_json()                                           # 1) buscar
        assert pedido["type"] == "buscar" and pedido["nome"] == "Marine"
        ext.send_json({"type": "resultado", "id": pedido["id"], "ok": True, "titulos": ["Marini", "Mariana Lopes"]})
        pedido = ext.receive_json()                                           # 2) enviar para a conversa escolhida
        assert pedido == {"type": "enviar_para", "titulo": "Marini", "mensagem": "chego às 8", "id": pedido["id"]}
        ext.send_json({"type": "resultado", "id": pedido["id"], "ok": True, "conferido": True, "conversa": "Marini"})
        t.join(10)
    assert respostas["r"]["ok"] and respostas["r"]["conversa"] == "Marini"
    assert not whatsapp.PONTE.conectada()                                     # desconectou ao fechar


def test_motivos_da_extensao_viram_mensagens(monkeypatch):
    monkeypatch.setattr(whatsapp.PONTE, "pedir", lambda msg, timeout=30: {"ok": False, "motivo": "login"})
    assert "desconectado" in whatsapp._enviar_extensao("Ana", "oi")["mensagem"]
    respostas = iter([{"ok": True, "titulos": ["Ana"]}, {"ok": False, "motivo": "conversa_errada"}])
    monkeypatch.setattr(whatsapp.PONTE, "pedir", lambda msg, timeout=30: next(respostas))
    assert "não enviei nada" in whatsapp._enviar_extensao("Ana", "oi")["mensagem"]


def test_com_extensao_usa_ela_e_nao_a_janela_propria(monkeypatch):
    usados = []
    monkeypatch.setattr(cfg.settings, "whatsapp_modo", "auto")
    monkeypatch.setattr(whatsapp.PONTE, "conectada", lambda: True)
    monkeypatch.setattr(whatsapp, "_enviar_extensao", lambda n, m, p=None: usados.append(("ext", n)) or {"ok": True, "conversa": n})
    monkeypatch.setattr(whatsapp, "_enviar_web", lambda *a, **k: usados.append(("janela",)) or {"ok": True})
    assert whatsapp.enviar("Família Souza", "oi")["contato"] == "Família Souza"
    assert usados == [("ext", "Família Souza")]


def test_extensao_tem_os_arquivos():
    import json
    pasta = whatsapp.EXTENSAO_ORIGEM
    m = json.loads((pasta / "manifest.json").read_text(encoding="utf-8"))
    assert m["manifest_version"] == 3 and "https://web.whatsapp.com/*" in m["host_permissions"]
    for arq in ("background.js", "content.js", "icon.png"):
        assert (pasta / arq).is_file()
