#!/bin/bash
# Одноразове встановлення Sparrow на Mac. Потрібен інтернет (GitHub, crates.io).
set -e
if ! xcode-select -p >/dev/null 2>&1; then
  echo "Спершу встановіть Command Line Tools: xcode-select --install, потім запустіть скрипт знову."; exit 1
fi
if ! command -v cargo >/dev/null 2>&1 && [ ! -f "$HOME/.cargo/env" ]; then
  curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
fi
source "$HOME/.cargo/env"
rustup update stable
cargo --version   # потрібно Rust 1.90 або новіший
if [ -d "$HOME/sparrow/.git" ]; then git -C "$HOME/sparrow" pull; else git clone https://github.com/JeroenGar/sparrow "$HOME/sparrow"; fi
cd "$HOME/sparrow" && cargo build --release
echo "Готово: Sparrow зібрано в $HOME/sparrow"
