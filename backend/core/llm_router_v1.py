"""
LLM Router — Supports DeepSeek (default), Ollama/Llama, Anthropic, OpenAI-compatible.
All providers normalised to a single streaming interface.
No Pydantic — pure dataclass.

version: 3.0.0
changelog:
  1.0.0 - Initial router with DeepSeek + Ollama
  2.0.0 - MODEL_MAX_TOKENS map; get_model_max_tokens() public helper
  3.0.0 - Removed Pydantic. LLMConfig is now a plain dataclass.
          Added retry logic (up to 2 retries on transient errors).
          Added request_id header for traceability.
          Hardened token extraction for all four providers.
"""
import httpx
import json
import os
import uuid
import logging
import asyncio
from dataclasses import dataclass, field
from typing import AsyncGenerator, Optional

logger = logging.getLogger("llm_router")

# ── Wire schema constants ──────────────────────────────────────────────────────
SCHEMA_OPENAI    = "openai"
SCHEMA_ANTHROPIC = "anthropic"


def _default_schema(provider: str) -> str:
    return SCHEMA_ANTHROPIC if provider == "anthropic" else SCHEMA_OPENAI


# ── Per-model max output token limits ─────────────────────────────────────────
DEFAULT_MAX_TOKENS = 8192

MODEL_MAX_TOKENS: dict[str, int] = {
    # DeepSeek
    "deepseek-chat":          8192,
    "deepseek-coder":         8192,
    "deepseek-v4-pro":        8192,
    "deepseek-v4-flash":      8192,
    "deepseek-reasoner":      8000,
    # Anthropic
    "claude-haiku-4-5":             8192,
    "claude-haiku-4-5-20251001":    8192,
    "claude-sonnet-4-20250514":     16000,
    "claude-sonnet-4-5":            16000,
    "claude-opus-4-5":              32000,
    "claude-opus-4-20250514":       32000,
    # OpenAI
    "gpt-3.5-turbo":    4096,
    "gpt-4":            8192,
    "gpt-4-turbo":      4096,
    "gpt-4o":           16384,
    "gpt-4o-mini":      16384,
    "o1":               32768,
    "o1-mini":          65536,
    "o3-mini":          100000,
    # Ollama
    "llama3":     4096,
    "llama3.1":   8192,
    "llama3.2":   8192,
    "codellama":  4096,
    "mistral":    8192,
    "phi3":       4096,
    "gemma2":     8192,
}


def get_model_max_tokens(model: str) -> int:
    """Return max output tokens for a model. Falls back to DEFAULT_MAX_TOKENS."""
    return MODEL_MAX_TOKENS.get(model, DEFAULT_MAX_TOKENS)


# ── Provider defaults ──────────────────────────────────────────────────────────
PROVIDER_DEFAULTS: dict[str, dict] = {
    "deepseek": {
        "base_url": "https://api.deepseek.com/v1",
        "model":    "deepseek-coder",
        "key_env":  "DEEPSEEK_API_KEY",
    },
    "ollama": {
        "base_url": os.getenv("OLLAMA_HOST", "http://localhost:11434") + "/v1",
        "model":    "llama3",
        "key_env":  None,
    },
    "anthropic": {
        "base_url": "https://api.anthropic.com/v1",
        "model":    "claude-sonnet-4-20250514",
        "key_env":  "ANTHROPIC_API_KEY",
    },
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "model":    "gpt-4o",
        "key_env":  "OPENAI_API_KEY",
    },
}

# ── LLMConfig — pure dataclass, no Pydantic ────────────────────────────────────

@dataclass
class LLMConfig:
    provider:      str           = "deepseek"
    model:         str           = "deepseek-coder"
    api_key:       Optional[str] = None
    base_url:      Optional[str] = None
    temperature:   float         = 0.7
    max_tokens:    int           = DEFAULT_MAX_TOKENS
    schema_format: Optional[str] = None   # None = auto-derive from provider
    timeout_s:     int           = 120
    max_retries:   int           = 2

    def get_schema(self) -> str:
        return self.schema_format or _default_schema(self.provider)

    def effective_max_tokens(self) -> int:
        """Cap requested max_tokens at the model's known limit."""
        return min(self.max_tokens, get_model_max_tokens(self.model))


# ── LLMRouter ─────────────────────────────────────────────────────────────────

