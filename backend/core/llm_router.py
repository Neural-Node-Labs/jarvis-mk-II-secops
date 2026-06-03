"""
LLM Router - Supports DeepSeek (default), Ollama/Llama, Anthropic, OpenAI-compatible
All providers normalized to a single streaming interface.

version: 2.0.0
changelog:
  2.0.0 - MODEL_MAX_TOKENS map for dynamic max_token limits per model.
          get_model_max_tokens() public helper used by the API.
"""
import httpx
import json
import os
from typing import AsyncGenerator, Optional
from pydantic import BaseModel


# ── Wire schema constants ──────────────────────────────────────────────────────
SCHEMA_OPENAI    = "openai"
SCHEMA_ANTHROPIC = "anthropic"

def _default_schema(provider: str) -> str:
    return SCHEMA_ANTHROPIC if provider == "anthropic" else SCHEMA_OPENAI


# ── Per-model max context / output token limits ────────────────────────────────
# Values represent the maximum *output* tokens the model supports.
# When a model is not in this map, DEFAULT_MAX_TOKENS is used as fallback.
DEFAULT_MAX_TOKENS = 8192

MODEL_MAX_TOKENS: dict[str, int] = {
    # ── DeepSeek ──────────────────────────────────────────────────────────────
    "deepseek-chat":            8192,
    "deepseek-coder":           8192,
    "deepseek-v4-pro":          8192,
    "deepseek-v4-flash":        8192,
    "deepseek-reasoner":        8000,

    # ── Anthropic ─────────────────────────────────────────────────────────────
    "claude-haiku-4-5":             8192,
    "claude-haiku-4-5-20251001":    8192,
    "claude-sonnet-4-20250514":     16000,
    "claude-sonnet-4-5":            16000,
    "claude-opus-4-5":              32000,
    "claude-opus-4-20250514":       32000,

    # ── OpenAI ────────────────────────────────────────────────────────────────
    "gpt-3.5-turbo":            4096,
    "gpt-4":                    8192,
    "gpt-4-turbo":              4096,
    "gpt-4o":                   16384,
    "gpt-4o-mini":              16384,
    "o1":                       32768,
    "o1-mini":                  65536,
    "o3-mini":                  100000,

    # ── Ollama / Llama ────────────────────────────────────────────────────────
    "llama3":       4096,
    "llama3.1":     8192,
    "llama3.2":     8192,
    "codellama":    4096,
    "mistral":      8192,
    "phi3":         4096,
    "gemma2":       8192,
}


def get_model_max_tokens(model: str) -> int:
    """
    Return the maximum output tokens for the given model name.
    Falls back to DEFAULT_MAX_TOKENS for unknown models.
    """
    return MODEL_MAX_TOKENS.get(model, DEFAULT_MAX_TOKENS)


# ── Provider defaults ──────────────────────────────────────────────────────────
PROVIDER_DEFAULTS = {
    "deepseek": {
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-coder",
        "key_env": "DEEPSEEK_API_KEY",
    },
    "ollama": {
        "base_url": os.getenv("OLLAMA_HOST", "http://localhost:11434") + "/v1",
        "model": "llama3",
        "key_env": None,
    },
    "anthropic": {
        "base_url": "https://api.anthropic.com/v1",
        "model": "claude-sonnet-4-20250514",
        "key_env": "ANTHROPIC_API_KEY",
    },
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o",
        "key_env": "OPENAI_API_KEY",
    },
}


class LLMConfig(BaseModel):
    provider: str = "deepseek"
    model: str = "deepseek-coder"
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    temperature: float = 0.7
    max_tokens: int = DEFAULT_MAX_TOKENS
    schema_format: Optional[str] = None  # None = auto-derive from provider

    def get_schema(self) -> str:
        return self.schema_format or _default_schema(self.provider)

    def effective_max_tokens(self) -> int:
        """
        Return the effective max_tokens, capped by the model's known limit.
        If the user asked for more than the model supports, silently cap it.
        """
        cap = get_model_max_tokens(self.model)
        return min(self.max_tokens, cap)


