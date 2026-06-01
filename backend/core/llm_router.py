"""
LLM Router - Supports DeepSeek (default), Ollama/Llama, Anthropic, OpenAI-compatible
All providers normalized to a single streaming interface.
"""
import httpx
import json
import os
from typing import AsyncGenerator, Optional
from pydantic import BaseModel


# Schema format used when building request payloads and parsing responses.
# Derived automatically from provider, but can be overridden explicitly.
#   "openai"    — OpenAI-compatible: system as first message, choices[0].delta.content
#   "anthropic" — Anthropic Messages API: top-level system field, content_block_delta
SCHEMA_OPENAI    = "openai"
SCHEMA_ANTHROPIC = "anthropic"

def _default_schema(provider: str) -> str:
    """Return the correct wire schema for a provider."""
    return SCHEMA_ANTHROPIC if provider == "anthropic" else SCHEMA_OPENAI


class LLMConfig(BaseModel):
    provider: str = "deepseek"      # deepseek | ollama | anthropic | openai
    model: str = "deepseek-v4-pro"
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    temperature: float = 0.7
    max_tokens: int = 4096
    # schema_format is set automatically from provider but can be forced:
    #   SCHEMA_OPENAI    — OpenAI-compatible wire format
    #   SCHEMA_ANTHROPIC — Anthropic Messages API wire format
    schema_format: Optional[str] = None  # None = auto-derive from provider

    def get_schema(self) -> str:
        """Resolve the active wire schema."""
        return self.schema_format or _default_schema(self.provider)


PROVIDER_DEFAULTS = {
    "deepseek": {
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-v4-pro",
        "key_env": "DEEPSEEK_API_KEY",
    },
    "ollama": {
        "base_url": os.getenv("OLLAMA_HOST", "http://localhost:11434") + "/v1",
        "model": "llama3",
        "key_env": None,                # Ollama needs no key
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


class LLMRouter:
    def __init__(self, config: LLMConfig):
        self.config = config
        defaults = PROVIDER_DEFAULTS.get(config.provider, PROVIDER_DEFAULTS["deepseek"])

        self.base_url = config.base_url or defaults["base_url"]
        self.model = config.model or defaults["model"]

        # Resolve API key — prefer explicit config, then env var, then placeholder
        key_env = defaults.get("key_env")
        resolved_key = config.api_key or (os.getenv(key_env) if key_env else None)
        # Ollama needs no key; for all others warn if missing
        if not resolved_key:
            if config.provider == "ollama":
                resolved_key = "ollama"
            else:
                # Will surface as a clear LLM ERROR rather than a cryptic 401
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
        if self.config.get_schema() == SCHEMA_ANTHROPIC:
            payload = {
                "model": self.model,
                "max_tokens": self.config.max_tokens,
                "stream": stream,
                "messages": messages,
            }
            if system:
                payload["system"] = system
        else:
            # OpenAI-compatible (DeepSeek, Ollama, OpenAI all use same format)
            all_messages = []
            if system:
                all_messages.append({"role": "system", "content": system})
            all_messages.extend(messages)
            payload = {
                "model": self.model,
                "messages": all_messages,
                "temperature": self.config.temperature,
                "max_tokens": self.config.max_tokens,
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
