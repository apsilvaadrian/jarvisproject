"""Servidor de voz clonada do JARVIS (XTTS-v2, roda 100% local na CPU).

Processo separado do JARVIS porque o motor de voz exige outra versão do Python e
leva alguns segundos para carregar o modelo. O JARVIS conversa com ele por HTTP:

    GET  /health          -> {"pronto": true/false, "erro": ...}
    POST /tts {"text"}    -> áudio WAV (24 kHz) falado com a voz de referência

Uso: python voz/servidor_voz.py   (com o Python do ambiente de voz)
Variáveis: JARVIS_VOZ_PORT (8001), JARVIS_VOZ_REF (data/voz/referencia.wav),
           JARVIS_VOZ_MODELO (~/.jarvis/xtts-v2), JARVIS_VOZ_THREADS
"""
from __future__ import annotations

import io
import json
import os
import threading
import time
import warnings
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

warnings.filterwarnings("ignore")

ROOT = Path(__file__).resolve().parents[1]
PORT = int(os.environ.get("JARVIS_VOZ_PORT", "8001"))
REF = Path(os.environ.get("JARVIS_VOZ_REF", ROOT / "data" / "voz" / "referencia.wav"))
MODEL_DIR = Path(os.environ.get("JARVIS_VOZ_MODELO", Path.home() / ".jarvis" / "xtts-v2"))
# int8 = ~1,7x mais rápido na CPU; JARVIS_VOZ_INT8=0 volta ao modelo original (mais fiel, mais lento)
INT8 = os.environ.get("JARVIS_VOZ_INT8", "1") != "0"
LANG = "pt"
SR = 24000
# O usuário aceitou a Coqui Public Model License (uso não comercial) na instalação
os.environ.setdefault("COQUI_TOS_AGREED", "1")
# O transporte "Xet" do Hugging Face pode travar no Windows; baixa por HTTP comum
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")

state = {"pronto": False, "erro": None, "carregado_em": None}
model = None
latents = None
lock = threading.Lock()               # o modelo atende uma frase por vez
cache: OrderedDict[str, bytes] = OrderedDict()


def quantize_gpt(xtts) -> None:
    """Quantização dinâmica int8 da parte autorregressiva (GPT), onde a CPU passa a maior parte do tempo.

    Medido num Ryzen 5 5600G: ~1,7x mais rápido, com pequena perda de qualidade.
    O GPT-2 do transformers usa Conv1D (peso transposto); o quantizador do PyTorch só entende
    nn.Linear, então as camadas são convertidas antes.
    """
    import torch
    from torch import nn

    def conv1d_to_linear(module: nn.Module) -> None:
        for name, child in module.named_children():
            if type(child).__name__ == "Conv1D":
                lin = nn.Linear(child.weight.shape[0], child.weight.shape[1])
                lin.weight.data = child.weight.data.t().contiguous()
                lin.bias.data = child.bias.data
                setattr(module, name, lin)
            else:
                conv1d_to_linear(child)

    conv1d_to_linear(xtts.gpt)
    xtts.gpt = torch.ao.quantization.quantize_dynamic(xtts.gpt, {nn.Linear}, dtype=torch.qint8)


def load() -> None:
    global model, latents
    t0 = time.time()
    try:
        import torch
        from TTS.api import TTS

        # Núcleos físicos (metade dos lógicos) rendem mais que usar todos os threads do SMT
        torch.set_num_threads(int(os.environ.get("JARVIS_VOZ_THREADS", max(1, (os.cpu_count() or 4) // 2))))
        if not REF.is_file():
            raise FileNotFoundError(f"Áudio de referência não encontrado: {REF}. Rode voz/preparar_voz.py")
        if (MODEL_DIR / "model.pth").is_file():
            # Modelo baixado pelo setup_voz (pasta local): carrega direto, sem depender do Hugging Face
            from TTS.tts.configs.xtts_config import XttsConfig
            from TTS.tts.models.xtts import Xtts

            config = XttsConfig()
            config.load_json(str(MODEL_DIR / "config.json"))
            model = Xtts.init_from_config(config)
            model.load_checkpoint(config, checkpoint_dir=str(MODEL_DIR), use_deepspeed=False)
            model.eval()
        else:
            tts = TTS("tts_models/multilingual/multi-dataset/xtts_v2", progress_bar=True).to("cpu")
            model = tts.synthesizer.tts_model
        if INT8:
            quantize_gpt(model)
        # As características da voz são extraídas uma única vez e reaproveitadas em toda frase
        gpt_cond, speaker = model.get_conditioning_latents(audio_path=[str(REF)], gpt_cond_len=30, max_ref_length=30)
        latents = (gpt_cond, speaker)
        state.update(pronto=True, carregado_em=round(time.time() - t0, 1), int8=INT8)
        print(f"[voz] Modelo pronto em {state['carregado_em']} s (int8={INT8}) — referência {REF.name}", flush=True)
    except Exception as exc:  # mostra o erro no /health em vez de derrubar o processo
        state["erro"] = f"{exc.__class__.__name__}: {exc}"
        print(f"[voz] ERRO ao carregar: {state['erro']}", flush=True)


def synthesize(text: str) -> bytes:
    import numpy as np
    import soundfile as sf

    import torch

    # Em português o XTTS às vezes pronuncia o ponto final ("...senhor, ponto"); removemos antes
    key = text.strip().rstrip(".").strip()
    if key in cache:
        cache.move_to_end(key)
        return cache[key]
    with lock, torch.inference_mode():
        out = model.inference(key, LANG, latents[0], latents[1], temperature=0.7,
                              length_penalty=1.0, repetition_penalty=5.0, speed=1.05,
                              enable_text_splitting=True)
    wav = np.asarray(out["wav"], dtype=np.float32)
    buf = io.BytesIO()
    sf.write(buf, wav, SR, format="WAV", subtype="PCM_16")
    data = buf.getvalue()
    cache[key] = data
    if len(cache) > 64:
        cache.popitem(last=False)
    return data


class Handler(BaseHTTPRequestHandler):
    def _json(self, code: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path == "/health":
            self._json(200, {**state, "referencia": str(REF)})
        else:
            self._json(404, {"erro": "não encontrado"})

    def do_POST(self) -> None:
        if self.path != "/tts":
            return self._json(404, {"erro": "não encontrado"})
        if not state["pronto"]:
            return self._json(503, {"erro": state["erro"] or "modelo carregando"})
        try:
            size = int(self.headers.get("Content-Length", 0))
            text = str(json.loads(self.rfile.read(size) or b"{}").get("text", "")).strip()[:600]
        except (ValueError, json.JSONDecodeError):
            return self._json(400, {"erro": "JSON inválido"})
        if not text:
            return self._json(400, {"erro": "texto vazio"})
        t0 = time.time()
        try:
            audio = synthesize(text)
        except Exception as exc:
            return self._json(500, {"erro": str(exc)})
        print(f"[voz] {len(text)} caracteres em {time.time() - t0:.1f} s", flush=True)
        self.send_response(200)
        self.send_header("Content-Type", "audio/wav")
        self.send_header("Content-Length", str(len(audio)))
        self.end_headers()
        self.wfile.write(audio)

    def log_message(self, *args) -> None:  # silencia o log padrão por requisição
        pass


if __name__ == "__main__":
    threading.Thread(target=load, daemon=True).start()
    print(f"[voz] Servidor de voz em http://127.0.0.1:{PORT} (carregando modelo...)", flush=True)
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