class LLMRouter:
    """
    Unified async LLM client.
    Supports streaming (chat_stream) and non-streaming (chat) modes.
    All provider differences are normalised internally.
    """

    def __init__(self, config: LLMConfig):
        self.config   = config
        defaults      = PROVIDER_DEFAULTS.get(config.provider, PROVIDER_DEFAULTS["deepseek"])

        self.base_url = config.base_url or defaults["base_url"]
        self.model    = config.model    or defaults["model"]

        key_env = defaults.get("key_env")
        resolved_key = config.api_key or (os.getenv(key_env) if key_env else None)
        if not resolved_key:
            resolved_key = "ollama" if config.provider == "ollama" else f"MISSING_{key_env or 'API_KEY'}"
        self.api_key = resolved_key

    # ── Header builders ────────────────────────────────────────────────────────

    def _build_headers(self, request_id: str = None) -> dict:
        headers = {
            "Content-Type":   "application/json",
            "X-Request-ID":   request_id or str(uuid.uuid4()),
        }
        if self.config.get_schema() == SCHEMA_ANTHROPIC:
            headers["x-api-key"]         = self.api_key
            headers["anthropic-version"] = "2023-06-01"
        else:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _build_payload(self, messages: list, system: str = None, stream: bool = True) -> dict:
        max_tok = self.config.effective_max_tokens()

        if self.config.get_schema() == SCHEMA_ANTHROPIC:
            payload: dict = {
                "model":      self.model,
                "max_tokens": max_tok,
                "stream":     stream,
                "messages":   messages,
            }
            if system:
                payload["system"] = system
        else:
            all_msgs: list = []
            if system:
                all_msgs.append({"role": "system", "content": system})
            all_msgs.extend(messages)
            payload = {
                "model":       self.model,
                "messages":    all_msgs,
                "temperature": self.config.temperature,
                "max_tokens":  max_tok,
                "stream":      stream,
            }
        return payload

    def _endpoint(self, stream: bool = True) -> str:
        if self.config.provider == "anthropic":
            return f"{self.base_url}/messages"
        return f"{self.base_url}/chat/completions"

    # ── Token extractors ───────────────────────────────────────────────────────

    def _extract_token(self, chunk: dict) -> str:
        try:
            if self.config.get_schema() == SCHEMA_ANTHROPIC:
                if chunk.get("type") == "content_block_delta":
                    return chunk.get("delta", {}).get("text", "")
                return ""
            else:
                delta = chunk.get("choices", [{}])[0].get("delta", {})
                return delta.get("content") or ""
        except (KeyError, IndexError, TypeError):
            return ""

    def _extract_full(self, data: dict) -> str:
        try:
            if self.config.get_schema() == SCHEMA_ANTHROPIC:
                return data["content"][0]["text"]
            else:
                return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            return str(data)

    # ── Public streaming interface ─────────────────────────────────────────────

    async def chat_stream(
        self,
        messages: list,
        system:   str = None,
    ) -> AsyncGenerator[str, None]:
        """
        Stream tokens from the LLM. Yields string fragments.
        Retries up to config.max_retries on transient HTTP errors (429, 503, 502).
        """
        request_id = str(uuid.uuid4())[:8]
        endpoint   = self._endpoint(stream=True)
        payload    = self._build_payload(messages, system, stream=True)
        headers    = self._build_headers(request_id)

        logger.debug("[llm_stream_start] req=%s provider=%s model=%s", request_id, self.config.provider, self.model)

        retries = 0
        while True:
            try:
                async with httpx.AsyncClient(timeout=self.config.timeout_s) as client:
                    async with client.stream("POST", endpoint, headers=headers, json=payload) as response:
                        if response.status_code in (429, 502, 503) and retries < self.config.max_retries:
                            retries += 1
                            wait = 2 ** retries
                            logger.warning("[llm_retry] req=%s status=%d retry=%d wait=%ds",
                                           request_id, response.status_code, retries, wait)
                            await asyncio.sleep(wait)
                            continue
                        response.raise_for_status()
                        async for line in response.aiter_lines():
                            if not line or line == "data: [DONE]":
                                continue
                            data = line[6:] if line.startswith("data: ") else line
                            try:
                                chunk = json.loads(data)
                                token = self._extract_token(chunk)
                                if token:
                                    yield token
                            except json.JSONDecodeError:
                                continue
                return  # success — exit retry loop

            except httpx.HTTPStatusError as exc:
                if exc.response.status_code in (429, 502, 503) and retries < self.config.max_retries:
                    retries += 1
                    await asyncio.sleep(2 ** retries)
                    continue
                yield f"\n[LLM ERROR] HTTP {exc.response.status_code}: {exc.response.text[:200]}"
                return

            except httpx.ConnectError:
                yield f"\n[LLM ERROR] Cannot connect to {self.base_url}. Provider running?"
                return

            except httpx.TimeoutException:
                yield f"\n[LLM ERROR] Request timed out after {self.config.timeout_s}s."
                return

            except Exception as exc:
                yield f"\n[LLM ERROR] {type(exc).__name__}: {exc}"
                return

    async def chat(self, messages: list, system: str = None) -> str:
        """Non-streaming call. Returns full response string."""
        endpoint = self._endpoint(stream=False)
        payload  = self._build_payload(messages, system, stream=False)
        headers  = self._build_headers()

        for attempt in range(self.config.max_retries + 1):
            try:
                async with httpx.AsyncClient(timeout=self.config.timeout_s) as client:
                    resp = await client.post(endpoint, headers=headers, json=payload)
                    if resp.status_code in (429, 502, 503) and attempt < self.config.max_retries:
                        await asyncio.sleep(2 ** (attempt + 1))
                        continue
                    resp.raise_for_status()
                    return self._extract_full(resp.json())
            except Exception as exc:
                if attempt < self.config.max_retries:
                    await asyncio.sleep(2 ** (attempt + 1))
                    continue
                return f"[LLM ERROR] {type(exc).__name__}: {exc}"

        return "[LLM ERROR] Max retries exceeded"
