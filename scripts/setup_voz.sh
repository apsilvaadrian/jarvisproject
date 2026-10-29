#!/usr/bin/env bash
# Instala a voz clonada (XTTS-v2, local e gratuita) e prepara o áudio de referência.
# Uso: ./scripts/setup_voz.sh /caminho/minha_voz.mp3
# O modelo usa a Coqui Public Model License (somente uso não comercial).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
[ $# -eq 1 ] || { echo "Uso: $0 /caminho/audio_da_sua_voz.mp3"; exit 2; }
[ -f "$1" ] || { echo "Arquivo não encontrado: $1"; exit 1; }
command -v uv >/dev/null 2>&1 || { echo "Instale o uv: curl -LsSf https://astral.sh/uv/install.sh | sh"; exit 1; }

VENV="$HOME/.jarvis/venv-voz"
if [ ! -x "$VENV/bin/python" ]; then
  echo "[voz] Criando ambiente Python 3.13 em $VENV"
  uv venv "$VENV" --python 3.13
fi
echo "[voz] Instalando coqui-tts e PyTorch (CPU)..."
uv pip install --python "$VENV/bin/python" coqui-tts "torch==2.8.*" "torchaudio==2.8.*" \
  --index-strategy unsafe-best-match --extra-index-url https://download.pytorch.org/whl/cpu

MODEL_DIR="$HOME/.jarvis/xtts-v2"
mkdir -p "$MODEL_DIR"
for f in config.json vocab.json speakers_xtts.pth LICENSE.txt model.pth; do
  echo "[voz] Baixando $f"
  curl -fL --retry 3 -C - -o "$MODEL_DIR/$f" "https://huggingface.co/tts-hub/XTTS-v2/resolve/main/$f"
done

echo "[voz] Preparando o áudio de referência"
"$VENV/bin/python" -W ignore voz/preparar_voz.py "$1"
chmod 700 data/voz        # a sua voz é um dado pessoal: só o dono acessa
echo "[voz] Pronto. O run.sh inicia o servidor de voz automaticamente."
