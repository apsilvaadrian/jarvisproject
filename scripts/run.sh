#!/usr/bin/env bash
# Gerencia o processo do JARVIS em segundo plano usando um arquivo de PID.
# Uso: ./scripts/run.sh {start|stop|restart|status|logs|cli}
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
PIDFILE="run/jarvis.pid"
OUTFILE="logs/servidor.out"
mkdir -p run logs

# Carrega as variáveis do .env para o ambiente deste shell (e dos processos filhos)
if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi
PORT="${JARVIS_PORT:-8000}"
HOST="${JARVIS_HOST:-127.0.0.1}"

[ -d .venv ] && source .venv/bin/activate
export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"

is_running() { [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; }

# Servidor de voz clonada (opcional, criado por scripts/setup_voz.sh)
VOZ_PY="${JARVIS_VOZ_PYTHON:-$HOME/.jarvis/venv-voz/bin/python}"
VOZ_PIDFILE="run/voz.pid"

start_voz() {
  [ -x "$VOZ_PY" ] && [ -f data/voz/referencia.wav ] || return 0
  [ -f "$VOZ_PIDFILE" ] && kill -0 "$(cat "$VOZ_PIDFILE")" 2>/dev/null && return 0
  nohup "$VOZ_PY" -u voz/servidor_voz.py >logs/voz.out 2>&1 &
  echo $! >"$VOZ_PIDFILE"
  echo "Servidor de voz clonada iniciado (PID $(cat "$VOZ_PIDFILE")); o modelo leva ~1 min para carregar."
}

stop_voz() {
  [ -f "$VOZ_PIDFILE" ] || return 0
  kill -TERM "$(cat "$VOZ_PIDFILE")" 2>/dev/null || true
  rm -f "$VOZ_PIDFILE"
}

start() {
  start_voz
  if is_running; then echo "JARVIS já está em execução (PID $(cat "$PIDFILE"))."; return 0; fi
  echo "Iniciando JARVIS em http://$HOST:$PORT ..."
  # nohup: o processo sobrevive ao fechamento do terminal; saída redirecionada para arquivo
  nohup python3 -m jarvis --no-browser >"$OUTFILE" 2>&1 &
  echo $! >"$PIDFILE"
  # Aguarda o servidor responder (até ~10s)
  for _ in $(seq 1 20); do
    if curl -fs "http://$HOST:$PORT/health" >/dev/null 2>&1; then
      echo "JARVIS online (PID $(cat "$PIDFILE"))."
      command -v xdg-open >/dev/null 2>&1 && xdg-open "http://$HOST:$PORT" >/dev/null 2>&1 &
      return 0
    fi
    sleep 0.5
  done
  echo "O servidor não respondeu. Veja $OUTFILE"; return 1
}

stop() {
  stop_voz
  if ! is_running; then echo "JARVIS não está em execução."; rm -f "$PIDFILE"; return 0; fi
  PID="$(cat "$PIDFILE")"
  echo "Enviando SIGTERM para o PID $PID ..."
  kill -TERM "$PID"
  for _ in $(seq 1 10); do
    kill -0 "$PID" 2>/dev/null || { rm -f "$PIDFILE"; echo "JARVIS encerrado."; return 0; }
    sleep 0.5
  done
  echo "Não respondeu ao SIGTERM; enviando SIGKILL."
  kill -KILL "$PID" 2>/dev/null || true
  rm -f "$PIDFILE"
}

status() {
  if is_running; then
    PID="$(cat "$PIDFILE")"
    echo "JARVIS em execução — PID $PID"
    ps -o pid,ppid,stat,etime,%cpu,%mem,rss,cmd -p "$PID"
  else
    echo "JARVIS parado."
  fi
}

case "${1:-start}" in
  start)   start ;;
  stop)    stop ;;
  restart) stop; start ;;
  status)  status ;;
  logs)    tail -n 50 -f logs/jarvis.log ;;
  cli)     python3 -m jarvis cli ;;
  *) echo "Uso: $0 {start|stop|restart|status|logs|cli}"; exit 2 ;;
esac
