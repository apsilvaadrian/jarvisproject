"""Atalhos diretos (letra, acordar), cards personalizados montados pela IA e o modo da janela."""
from fastapi.testclient import TestClient

import jarvis.config as cfg
from jarvis import cards, desktop, letras, server, tools
from jarvis.assistant import Assistant
from jarvis.brain.base import Brain
from jarvis.tools import ToolExecutor


class CerebroQueRecusa(Brain):
    """Simula o Gemini respondendo sozinho ("não posso mostrar letras") sem chamar ferramentas."""
    name = "teste"

    def respond(self, history, executor):
        return "Não posso fornecer letras de músicas."


def _assistente(eventos):
    a = Assistant(eventos.append)
    a.brain = CerebroQueRecusa()
    return a


def test_pedido_de_letra_abre_o_quadro_sem_depender_da_ia(monkeypatch):
    monkeypatch.setattr(letras, "mostrar", lambda acao="mostrar": {"ok": True, "acao": acao, "musica": "Back In Black",
                                                                    "tipo": "sincronizada", "mensagem": "ok"})
    eventos = []
    a = _assistente(eventos)
    resposta = a.chat("Jarvis, me dá a letra dessa música")
    assert "Back In Black" in resposta and "tela" in resposta
    assert any(e["type"] == "painel" and e["tipo"] == "letra" for e in eventos)
    assert "fechado" in a.chat("fecha a letra")
    assert "Não posso" in a.chat("como se escreve a letra maiúscula?")          # não é sobre música: vai para a IA


def test_acorda_traz_a_janela(monkeypatch):
    chamadas = []
    monkeypatch.setitem(server.ganchos, "mostrar", lambda: chamadas.append("mostrar"))
    a = _assistente([])
    assert "ordens" in a.chat("Jarvis, acorda!")
    assert "ordens" in a.chat("mostra a interface")
    assert chamadas == ["mostrar", "mostrar"]
    assert "Não posso" in a.chat("acordei cedo hoje")                             # conversa normal


def test_card_personalizado_de_tabela():
    eventos = []
    r = ToolExecutor(eventos.append).run("mostrar_card", {
        "titulo": "Fórmula 1 — Pilotos", "tipo": "tabela", "colunas": ["Pos", "Piloto", "Pts"],
        "linhas": [["1", "Kimi Antonelli", "340"], ["2", "George Russell", "238"]], "fonte": "formula1.com",
        "destacar": "Russell"})
    assert r["ok"] and "linhas" not in r["mensagem"]
    painel = next(e for e in eventos if e["type"] == "painel")
    assert painel["tipo"] == "custom" and painel["dados"]["card"]["linhas"][1] == ["2", "George Russell", "238"]


def test_card_personalizado_valida_e_limita():
    assert not cards.personalizado("Vazia", "tabela", ["a"], [])["ok"]
    grande = cards.personalizado("Grande", "tabela", ["c"] * 20, [["x" * 500] * 20] * 100)["card"]
    assert len(grande["colunas"]) == 8 and len(grande["linhas"]) == 40 and len(grande["linhas"][0][0]) == 60
    lista = cards.personalizado("Top", "lista", itens=[{"titulo": "A", "valor": "1"}, {"sem": "titulo"}])["card"]
    assert lista["itens"] == [{"titulo": "A", "detalhe": "", "valor": "1"}]
    assert cards.personalizado("Nota", "qualquer", texto="", linhas=[["1"]])["card"]["tipo"] == "tabela"


def test_ferramenta_de_card_aceita_arrays_no_schema():
    spec = next(t for t in tools.TOOLS if t.name == "mostrar_card").parameters
    assert spec["properties"]["linhas"]["items"]["type"] == "array"
    assert set(spec["required"]) == {"titulo", "tipo"}


def test_modo_da_janela_pelas_configuracoes(monkeypatch, tmp_path):
    monkeypatch.setattr(cfg, "ENV_FILE", tmp_path / ".env")
    monkeypatch.setattr(desktop, "aplicar_modo_agora", lambda modo: True)
    client = TestClient(server.app)
    r = client.post("/api/janela", json={"modo": "cheia"})
    assert r.status_code == 200 and r.json()["aplicado"]
    assert cfg.settings.janela == "cheia" and "JARVIS_JANELA=cheia" in (tmp_path / ".env").read_text(encoding="utf-8")
    assert client.get("/api/config").json()["janela"] == "cheia"
    assert client.post("/api/janela", json={"modo": "gigante"}).status_code == 400
    monkeypatch.setattr(cfg.settings, "janela", "maximizada")


def test_modo_janela_centralizado(monkeypatch):
    monkeypatch.setattr(desktop, "_tela", lambda: (1920, 1080))
    monkeypatch.setattr(desktop, "IS_WINDOWS", True)
    x, y, w, h = desktop.tamanho_janela()
    assert (w, h) == (1600, 918) and x == 160 and y == 81
