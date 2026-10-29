"""Síntese de voz no servidor: ElevenLabs (nuvem) com reserva na voz clonada local (XTTS).

Ordem de tentativa para o motor "elevenlabs": ElevenLabs -> XTTS local. Se ambos falharem a
interface usa a voz do navegador. Frases repetidas ficam em cache (economiza créditos).
"""
from __future__ import annotations

from collections import OrderedDict
from typing import Any

import httpx

from .config import settings
from .logger import log

ELEVEN = "https://api.elevenlabs.io/v1"
_transport: httpx.AsyncBaseTransport | None = None   # testes trocam por um transporte simulado
_cache: OrderedDict[tuple[str, str, str], bytes] = OrderedDict()
_sem_contexto: set[str] = set()     # modelos que recusaram previous_text/next_text


class VoiceError(Exception):
    """Falha de um motor de voz, com o motivo em português para mostrar ao usuário."""


def _client(timeout: float = 30) -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=timeout, transport=_transport)


def elevenlabs_configured() -> bool:
    return bool(settings.elevenlabs_key and settings.elevenlabs_voice_id)


def _eleven_reason(r: httpx.Response) -> str:
    try:
        detail = r.json().get("detail", {})
    except ValueError:
        detail = {}
    status = detail.get("status", "") if isinstance(detail, dict) else ""
    if status == "quota_exceeded":
        return "créditos do mês do ElevenLabs esgotados"
    if status == "missing_permissions":
        return "a chave do ElevenLabs não tem permissão de Text to Speech/Voices"
    if status == "voice_not_found" or r.status_code == 404:
        return "voz não encontrada no ElevenLabs (confira o Voice ID)"
    if r.status_code == 401:
        return "chave do ElevenLabs recusada"
    if r.status_code == 429:
        return "limite momentâneo do ElevenLabs"
    if r.status_code == 402 or status in ("payment_required", "paid_plan_required"):
        return "o seu plano do ElevenLabs não permite usar essa voz/modelo pela API"
    # Qualquer outro erro: mostra o motivo que o próprio ElevenLabs enviou
    motivo = detail.get("message", "") if isinstance(detail, dict) else str(detail or "")
    return f"ElevenLabs recusou (HTTP {r.status_code}): {motivo[:160]}" if motivo else f"ElevenLabs respondeu HTTP {r.status_code}"


async def elevenlabs_tts(text: str, previous: str = "", following: str = "") -> bytes:
    if not elevenlabs_configured():
        raise VoiceError("ElevenLabs não configurado")
    key = (settings.elevenlabs_voice_id, settings.elevenlabs_model, text)
    if key in _cache:
        _cache.move_to_end(key)
        return _cache[key]
    body: dict[str, Any] = {
        "text": text,
        "model_id": settings.elevenlabs_model,
        "voice_settings": {"stability": 0.5, "similarity_boost": 0.8},
    }
    # Contexto das frases vizinhas: a entonação fica contínua mesmo gerando frase a frase
    # (alguns modelos, como o eleven_v3, recusam isso com HTTP 400; aí repetimos sem contexto)
    usa_contexto = settings.elevenlabs_model not in _sem_contexto
    if previous and usa_contexto:
        body["previous_text"] = previous[-500:]
    if following and usa_contexto:
        body["next_text"] = following[:500]
    try:
        async with _client() as c:
            url = f"{ELEVEN}/text-to-speech/{settings.elevenlabs_voice_id}"
            params, headers = {"output_format": "mp3_44100_128"}, {"xi-api-key": settings.elevenlabs_key}
            r = await c.post(url, params=params, headers=headers, json=body)
            if r.status_code == 400 and ("previous_text" in r.text or "next_text" in r.text):
                _sem_contexto.add(settings.elevenlabs_model)
                body.pop("previous_text", None)
                body.pop("next_text", None)
                r = await c.post(url, params=params, headers=headers, json=body)
    except httpx.HTTPError as exc:
        raise VoiceError(f"sem conexão com o ElevenLabs ({exc.__class__.__name__})")
    if r.status_code != 200:
        motivo = _eleven_reason(r)
        log.warning("ElevenLabs falhou: %s | %s", motivo, r.text[:300])
        raise VoiceError(motivo)
    _cache[key] = r.content
    if len(_cache) > 128:
        _cache.popitem(last=False)
    return r.content


