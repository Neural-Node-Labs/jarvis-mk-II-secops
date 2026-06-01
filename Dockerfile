# ─── Stage 1: Build React app ──────────────────────────────────────────────────
FROM node:20-alpine AS frontend-builder

WORKDIR /app

COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci 2>/dev/null || npm install

COPY frontend/ .

# Leave VITE_API_URL empty — nginx proxies /api/ and /ws/ to uvicorn internally
RUN npm run build


# ─── Stage 2: Combined runtime (nginx + FastAPI via supervisord) ────────────────
FROM python:3.11-slim AS runtime

# ── System packages: nginx + supervisor + wget (for healthcheck) ──────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
        nginx \
        supervisor \
        wget \
    && rm -rf /var/lib/apt/lists/*

# ── Python dependencies ───────────────────────────────────────────────────────
# Install deps to a NON-/app path so the /app volume mount doesn't hide them
WORKDIR /app
COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# ── Backend source ────────────────────────────────────────────────────────────
COPY backend/ ./

# ── Frontend build artifacts → nginx root ────────────────────────────────────
COPY --from=frontend-builder /app/dist /usr/share/nginx/html

# ── nginx config ─────────────────────────────────────────────────────────────
RUN rm -f /etc/nginx/sites-enabled/default /etc/nginx/conf.d/default.conf
COPY deploy/nginx.conf /etc/nginx/conf.d/agent.conf

# ── supervisord config ────────────────────────────────────────────────────────
COPY deploy/supervisord.conf /etc/supervisor/conf.d/agent.conf

# ── Runtime directories ───────────────────────────────────────────────────────
RUN mkdir -p /app/data /app/experienced \
    && mkdir -p /var/log/supervisor

# ── Health check ──────────────────────────────────────────────────────────────
HEALTHCHECK --interval=15s --timeout=5s --start-period=25s --retries=5 \
    CMD wget -qO- http://localhost:80/health || exit 1

EXPOSE 80

CMD ["/usr/bin/supervisord", "-n", "-c", "/etc/supervisor/conf.d/agent.conf"]