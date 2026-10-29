"""O cérebro Gemini deve contornar sobrecarga (503) e modelos aposentados (404) sem chamar a rede real."""
import json

import httpx
import pytest

from jarvis.brain.gemini import FALLBACK_MODELS
from jarvis.config import settings
from jarvis.tools import ToolExecutor


def _brain(monkeypatch, handler):
    from jarvis.brain.gemini import GeminiBrain

    monkeypatch.setattr(settings, "gemini_key", "chave-de-teste-123456")
    monkeypatch.setattr(settings, "gemini_model", "gemini-modelo-escolhido")
    monkeypatch.setattr("jarvis.brain.gemini.time.sleep", lambda s: None)
    brain = GeminiBrain()
    brain.http = httpx.Client(transport=httpx.MockTransport(handler))
    return brain


def _ok(text):
    return httpx.Response(200, json={"candidates": [{"content": {"role": "model", "parts": [{"text": text}]}}]})


def test_modelo_sobrecarregado_usa_reserva(monkeypatch):
    chamados = []

    def handler(req: httpx.Request):
        model = req.url.path.split("/models/")[1].split(":")[0]
        chamados.append(model)
        if model == "gemini-modelo-escolhido":
            return httpx.Response(503, json={"error": {"message": "high demand"}})
        return _ok("Memória em 70 por cento, senhor.")

    eventos = []
    resposta = _brain(monkeypatch, handler).respond([{"role": "user", "text": "memória?"}], ToolExecutor(eventos.append))
    assert resposta.startswith("Memória em 70")
    assert chamados[:2] == ["gemini-modelo-escolhido", FALLBACK_MODELS[0]]
    assert any(e["type"] == "warning" for e in eventos)


def test_modelo_que_falhou_vai_para_o_fim_da_fila(monkeypatch):
    chamados = []

    def handler(req: httpx.Request):
        model = req.url.path.split("/models/")[1].split(":")[0]
        chamados.append(model)
        if model == "gemini-modelo-escolhido":
            return httpx.Response(503, json={"error": {"message": "high demand"}})
        return _ok("ok")

    brain = _brain(monkeypatch, handler)
    eventos = []
    brain.respond([{"role": "user", "text": "1"}], ToolExecutor(eventos.append))
    chamados.clear()
    brain.respond([{"role": "user", "text": "2"}], ToolExecutor(eventos.append))
    assert chamados == [FALLBACK_MODELS[0]]                  # não perde tempo com o que falhou
    assert sum(e["type"] == "warning" for e in eventos) == 1  # avisa uma vez só


def _model(req):
    return req.url.path.split("/models/")[1].split(":")[0]


def _tool_call(name, args):
    return httpx.Response(200, json={"candidates": [{"content": {"role": "model", "parts": [
        {"functionCall": {"name": name, "args": args}, "thoughtSignature": "assinatura"}]}}]})


def test_limite_no_meio_da_resposta_troca_de_modelo_sem_repetir_acao(monkeypatch):
    """O caso real: o modelo chama a ferramenta e, na 2ª rodada, toma 429 sem prazo para liberar."""
    corpos = []

    def handler(req: httpx.Request):
        body = json.loads(req.content)
        corpos.append((_model(req), body))
        if _model(req) == "gemini-modelo-escolhido":
            if len(body["contents"]) == 1:
                return _tool_call("data_hora", {})
            return httpx.Response(429, json={"error": {"message": "You exceeded your current quota"}})
        return _ok("São dez horas, senhor.")

    executadas = []
    ex = ToolExecutor(lambda e: executadas.append(e["tool"]) if e["type"] == "action" and e["status"] == "ok" else None)
    resposta = _brain(monkeypatch, handler).respond([{"role": "user", "text": "que horas são?"}], ex)

    assert resposta == "São dez horas, senhor."
    assert executadas == ["data_hora"]                        # a ação rodou uma vez só
    modelo, body = corpos[-1]
    assert modelo == FALLBACK_MODELS[0]
    texto = json.dumps(body["contents"], ensure_ascii=False)
    assert "NÃO execute de novo" in texto and "data_hora" in texto
    assert "assinatura" not in texto                          # assinatura do outro modelo não vai junto


