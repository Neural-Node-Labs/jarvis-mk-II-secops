#!/usr/bin/env bash
set -e

# Resolve paths
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BACKEND_DIR="$SCRIPT_DIR/../backend"
VENV_DIR="$BACKEND_DIR/.venv"
REQ_FILE="$BACKEND_DIR/requirements.txt"

# Ensure backend directory exists
if [ ! -d "$BACKEND_DIR" ]; then
    echo "Error: Backend directory not found at $BACKEND_DIR"
    exit 1
fi

# Create virtual environment if missing
if [ ! -d "$VENV_DIR" ]; then
    echo "Creating Python virtual environment..."
    python3 -m venv "$VENV_DIR"
fi

# Activate
source "$VENV_DIR/bin/activate"

# Install dependencies (quiet if already up-to-date)
if [ -f "$REQ_FILE" ]; then
    echo "Installing/updating dependencies..."
    pip install -q -r "$REQ_FILE"
else
    echo "Warning: requirements.txt not found; skipping dependency install."
fi

# Run console, forwarding all arguments
exec python "$BACKEND_DIR/console.py" "$@"