async def local_tts(text: str) -> bytes:
    try:
        async with _client(timeout=90) as c:
            r = await c.post(settings.voz_url + "/tts", json={"text": text})
    except httpx.HTTPError:
        raise VoiceError("servidor de voz local desligado")
    if r.status_code != 200:
        raise VoiceError("voz local indisponível")
    return r.content


async def synthesize(text: str, engine: str, previous: str = "", following: str = "") -> tuple[bytes, str, str, str]:
    """Retorna (áudio, tipo MIME, motor usado, aviso). Levanta VoiceError se nenhum motor funcionar."""
    aviso = ""
    if engine == "elevenlabs":
        try:
            return await elevenlabs_tts(text, previous, following), "audio/mpeg", "elevenlabs", ""
        except VoiceError as exc:
            aviso = str(exc)
    try:
        return await local_tts(text), "audio/wav", "local", aviso
    except VoiceError as exc:
        raise VoiceError(f"{aviso}; {exc}" if aviso else str(exc))


async def list_voices(key: str) -> list[dict[str, str]]:
    """Vozes da conta: as suas (clonadas) primeiro, depois as prontas do ElevenLabs."""
    try:
        async with _client(timeout=15) as c:
            r = await c.get(f"{ELEVEN}/voices", headers={"xi-api-key": key})
    except httpx.HTTPError as exc:
        raise VoiceError(f"sem conexão com o ElevenLabs ({exc.__class__.__name__})")
    if r.status_code != 200:
        raise VoiceError(_eleven_reason(r))
    voices = [{"id": v["voice_id"], "nome": v.get("name", v["voice_id"]), "categoria": v.get("category", "")}
              for v in r.json().get("voices", [])]
    voices.sort(key=lambda v: (v["categoria"] not in ("cloned", "professional", "generated"), v["nome"].lower()))
    return voices


async def list_models(key: str) -> list[dict[str, Any]]:
    """Modelos de texto-para-fala; os que falam português vêm marcados."""
    try:
        async with _client(timeout=15) as c:
            r = await c.get(f"{ELEVEN}/models", headers={"xi-api-key": key})
    except httpx.HTTPError as exc:
        raise VoiceError(f"sem conexão com o ElevenLabs ({exc.__class__.__name__})")
    if r.status_code != 200:
        raise VoiceError(_eleven_reason(r))
    out = []
    for m in r.json():
        if not m.get("can_do_text_to_speech", True):
            continue
        langs = {(lang.get("language_id") or "").lower() for lang in m.get("languages", [])}
        out.append({"id": m["model_id"], "nome": m.get("name", m["model_id"]), "pt": bool(langs & {"pt", "por", "pt-br"})})
    out.sort(key=lambda m: not m["pt"])
    return out


async def test_tts(key: str, voice_id: str, model: str) -> None:
    """Gera "Ok." com a combinação escolhida (3 caracteres de crédito) para validar antes de salvar."""
    try:
        async with _client() as c:
            r = await c.post(f"{ELEVEN}/text-to-speech/{voice_id}", params={"output_format": "mp3_44100_128"},
                             headers={"xi-api-key": key}, json={"text": "Ok.", "model_id": model})
    except httpx.HTTPError as exc:
        raise VoiceError(f"sem conexão com o ElevenLabs ({exc.__class__.__name__})")
    if r.status_code != 200:
        raise VoiceError(_eleven_reason(r))


async def check_voice(key: str, voice_id: str) -> None:
    """Confere se a chave enxerga a voz (levanta VoiceError se não)."""
    try:
        async with _client(timeout=15) as c:
            r = await c.get(f"{ELEVEN}/voices/{voice_id}", headers={"xi-api-key": key})
    except httpx.HTTPError as exc:
        raise VoiceError(f"sem conexão com o ElevenLabs ({exc.__class__.__name__})")
    if r.status_code != 200:
        raise VoiceError(_eleven_reason(r))
