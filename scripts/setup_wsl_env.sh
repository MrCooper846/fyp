#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="$ROOT_DIR/.venv-wsl"
REQ_FILE="$ROOT_DIR/requirements.txt"

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 is required in WSL but was not found." >&2
  exit 1
fi

if [[ ! -f "$REQ_FILE" ]]; then
  echo "requirements.txt not found at $REQ_FILE" >&2
  exit 1
fi

bootstrap_with_stdlib_venv() {
  python3 -m venv "$VENV_DIR"
  # shellcheck disable=SC1090
  source "$VENV_DIR/bin/activate"
  python -m pip install --upgrade pip
  python -m pip install -r "$REQ_FILE"
}

bootstrap_with_uv() {
  if ! command -v uv >/dev/null 2>&1; then
    echo "Installing uv into your WSL user account..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
  fi

  uv venv "$VENV_DIR"
  # shellcheck disable=SC1090
  source "$VENV_DIR/bin/activate"
  uv pip install -r "$REQ_FILE"
}

if python3 -m venv "$VENV_DIR" >/dev/null 2>&1; then
  rm -rf "$VENV_DIR"
  echo "Using stdlib venv bootstrap..."
  bootstrap_with_stdlib_venv
else
  echo "python3 -m venv is unavailable; falling back to uv bootstrap..."
  bootstrap_with_uv
fi

echo
echo "WSL environment ready."
echo "Activate it with:"
echo "  source \"$VENV_DIR/bin/activate\""
echo
echo "Optional Playwright browser install:"
echo "  python -m playwright install chromium"
