#!/usr/bin/env bash
# Verifica os recursos do sistema usando apenas comandos do Linux (/proc, free, df, ps)
# e grava um relatório em reports/. Emite alertas quando um limite é ultrapassado.
#
# Uso:  ./scripts/monitor.sh [--limite-cpu 85] [--limite-mem 90] [--limite-disco 90]
# Agendar a cada 30 min (crontab -e):
#   */30 * * * * /caminho/para/jarvis/scripts/monitor.sh >> /caminho/para/jarvis/logs/cron.log 2>&1
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPORT_DIR="$ROOT/reports"
LOG="$ROOT/logs/alertas.log"
mkdir -p "$REPORT_DIR" "$ROOT/logs"

LIM_CPU=85; LIM_MEM=90; LIM_DISK=90
while [ $# -gt 0 ]; do
  # Cada --limite-* precisa de um número inteiro logo depois (ex.: --limite-cpu 90)
  if [[ "$1" == --limite-* ]] && ! [[ "${2:-}" =~ ^[0-9]+$ ]]; then
    echo "Valor inválido para $1: use um número inteiro (ex.: $1 90)" >&2
    exit 2
  fi
  case "$1" in
    --limite-cpu)   LIM_CPU="$2"; shift 2 ;;
    --limite-mem)   LIM_MEM="$2"; shift 2 ;;
    --limite-disco) LIM_DISK="$2"; shift 2 ;;
    *) echo "Opção desconhecida: $1"; exit 2 ;;
  esac
done

STAMP="$(date +%Y%m%d_%H%M%S)"
OUT="$REPORT_DIR/monitor_$STAMP.txt"
ALERTS=0

alert() {
  ALERTS=$((ALERTS + 1))
  echo "  [ALERTA] $*"
  echo "$(date '+%F %T') ALERTA $*" >>"$LOG"
}

# Uso de CPU: duas leituras de /proc/stat com 1s de intervalo
cpu_usage() {
  read -r _ u1 n1 s1 i1 w1 q1 sq1 _ < /proc/stat
  sleep 1
  read -r _ u2 n2 s2 i2 w2 q2 sq2 _ < /proc/stat
  local idle=$(( (i2 + w2) - (i1 + w1) ))
  local total=$(( (u2+n2+s2+i2+w2+q2+sq2) - (u1+n1+s1+i1+w1+q1+sq1) ))
  echo $(( 100 * (total - idle) / total ))
}

{
  echo "============================================================"
  echo "  J.A.R.V.I.S. — MONITOR DE RECURSOS (shell script)"
  echo "  $(date '+%d/%m/%Y %H:%M:%S')  ·  host: $(hostname)  ·  usuário: $(whoami)"
  echo "============================================================"
  echo
  echo "[SISTEMA]"
  echo "  Kernel   : $(uname -sr)"
  echo "  Uptime   : $(uptime -p 2>/dev/null || cat /proc/uptime)"
  echo "  Load avg : $(cut -d' ' -f1-3 /proc/loadavg)"
  echo "  Processos: $(ps -e --no-headers | wc -l)"
  echo

  CPU="$(cpu_usage)"
  echo "[CPU]"
  echo "  Modelo   : $(grep -m1 'model name' /proc/cpuinfo | cut -d: -f2 | sed 's/^ //')"
  echo "  Núcleos  : $(nproc)"
  echo "  Uso      : ${CPU}%"
  [ "$CPU" -ge "$LIM_CPU" ] && alert "CPU em ${CPU}% (limite ${LIM_CPU}%)"
  echo

  echo "[MEMÓRIA]"
  free -h | sed 's/^/  /'
  # Lê do /proc/meminfo (de onde o free tira os números): a saída do free muda com o idioma
  # ("Mem.:" em pt_BR), mas os nomes do /proc não. Uso real = (total - disponível) / total.
  MEM="$(awk '/^MemTotal:/ {t=$2} /^MemAvailable:/ {a=$2} END {printf "%d", (t-a)*100/t}' /proc/meminfo)"
  echo "  Uso real : ${MEM}%"
  [ "$MEM" -ge "$LIM_MEM" ] && alert "Memória em ${MEM}% (limite ${LIM_MEM}%)"
  echo

  echo "[DISCOS]"
  df -h -x tmpfs -x devtmpfs -x squashfs -x overlay 2>/dev/null | sed 's/^/  /'
  while read -r pct mnt; do
    p="${pct%\%}"
    [ "$p" -ge "$LIM_DISK" ] && alert "Disco $mnt em ${p}% (limite ${LIM_DISK}%)"
  done < <(df --output=pcent,target -x tmpfs -x devtmpfs -x squashfs -x overlay 2>/dev/null | tail -n +2)
  echo

  echo "[TEMPERATURA]"
  found=0
  for z in /sys/class/thermal/thermal_zone*; do
    [ -r "$z/temp" ] || continue
    t=$(( $(cat "$z/temp") / 1000 ))
    echo "  $(cat "$z/type" 2>/dev/null): ${t} °C"
    found=1
  done
  [ "$found" -eq 0 ] && echo "  Sensores não disponíveis (comum em VMs/WSL)."
  echo

  echo "[TOP 5 PROCESSOS — CPU]"
  ps -eo pid,user,%cpu,%mem,comm --sort=-%cpu | sed -n '1,6s/^/  /p'
  echo
  echo "[TOP 5 PROCESSOS — MEMÓRIA]"
  ps -eo pid,user,%cpu,%mem,rss,comm --sort=-%mem | sed -n '1,6s/^/  /p'
  echo
  echo "Alertas emitidos: $ALERTS"
} > "$OUT"
cat "$OUT"

chmod 640 "$OUT"
echo
echo "Relatório salvo em: $OUT"

# Mantém só os 30 relatórios mais recentes (rotação simples)
ls -1t "$REPORT_DIR"/monitor_*.txt 2>/dev/null | tail -n +31 | xargs -r rm -f

# Código de saída 1 se houve alerta (útil para automações e cron)
[ "$ALERTS" -eq 0 ]
