#!/bin/bash
# ─── Agent Start Script ───────────────────────────────────────────────────────

set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo ""
echo "◈ STARTING AI AGENT"
echo "════════════════════════════════════════"
echo "  Backend  → http://localhost:8000"
echo "  Frontend → http://localhost:5173"
echo "  API Docs → http://localhost:8000/docs"
echo "  Provider → ${DEEPSEEK_API_KEY:+DeepSeek ✓}${DEEPSEEK_API_KEY:-DeepSeek ✗ (set DEEPSEEK_API_KEY)}"
echo "════════════════════════════════════════"
echo ""

# Cleanup on exit
cleanup() {
  echo ""
  echo "Shutting down..."
  kill $BACKEND_PID $FRONTEND_PID 2>/dev/null || true
}
trap cleanup EXIT INT TERM

# ─── Start Backend ────────────────────────────────────────────────────────────
cd "$SCRIPT_DIR/backend"

if [ -d ".venv" ]; then
  source .venv/bin/activate
fi

python3 -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload &
BACKEND_PID=$!
echo "  ✓ Backend started (PID $BACKEND_PID)"

# ─── Start Frontend ───────────────────────────────────────────────────────────
cd "$SCRIPT_DIR/frontend"
npm run dev -- --host 0.0.0.0 --port 5173 &
FRONTEND_PID=$!
echo "  ✓ Frontend started (PID $FRONTEND_PID)"

echo ""
echo "  Both services running. Ctrl+C to stop."
echo ""

wait $BACKEND_PID $FRONTEND_PID
