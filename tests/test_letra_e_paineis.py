"""Letra sincronizada (LRCLIB simulado) e os eventos que trocam os cards da interface, sem rede."""
import httpx
from fastapi.testclient import TestClient

from jarvis import letras, server, spotify, web_tools
from jarvis.brain.local import LocalBrain
from jarvis.tools import ToolExecutor

LRC = "[00:01.50] Primeiro verso\n[00:04.00][00:20.00] Refrão de teste\n[00:08.25] Terceiro verso\nlinha sem tempo"


def test_converte_lrc_em_versos_com_tempo():
    linhas = letras.parse_lrc(LRC)
    assert [x["t"] for x in linhas] == [1500, 4000, 8250, 20000]
    assert linhas[1]["texto"] == linhas[3]["texto"] == "Refrão de teste"


def test_busca_tira_remaster_e_cai_na_pesquisa(monkeypatch):
    pedidos = []

    def fake_get(url, params=None, **kw):
        pedidos.append((url, dict(params or {})))
        req = httpx.Request("GET", url)
        if url.endswith("/get"):
            return httpx.Response(404, json={}, request=req)
        return httpx.Response(200, json=[{"duration": 300, "plainLyrics": "sem tempo"},
                                         {"duration": 201, "syncedLyrics": LRC}], request=req)

    monkeypatch.setattr(letras.httpx, "get", fake_get)
    monkeypatch.setattr(letras, "_cache", {})
    r = letras.buscar("Banda X, Convidado", "Canção - Remastered 2011", "Álbum", 200000)
    assert r["ok"] and r["sincronizada"] and len(r["linhas"]) == 4
    assert pedidos[0][1]["track_name"] == "Canção" and pedidos[0][1]["artist_name"] == "Banda X"
    assert pedidos[0][1]["duration"] == 200


def test_ferramenta_mostra_na_tela_sem_devolver_a_letra(monkeypatch):
    monkeypatch.setattr(spotify, "player_state", lambda: {"conectado": True, "ativo": True, "musica": "Canção",
                                                          "artistas": "Banda X", "album": "", "duracao_ms": 1000})
    monkeypatch.setattr(letras, "buscar", lambda *a: {"ok": True, "sincronizada": True, "musica": "Canção",
                                                      "artista": "Banda X", "linhas": [{"t": 0, "texto": "verso"}]})
    eventos = []
    r = ToolExecutor(eventos.append).run("mostrar_letra", {})
    assert r["ok"] and "linhas" not in r and "verso" not in str(r)     # o cérebro não recebe a letra
    assert {"type": "painel", "tipo": "letra", "dados": r} in eventos


def test_sem_musica_tocando_nao_abre_o_quadro(monkeypatch):
    monkeypatch.setattr(spotify, "player_state", lambda: {"conectado": True, "ativo": False})
    eventos = []
    r = ToolExecutor(eventos.append).run("mostrar_letra", {})
    assert not r["ok"] and "Nada tocando" in r["mensagem"]
    assert not any(e["type"] == "painel" for e in eventos)


def test_endpoint_da_letra(monkeypatch):
    monkeypatch.setattr(letras, "da_musica_atual", lambda: {"ok": True, "sincronizada": True, "linhas": []})
    assert TestClient(server.app).get("/api/spotify/letra").json()["sincronizada"]


def test_noticias_e_clima_trocam_os_cards(monkeypatch):
    monkeypatch.setattr(web_tools, "noticias", lambda tema="", limite=6: {"ok": True, "noticias": [
        {"titulo": "Manchete", "fonte": "G1", "quando": "", "link": ""}]})
    eventos = []
    LocalBrain().respond([{"role": "user", "text": "quais as notícias?"}], ToolExecutor(eventos.append))
    painel = [e for e in eventos if e["type"] == "painel"]
    assert painel and painel[0]["tipo"] == "noticias" and painel[0]["dados"]["noticias"][0]["titulo"] == "Manchete"


def test_status_do_sistema_volta_aos_cards_de_hardware():
    eventos = []
    ToolExecutor(eventos.append).run("status_sistema", {})
    assert any(e["type"] == "painel" and e["tipo"] == "sistema" for e in eventos)


def test_pedido_de_letra_no_modo_offline(monkeypatch):
    pedidos = []
    monkeypatch.setattr(letras, "mostrar", lambda acao="mostrar": pedidos.append(acao) or {"ok": True, "mensagem": "ok"})
    brain = LocalBrain()
    ex = ToolExecutor(lambda e: None)
    brain.respond([{"role": "user", "text": "mostra a letra dessa música"}], ex)
    brain.respond([{"role": "user", "text": "fecha a letra"}], ex)
    assert pedidos == ["mostrar", "fechar"]
