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

# ── System packages: added core tools needed for SecOps & System skills ───────
RUN apt-get update && apt-get install -y --no-install-recommends \
        nginx \
        supervisor \
        wget \
        # Dependencies for network_recon, port_scanner, and dns_lookup
        nmap \
        dnsutils \
        whois \
        # Dependencies for cryptographic/SSL tools and compilation if needed
        build-essential \
        libssl-dev \
        libffi-dev \
        # General utilities for OS execution skills
        curl \
        git \
        procps \
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

# Create persistent data directories and set permissions BEFORE switching user
# /app/data  — settings.json (provider config)
# /app/experienced — Experienced knowledge base entries + index.md
RUN mkdir -p /app/data /app/experienced \
    && useradd -m -u 1001 jarvis \
    && mkdir -p /var/log/supervisor \
    && chown -R jarvis:jarvis /app

# USER jarvis

# ── Health check ──────────────────────────────────────────────────────────────
HEALTHCHECK --interval=15s --timeout=5s --start-period=25s --retries=5 \
    CMD wget -qO- http://localhost:80/health || exit 1

EXPOSE 80

CMD ["/usr/bin/supervisord", "-n", "-c", "/etc/supervisor/conf.d/agent.conf"]