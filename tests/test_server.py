"""API HTTP e WebSocket."""
from fastapi.testclient import TestClient

from jarvis.server import app

client = TestClient(app)


def test_health():
    r = client.get("/health")
    assert r.status_code == 200 and r.json()["status"] == "online"


def test_interface_servida():
    r = client.get("/")
    assert r.status_code == 200 and "J.A.R.V.I.S." in r.text
    assert client.get("/static/app.js").status_code == 200


def test_config_sem_chaves_usa_offline():
    cfg = client.get("/api/config").json()
    assert cfg["default_brain"] == "local"
    assert any(b["id"] == "local" for b in cfg["brains"])


def test_config_lista_todos_os_cerebros():
    brains = {b["id"]: b for b in client.get("/api/config").json()["brains"]}
    assert set(brains) == {"claude", "openai", "gemini", "groq", "openrouter", "ollama", "claude-conta", "openai-conta",
                           "gemini-conta", "local"}
    assert brains["local"]["configured"] and not brains["claude"]["configured"]
    assert {b["tipo"] for b in brains.values()} == {"api", "conta", "local"}


def test_chave_invalida_recusada_sem_rede():
    assert client.post("/api/brain-key", json={"brain": "skynet", "key": "x" * 20}).status_code == 400
    assert client.post("/api/brain-key", json={"brain": "claude", "key": "curta"}).status_code == 400
    assert client.post("/api/brain-key", json={"brain": "claude", "key": "abc def ghi jkl"}).status_code == 400


def test_atualiza_env_preservando_outras_linhas(tmp_path):
    from jarvis.config import update_env_file

    env = tmp_path / ".env"
    env.write_text("# comentario\nJARVIS_PORT=8000\nANTHROPIC_API_KEY=\n", encoding="utf-8")
    update_env_file(env, {"ANTHROPIC_API_KEY": "sk-nova\nINJETADO=1", "ANTHROPIC_MODEL": "modelo-x"})
    linhas = env.read_text(encoding="utf-8").splitlines()
    assert linhas[:2] == ["# comentario", "JARVIS_PORT=8000"]
    assert "ANTHROPIC_API_KEY=sk-novaINJETADO=1" in linhas      # quebra de linha removida
    assert "ANTHROPIC_MODEL=modelo-x" in linhas
    assert not any(l.startswith("INJETADO") for l in linhas)


def test_filtra_so_modelos_de_chat():
    from jarvis.brain import filter_models

    gemini = {"models": [
        {"name": "models/gemini-2.5-flash", "displayName": "Gemini 2.5 Flash", "supportedGenerationMethods": ["generateContent"]},
        {"name": "models/gemini-2.5-flash-preview-tts", "supportedGenerationMethods": ["generateContent"]},
        {"name": "models/text-embedding-004", "supportedGenerationMethods": ["embedContent"]},
        {"name": "models/gemini-2.5-pro", "displayName": "Gemini 2.5 Pro", "supportedGenerationMethods": ["generateContent"]},
        {"name": "models/gemini-3.1-pro-preview", "supportedGenerationMethods": ["generateContent"]},
        {"name": "models/gemini-3.1-flash", "supportedGenerationMethods": ["generateContent"]},
        {"name": "models/gemini-flash-latest", "supportedGenerationMethods": ["generateContent"]},
        {"name": "models/gemini-nano-banana-2", "supportedGenerationMethods": ["generateContent"]},
    ]}
    # versão mais nova primeiro, estável antes de preview, apelidos no fim, modelos de imagem fora
    assert [m["id"] for m in filter_models("gemini", gemini)] == [
        "gemini-3.1-flash", "gemini-3.1-pro-preview", "gemini-2.5-pro", "gemini-2.5-flash", "gemini-flash-latest"]

    openai = {"data": [{"id": "gpt-5-mini", "created": 2}, {"id": "whisper-1", "created": 3},
                       {"id": "gpt-4o-realtime-preview", "created": 4}, {"id": "o4-mini", "created": 1}]}
    assert [m["id"] for m in filter_models("openai", openai)] == ["gpt-5-mini", "o4-mini"]

    claude = {"data": [{"id": "claude-sonnet-5-5", "display_name": "Claude Sonnet 5.5"}]}
    assert filter_models("claude", claude) == [{"id": "claude-sonnet-5-5", "label": "Claude Sonnet 5.5"}]


def test_troca_so_o_modelo_sem_reenviar_chave(monkeypatch, tmp_path):
    import jarvis.config as cfg

    monkeypatch.setattr(cfg, "ENV_FILE", tmp_path / ".env")
    monkeypatch.setattr(cfg.settings, "gemini_key", "chave-ja-salva-123456")
    monkeypatch.setattr(cfg.settings, "gemini_model", "gemini-2.5-flash")
    r = client.post("/api/brain-key", json={"brain": "gemini", "model": "gemini-2.5-pro"})
    assert r.status_code == 200
    assert cfg.settings.gemini_model == "gemini-2.5-pro"
    assert "GEMINI_MODEL=gemini-2.5-pro" in (tmp_path / ".env").read_text(encoding="utf-8")
    # sem chave salva e sem chave enviada, recusa
    monkeypatch.setattr(cfg.settings, "anthropic_key", "")
    assert client.post("/api/brain-key", json={"brain": "claude", "model": "x"}).status_code == 400
    assert client.post("/api/models", json={"brain": "claude"}).status_code == 400


def test_voz_clonada_desligada_cai_para_navegador(monkeypatch):
    from jarvis.config import settings

    monkeypatch.setattr(settings, "voz_url", "http://127.0.0.1:9")   # porta sem servidor
    assert client.get("/api/voz").json()["disponivel"] is False
    assert client.post("/api/tts", json={"text": "teste"}).status_code == 503


def test_stats():
    s = client.get("/api/stats").json()
    assert 0 <= s["cpu"]["percent"] <= 100


def test_processos_limitados():
    procs = client.get("/api/processes?limit=3").json()
    assert len(procs) <= 3


def test_websocket_conversa():
    with client.websocket_connect("/ws") as ws:
        assert ws.receive_json()["type"] == "hello"
        ws.send_json({"type": "chat", "text": "que horas são?"})
        for _ in range(20):
            msg = ws.receive_json()
            if msg["type"] == "reply":
                assert "São" in msg["text"]
                break
        else:
            raise AssertionError("sem resposta do JARVIS")
