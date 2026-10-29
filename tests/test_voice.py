"""Voz: ElevenLabs com reserva na voz local, sem rede real (transporte HTTP simulado)."""
import json

import httpx
import pytest
from fastapi.testclient import TestClient

import jarvis.config as cfg
from jarvis import voice
from jarvis.server import app

client = TestClient(app)
MP3 = b"ID3-audio-falso"
WAV = b"RIFF-audio-local"


@pytest.fixture
def rede(monkeypatch, tmp_path):
    """Simula ElevenLabs + servidor de voz local. `estado` controla as respostas."""
    estado = {"eleven": 200, "local": 200, "pedidos": []}

    def handler(req: httpx.Request):
        estado["pedidos"].append((req.method, req.url.host, req.url.path, req.content))
        if req.url.host == "api.elevenlabs.io":
            if req.url.path.endswith("/voices"):
                return httpx.Response(200, json={"voices": [
                    {"voice_id": "pronta1", "name": "Adam", "category": "premade"},
                    {"voice_id": "minha1", "name": "JARVIS", "category": "cloned"}]})
            if req.url.path.endswith("/models"):
                return httpx.Response(200, json=[
                    {"model_id": "eleven_flash_v2_5", "name": "Flash v2.5", "can_do_text_to_speech": True,
                     "languages": [{"language_id": "pt"}]},
                    {"model_id": "eleven_english_sts", "can_do_text_to_speech": False}])
            if req.url.path.startswith("/v1/voices/"):
                return httpx.Response(200 if req.url.path.endswith("minha1") else 404, json={})
            if estado["eleven"] != 200:
                return httpx.Response(estado["eleven"], json={"detail": {"status": "quota_exceeded"}})
            corpo = json.loads(req.content)
            if corpo.get("model_id") == "eleven_v3" and ("previous_text" in corpo or "next_text" in corpo):
                return httpx.Response(400, json={"detail": {"status": "unsupported_model", "message":
                    "Providing previous_text or next_text is not yet supported with the 'eleven_v3' model."}})
            if corpo.get("model_id") == "modelo_ruim":
                return httpx.Response(400, json={"detail": {"status": "invalid", "message": "model_id inválido"}})
            return httpx.Response(200, content=MP3)
        return httpx.Response(estado["local"], content=WAV)

    monkeypatch.setattr(voice, "_transport", httpx.MockTransport(handler))
    monkeypatch.setattr(voice, "_cache", voice.OrderedDict())
    monkeypatch.setattr(cfg, "ENV_FILE", tmp_path / ".env")
    return estado


def _configurar(monkeypatch):
    monkeypatch.setattr(cfg.settings, "elevenlabs_key", "xi-chave-de-teste-123456")
    monkeypatch.setattr(cfg.settings, "elevenlabs_voice_id", "minha1")


def test_elevenlabs_gera_mp3_com_contexto(rede, monkeypatch):
    _configurar(monkeypatch)
    r = client.post("/api/tts", json={"text": "Bom dia, senhor.", "engine": "elevenlabs", "next": "Tudo pronto."})
    assert r.status_code == 200 and r.content == MP3
    assert r.headers["x-voz-motor"] == "elevenlabs" and r.headers["content-type"] == "audio/mpeg"
    corpo = json.loads(rede["pedidos"][-1][3])
    assert corpo["model_id"] == "eleven_flash_v2_5" and corpo["next_text"] == "Tudo pronto."


def test_modelo_sem_suporte_a_contexto_repete_sem_contexto(rede, monkeypatch):
    """O erro 400 visto no PC do trabalho: eleven_v3 recusa previous_text/next_text."""
    _configurar(monkeypatch)
    monkeypatch.setattr(cfg.settings, "elevenlabs_model", "eleven_v3")
    monkeypatch.setattr(voice, "_sem_contexto", set())
    r = client.post("/api/tts", json={"text": "Olá.", "engine": "elevenlabs", "previous": "Bom dia.", "next": "Tudo bem?"})
    assert r.status_code == 200 and r.headers["x-voz-motor"] == "elevenlabs"
    assert "eleven_v3" in voice._sem_contexto
    antes = len(rede["pedidos"])
    client.post("/api/tts", json={"text": "Outra frase.", "engine": "elevenlabs", "previous": "Olá."})
    assert len(rede["pedidos"]) == antes + 1          # da 2ª vez já vai sem contexto, sem erro


def test_erro_mostra_o_motivo_do_elevenlabs(rede, monkeypatch):
    monkeypatch.setattr(cfg.settings, "elevenlabs_key", "")
    r = client.post("/api/elevenlabs", json={"key": "xi-chave-de-teste-123456", "voice_id": "minha1", "model": "modelo_ruim"})
    assert r.status_code == 400 and "model_id inválido" in r.json()["detail"]


def test_frase_repetida_vem_do_cache_sem_gastar_creditos(rede, monkeypatch):
    _configurar(monkeypatch)
    for _ in range(3):
        client.post("/api/tts", json={"text": "Às suas ordens.", "engine": "elevenlabs"})
    assert sum(p[1] == "api.elevenlabs.io" for p in rede["pedidos"]) == 1


def test_creditos_esgotados_cai_para_voz_local(rede, monkeypatch):
    _configurar(monkeypatch)
    rede["eleven"] = 401
    r = client.post("/api/tts", json={"text": "Olá.", "engine": "elevenlabs"})
    assert r.status_code == 200 and r.content == WAV
    assert r.headers["x-voz-motor"] == "local"
    assert "esgotados" in httpx.QueryParams(f"a={r.headers['x-voz-aviso']}")["a"]


def test_tudo_fora_do_ar_responde_503(rede, monkeypatch):
    _configurar(monkeypatch)
    rede["eleven"], rede["local"] = 429, 503
    assert client.post("/api/tts", json={"text": "Olá.", "engine": "elevenlabs"}).status_code == 503


def test_sem_configuracao_usa_local_direto(rede):
    r = client.post("/api/tts", json={"text": "Olá.", "engine": "elevenlabs"})
    assert r.headers["x-voz-motor"] == "local"
    assert not any(p[1] == "api.elevenlabs.io" for p in rede["pedidos"])


def test_lista_vozes_proprias_primeiro_e_modelos_de_fala(rede):
    d = client.post("/api/elevenlabs/opcoes", json={"key": "xi-chave-de-teste-123456"}).json()
    assert [v["id"] for v in d["vozes"]] == ["minha1", "pronta1"]
    assert [m["id"] for m in d["modelos"]] == ["eleven_flash_v2_5"]


def test_salvar_valida_voz_e_grava_no_env(rede, monkeypatch):
    monkeypatch.setattr(cfg.settings, "elevenlabs_key", "")
    monkeypatch.setattr(cfg.settings, "elevenlabs_voice_id", "")
    ruim = client.post("/api/elevenlabs", json={"key": "xi-chave-de-teste-123456", "voice_id": "naoexiste"})
    assert ruim.status_code == 400 and "Voice ID" in ruim.json()["detail"]

    ok = client.post("/api/elevenlabs", json={"key": "xi-chave-de-teste-123456", "voice_id": "minha1",
                                              "model": "eleven_multilingual_v2"})
    assert ok.status_code == 200 and ok.json()["elevenlabs"]["configurado"]
    env = (cfg.ENV_FILE).read_text(encoding="utf-8")
    assert "ELEVENLABS_VOICE_ID=minha1" in env and "ELEVENLABS_MODEL=eleven_multilingual_v2" in env
