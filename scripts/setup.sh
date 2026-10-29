#!/usr/bin/env bash
# Prepara o ambiente do JARVIS no Linux: venv, dependências, .env e diretórios com permissões.
# Uso: ./scripts/setup.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

info() { printf '\033[36m[JARVIS]\033[0m %s\n' "$*"; }
fail() { printf '\033[31m[ERRO]\033[0m %s\n' "$*" >&2; exit 1; }

# 1. Verifica o interpretador Python (>= 3.10)
command -v python3 >/dev/null 2>&1 || fail "python3 não encontrado. Instale com: sudo apt install python3 python3-venv"
PYV="$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' || fail "Python $PYV encontrado; é necessário 3.10+."
info "Python $PYV encontrado em $(command -v python3)"

# 2. Ambiente virtual isolado
if [ ! -d .venv ]; then
  info "Criando ambiente virtual em .venv/"
  python3 -m venv .venv || fail "Falha ao criar venv. Instale: sudo apt install python3-venv"
fi
# shellcheck disable=SC1091
source .venv/bin/activate
info "Instalando dependências (requirements.txt)"
pip install --quiet --upgrade pip
pip install --quiet -r requirements.txt

# 3. Arquivo de configuração (variáveis de ambiente)
if [ ! -f .env ]; then
  cp .env.example .env
  info ".env criado a partir de .env.example — edite-o para adicionar suas chaves de API."
fi
chmod 600 .env   # só o dono pode ler: o arquivo contém segredos

# 4. Diretórios de trabalho com permissões restritas
mkdir -p logs reports data run
chmod 750 logs reports run    # dono: rwx | grupo: r-x | outros: ---
chmod 700 data                # notas e token do Spotify: só o dono
chmod +x scripts/*.sh

# 5. Ferramentas opcionais do sistema
for tool in playerctl pactl nvidia-smi; do
  if command -v "$tool" >/dev/null 2>&1; then
    info "Opcional encontrado: $tool"
  else
    info "Opcional ausente: $tool (recursos de mídia/GPU ficam limitados)"
  fi
done

info "Pronto! Inicie com: ./scripts/run.sh start"
