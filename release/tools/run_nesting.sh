#!/bin/bash
# Ручний запуск нестингу (без служби).
# Використання: tools/run_nesting.sh <папка завдання> [секунд_на_спробу] 
set -e
[ -z "$1" ] && { echo "Вкажіть папку завдання, напр. zavdannia/2026-10-22"; exit 1; }
TOOLS="$(cd "$(dirname "$0")" && pwd)"
[ -f "$HOME/.cargo/env" ] && source "$HOME/.cargo/env"
if [ -n "$2" ]; then python3 "$TOOLS/nest_run.py" --task "$1" --trial-seconds "$2"; else python3 "$TOOLS/nest_run.py" --task "$1"; fi
