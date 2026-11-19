#!/bin/bash
# Виконує запити нестингу з ~/iQ-AI/kd-dxf-pack/queue/*.json (запускає launchd).
# Запит: {"task": "zavdannia/<папка>", "profile": "default", "trial_seconds": 45}
B="$HOME/iQ-AI/kd-dxf-pack"; Q="$B/queue"
mkdir -p "$Q/running" "$Q/done"
LOCK="$Q/.lock"
if ! mkdir "$LOCK" 2>/dev/null; then
  # застарілий замок (старший за 3 год) прибрати
  if [ -n "$(find "$LOCK" -maxdepth 0 -mmin +180 2>/dev/null)" ]; then rmdir "$LOCK"; mkdir "$LOCK" || exit 0; else exit 0; fi
fi
trap 'rmdir "$LOCK" 2>/dev/null' EXIT
[ -f "$HOME/.cargo/env" ] && source "$HOME/.cargo/env"
for REQ in "$Q"/*.json; do
  [ -e "$REQ" ] || continue
  ID=$(basename "$REQ" .json)
  mv "$REQ" "$Q/running/$ID.json"
  echo "running $(date '+%F %T')" > "$Q/done/$ID.status"
  /usr/bin/python3 "$B/tools/nest_run.py" --queue-request "$Q/running/$ID.json" --base "$B" > "$Q/done/$ID.log" 2>&1
  RC=$?
  echo "$([ $RC -eq 0 ] && echo ok || echo error) $RC $(date '+%F %T')" > "$Q/done/$ID.status"
  mv "$Q/running/$ID.json" "$Q/done/$ID.json"
done