class LLMRouter:
    def __init__(self, config: LLMConfig):
        self.config = config
        defaults = PROVIDER_DEFAULTS.get(config.provider, PROVIDER_DEFAULTS["deepseek"])

        self.base_url = config.base_url or defaults["base_url"]
        self.model = config.model or defaults["model"]

        key_env = defaults.get("key_env")
        resolved_key = config.api_key or (os.getenv(key_env) if key_env else None)
        if not resolved_key:
            if config.provider == "ollama":
                resolved_key = "ollama"
            else:
                resolved_key = f"MISSING_{(key_env or 'API_KEY')}"
        self.api_key = resolved_key

    def _build_headers(self) -> dict:
        headers = {"Content-Type": "application/json"}
        if self.config.get_schema() == SCHEMA_ANTHROPIC:
            headers["x-api-key"] = self.api_key
            headers["anthropic-version"] = "2023-06-01"
        else:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _build_payload(self, messages: list, system: str = None, stream: bool = True) -> dict:
        max_tok = self.config.effective_max_tokens()
        if self.config.get_schema() == SCHEMA_ANTHROPIC:
            payload = {
                "model": self.model,
                "max_tokens": max_tok,
                "stream": stream,
                "messages": messages,
            }
            if system:
                payload["system"] = system
        else:
            all_messages = []
            if system:
                all_messages.append({"role": "system", "content": system})
            all_messages.extend(messages)
            payload = {
                "model": self.model,
                "messages": all_messages,
                "temperature": self.config.temperature,
                "max_tokens": max_tok,
                "stream": stream,
            }
        return payload

    async def chat_stream(
        self, messages: list, system: str = None
    ) -> AsyncGenerator[str, None]:
        """Stream tokens from the configured LLM provider."""
        endpoint = (
            f"{self.base_url}/messages"
            if self.config.provider == "anthropic"
            else f"{self.base_url}/chat/completions"
        )
        payload = self._build_payload(messages, system, stream=True)

        try:
            async with httpx.AsyncClient(timeout=120) as client:
                async with client.stream(
                    "POST", endpoint, headers=self._build_headers(), json=payload
                ) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if not line or line == "data: [DONE]":
                            continue
                        if line.startswith("data: "):
                            line = line[6:]
                        try:
                            chunk = json.loads(line)
                            token = self._extract_token(chunk)
                            if token:
                                yield token
                        except json.JSONDecodeError:
                            continue
        except httpx.HTTPStatusError as e:
            yield f"\n[LLM ERROR] HTTP {e.response.status_code}: {e.response.text}"
        except httpx.ConnectError:
            yield f"\n[LLM ERROR] Cannot connect to {self.base_url}. Is the provider running?"
        except Exception as e:
            yield f"\n[LLM ERROR] {type(e).__name__}: {str(e)}"

    async def chat(self, messages: list, system: str = None) -> str:
        """Non-streaming chat call."""
        endpoint = (
            f"{self.base_url}/messages"
            if self.config.provider == "anthropic"
            else f"{self.base_url}/chat/completions"
        )
        payload = self._build_payload(messages, system, stream=False)
        try:
            async with httpx.AsyncClient(timeout=120) as client:
                resp = await client.post(
                    endpoint, headers=self._build_headers(), json=payload
                )
                resp.raise_for_status()
                data = resp.json()
                return self._extract_full(data)
        except Exception as e:
            return f"[LLM ERROR] {type(e).__name__}: {str(e)}"

    def _extract_token(self, chunk: dict) -> str:
        try:
            if self.config.get_schema() == SCHEMA_ANTHROPIC:
                if chunk.get("type") == "content_block_delta":
                    return chunk["delta"].get("text", "")
            else:
                return chunk["choices"][0]["delta"].get("content", "") or ""
        except (KeyError, IndexError):
            return ""

    def _extract_full(self, data: dict) -> str:
        try:
            if self.config.get_schema() == SCHEMA_ANTHROPIC:
                return data["content"][0]["text"]
            else:
                return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError):
            return str(data)
