"""Converte uma gravação qualquer (mp3, ogg, wav...) no áudio de referência da voz clonada.

Uso (com o Python do ambiente de voz):
    python voz/preparar_voz.py "C:/caminho/audio.mp3"

Gera data/voz/referencia.wav: mono, 22.050 Hz, sem silêncios nas pontas e com volume normalizado.
O XTTS precisa de 6 a 30 segundos de fala limpa, sem música de fundo.
"""
from __future__ import annotations

import sys
from pathlib import Path

import librosa
import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parents[1]
DESTINO = ROOT / "data" / "voz" / "referencia.wav"
SR = 22050


def preparar(origem: Path) -> Path:
    audio, _ = librosa.load(str(origem), sr=SR, mono=True)
    # Remove silêncio no começo/fim e junta trechos falados (pausas longas viram 0,3 s)
    trechos = librosa.effects.split(audio, top_db=35)
    pausa = np.zeros(int(0.3 * SR), dtype=audio.dtype)
    partes = []
    for ini, fim in trechos:
        partes += [audio[ini:fim], pausa]
    audio = np.concatenate(partes[:-1]) if partes else audio
    audio = audio[: 30 * SR]                               # o XTTS usa no máximo ~30 s
    audio = audio / (np.max(np.abs(audio)) + 1e-9) * 0.95  # normaliza o volume
    DESTINO.parent.mkdir(parents=True, exist_ok=True)
    sf.write(DESTINO, audio, SR, subtype="PCM_16")
    return DESTINO


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    destino = preparar(Path(sys.argv[1]))
    dur = sf.info(destino).duration
    print(f"Referência salva em {destino} ({dur:.1f} s de fala)")
    if dur < 6:
        print("Aviso: menos de 6 s de fala; a clonagem pode ficar imprecisa. Grave um áudio mais longo.")
