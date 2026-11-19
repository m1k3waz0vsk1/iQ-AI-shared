#!/bin/bash
# Встановлює службу нестингу (launchd, лише для поточного користувача).
set -e
B="$HOME/iQ-AI/kd-dxf-pack"
[ -x "$HOME/sparrow/target/release/sparrow" ] || { echo "Немає Sparrow: спершу tools/build_sparrow.sh"; exit 1; }
mkdir -p "$B/queue/running" "$B/queue/done" "$HOME/Library/LaunchAgents"
chmod +x "$B/service/queue_runner.sh"
P="$HOME/Library/LaunchAgents/ua.iqcomposite.kd-nesting.plist"
sed "s#__HOME__#$HOME#g" "$B/service/kd-nesting.plist.template" > "$P"
launchctl bootout "gui/$(id -u)/ua.iqcomposite.kd-nesting" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$P"
echo "Службу встановлено: ua.iqcomposite.kd-nesting (черга $B/queue)"