def test_limite_por_minuto_espera_e_repete_o_mesmo_modelo(monkeypatch):
    tentativas = []

    def handler(req: httpx.Request):
        tentativas.append(_model(req))
        if len(tentativas) == 1:
            return httpx.Response(429, json={"error": {"message": "quota", "details": [
                {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "3s"}]}})
        return _ok("ok")

    esperas = []
    brain = _brain(monkeypatch, handler)
    monkeypatch.setattr("jarvis.brain.gemini.time.sleep", esperas.append)
    assert brain.respond([{"role": "user", "text": "oi"}], ToolExecutor()) == "ok"
    assert tentativas == ["gemini-modelo-escolhido", "gemini-modelo-escolhido"]
    assert esperas and esperas[0] >= 3


def test_cota_diaria_esgotada_pula_o_modelo_ate_liberar(monkeypatch):
    tentativas = []

    def handler(req: httpx.Request):
        tentativas.append(_model(req))
        if _model(req) == "gemini-modelo-escolhido":
            return httpx.Response(429, json={"error": {"message": "quota", "details": [
                {"@type": "type.googleapis.com/google.rpc.QuotaFailure",
                 "violations": [{"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"}]},
                {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "57319s"}]}})
        return _ok("ok")

    brain = _brain(monkeypatch, handler)
    brain.respond([{"role": "user", "text": "1"}], ToolExecutor())
    tentativas.clear()
    brain.respond([{"role": "user", "text": "2"}], ToolExecutor())
    assert "gemini-modelo-escolhido" not in tentativas      # nem tenta: cota do dia acabou
    pausa = brain.cooldown["gemini-modelo-escolhido"] - __import__("time").monotonic()
    assert pausa > 50_000


def test_assistente_explica_quando_a_ia_cai(monkeypatch):
    from jarvis.assistant import Assistant
    from jarvis.brain.gemini import TemporaryBrainError

    class Falha:
        def respond(self, history, executor):
            raise TemporaryBrainError("todos os modelos Gemini falharam (x: HTTP 429)")

    jarvis = Assistant(brain="local")
    jarvis.brain, jarvis.brain_name = Falha(), "gemini"
    resposta = jarvis.chat("o que você acha dessa ideia?")
    assert "limite de uso" in resposta and "modo offline" not in resposta
    assert jarvis.chat("que horas são?").startswith("São")  # comando direto segue funcionando


def test_modelo_aposentado_404_usa_reserva(monkeypatch):
    def handler(req: httpx.Request):
        if "modelo-escolhido" in req.url.path:
            return httpx.Response(404, json={"error": {"message": "is no longer available to new users"}})
        return _ok("Olá, senhor.")

    assert _brain(monkeypatch, handler).respond([{"role": "user", "text": "oi"}], ToolExecutor()) == "Olá, senhor."


def test_pensamento_minimo_e_enviado(monkeypatch):
    corpos = []

    def handler(req: httpx.Request):
        corpos.append(json.loads(req.content))
        return _ok("ok")

    _brain(monkeypatch, handler).respond([{"role": "user", "text": "oi"}], ToolExecutor())
    assert corpos[0]["generationConfig"]["thinkingConfig"]["thinkingLevel"] == "minimal"


def test_erro_de_chave_nao_tenta_reservas(monkeypatch):
    from jarvis.brain.base import BrainError

    chamados = []

    def handler(req: httpx.Request):
        chamados.append(req.url.path)
        return httpx.Response(403, json={"error": {"message": "API key not valid"}})

    with pytest.raises(BrainError):
        _brain(monkeypatch, handler).respond([{"role": "user", "text": "oi"}], ToolExecutor())
    assert len(chamados) == 1
