# ─── AI Agent — Makefile ──────────────────────────────────────────────────────
# Usage: make <target>
# Requires: docker, docker compose (v2)

.PHONY: help setup up down build rebuild logs shell-backend shell-frontend \
        pull-llama3 pull-llama31 ps clean

COMPOSE = docker compose
APP_URL = http://localhost:$${PORT:-3000}

help: ## Show this help
	@echo ""
	@echo "  ◈ AI Agent — Docker Commands"
	@echo "  ══════════════════════════════════════"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'
	@echo ""

setup: ## First-time setup: copy .env.example → .env
	@if [ ! -f .env ]; then \
		cp .env.example .env; \
		echo "✓ Created .env — edit it and add your DEEPSEEK_API_KEY"; \
	else \
		echo "✓ .env already exists"; \
	fi

up: ## Start all services (builds if needed)
	$(COMPOSE) up -d
	@echo ""
	@echo "  ✓ Agent running at $(APP_URL)"
	@echo "  Run 'make logs' to follow output"

down: ## Stop all services
	$(COMPOSE) down

build: ## Build images without starting
	$(COMPOSE) build

rebuild: ## Force rebuild images (no cache)
	$(COMPOSE) build --no-cache

restart: ## Restart all services
	$(COMPOSE) restart

logs: ## Follow logs from all services
	$(COMPOSE) logs -f

logs-backend: ## Follow backend logs only
	$(COMPOSE) logs -f backend

logs-frontend: ## Follow frontend logs only
	$(COMPOSE) logs -f frontend

ps: ## Show running containers and health
	$(COMPOSE) ps

shell-backend: ## Open shell in backend container
	$(COMPOSE) exec backend bash || $(COMPOSE) exec backend sh

shell-frontend: ## Open shell in frontend container
	$(COMPOSE) exec frontend sh

# ─── Ollama helpers ───────────────────────────────────────────────────────────
pull-llama3: ## Pull llama3 model into Ollama (uncomment ollama service first)
	$(COMPOSE) exec ollama ollama pull llama3

pull-llama31: ## Pull llama3.1 model into Ollama
	$(COMPOSE) exec ollama ollama pull llama3.1

pull-codellama: ## Pull codellama model into Ollama
	$(COMPOSE) exec ollama ollama pull codellama

# ─── Maintenance ──────────────────────────────────────────────────────────────
clean: ## Remove containers, networks, images (keeps .env and volumes)
	$(COMPOSE) down --rmi local --remove-orphans

clean-all: ## Remove everything including volumes (DELETES Ollama models)
	$(COMPOSE) down --rmi all --volumes --remove-orphans
