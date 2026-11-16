#!/usr/bin/env bash
# Faz backup de data/ e logs/ num arquivo .tar.gz com a data no nome e apaga os mais antigos (rotação).
#
# Uso:  ./scripts/backup.sh [--manter 7]
# Destino: backups/ ou a pasta da variável de ambiente JARVIS_BACKUP_DIR (ex.: um HD externo).
# Restaurar (na pasta do projeto): tar -xzf backups/jarvis_AAAA-MM-DD_HHMMSS.tar.gz
# Agendar todo dia às 2h (crontab -e):
#   0 2 * * * /caminho/para/jarvis/scripts/backup.sh >> /caminho/para/jarvis/logs/cron.log 2>&1
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
DEST="${JARVIS_BACKUP_DIR:-$ROOT/backups}"
LOG="$ROOT/logs/backup.log"
MANTER=7

while [ $# -gt 0 ]; do
  case "$1" in
    --manter)
      [[ "${2:-}" =~ ^[1-9][0-9]*$ ]] || {
        echo "Valor inválido para --manter: use um número inteiro maior que zero (ex.: --manter 7)" >&2; exit 2; }
      MANTER="$2"; shift 2 ;;
    *) echo "Opção desconhecida: $1" >&2; echo "Uso: $0 [--manter N]" >&2; exit 2 ;;
  esac
done

# umask 077: tudo o que este processo criar nasce só para o dono (pastas 700, arquivos 600).
# O backup tem as notas, o token do Spotify e os logs: ninguém mais pode ler.
umask 077
mkdir -p "$DEST" logs run

# Trava (flock): se o cron disparar enquanto um backup anterior ainda roda, o segundo desiste.
# O descritor 9 fica aberto até o script terminar; o kernel solta a trava sozinho quando o processo sai.
exec 9>run/backup.lock
flock -n 9 || { echo "Outro backup já está em andamento." >&2; exit 1; }

registrar() { echo "$(date '+%F %T') $*" | tee -a "$LOG"; }

PASTAS=()
for p in data logs; do [ -d "$p" ] && PASTAS+=("$p"); done
[ ${#PASTAS[@]} -gt 0 ] || { registrar "ERRO nada para copiar: data/ e logs/ não existem"; exit 1; }

NOME="jarvis_$(date +%Y-%m-%d_%H%M%S).tar.gz"
TMP="$DEST/.$NOME.parcial"
# Se o tar falhar ou o script for interrompido (Ctrl+C, kill), o arquivo pela metade é apagado
trap 'rm -f "$TMP"' EXIT

# O JARVIS pode estar gravando nos logs agora: o tar avisa e sai com código 1, o que não é erro.
# Código 2 ou mais é falha de verdade (disco cheio, sem permissão...).
status=0
tar -czf "$TMP" --warning=no-file-changed "${PASTAS[@]}" || status=$?
if [ "$status" -gt 1 ]; then
  registrar "ERRO o tar falhou (código $status)"
  exit 1
fi

# Renomear dentro da mesma pasta é atômico: o backup aparece inteiro ou não aparece
mv "$TMP" "$DEST/$NOME"
trap - EXIT
registrar "OK $DEST/$NOME ($(du -h "$DEST/$NOME" | cut -f1); pastas: ${PASTAS[*]})"

# Rotação: mantém só os $MANTER mais recentes. A data no nome (ano-mês-dia) faz a ordem
# alfabética ser a mesma que a cronológica, então basta ordenar ao contrário e apagar o resto.
printf '%s\n' "$DEST"/jarvis_*.tar.gz | sort -r | tail -n +$((MANTER + 1)) | while read -r velho; do
  rm -f -- "$velho"
  registrar "Rotação: apagado $velho"
done
