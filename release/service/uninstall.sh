#!/bin/bash
launchctl bootout "gui/$(id -u)/ua.iqcomposite.kd-nesting" 2>/dev/null || true
rm -f "$HOME/Library/LaunchAgents/ua.iqcomposite.kd-nesting.plist"
echo "Службу видалено"
