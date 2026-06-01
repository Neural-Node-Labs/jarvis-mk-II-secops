#!/bin/bash
# ─── Agent Setup Script ───────────────────────────────────────────────────────
# Installs dependencies for both backend (Python) and frontend (React)

set -e

echo ""
echo "◈ AI AGENT SETUP"
echo "════════════════════════════════════════"

# ─── Backend ──────────────────────────────────────────────────────────────────
echo ""
echo "→ Installing Python backend dependencies..."
cd "$(dirname "$0")/backend"

python3 -m venv .venv 2>/dev/null || true
source .venv/bin/activate

pip install -r requirements.txt -q
echo "  ✓ Backend dependencies installed"

# ─── Frontend ─────────────────────────────────────────────────────────────────
echo ""
echo "→ Setting up React frontend..."
cd "../frontend"

if [ ! -f "package.json" ]; then
  npm create vite@latest . -- --template react --yes 2>/dev/null || \
  npx create-vite@latest . --template react --yes
fi

# Overwrite App.jsx with our version
cp src/App.jsx src/App.jsx.bak 2>/dev/null || true

npm install -q
echo "  ✓ Frontend dependencies installed"

echo ""
echo "════════════════════════════════════════"
echo "✓ Setup complete!"
echo ""
echo "To start the agent, run: ./start.sh"
echo ""
echo "Environment variables (set before starting):"
echo "  export DEEPSEEK_API_KEY=your_key   # Default provider"
echo "  export ANTHROPIC_API_KEY=your_key  # If using Anthropic"
echo "  export OPENAI_API_KEY=your_key     # If using OpenAI"
echo "  Ollama: no key needed (localhost:11434)"
echo ""
