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
import traceback
logger = logging.getLogger("llm_router")

# ── Ollama auto-detection ──────────────────────────────────────────────────────

_OLLAMA_PROBE_TIMEOUT = 2.0   # seconds — fast, non-blocking probe

def _probe_ollama(base_url: str) -> Optional[str]:
    """
    Probe the Ollama /api/tags endpoint and return the first available model name,
    or None if Ollama is unreachable or has no models loaded.
    Uses a short synchronous httpx call so it can be used at config-build time.
    """
    import httpx as _httpx
    try:
        tags_url = base_url.rstrip("/v1").rstrip("/") + "/api/tags"
        resp = _httpx.get(tags_url, timeout=_OLLAMA_PROBE_TIMEOUT)
        if resp.status_code == 200:
            models = resp.json().get("models", [])
            if models:
                return models[0].get("name") or models[0].get("model")
    except Exception:
        pass
    return None


def resolve_config_with_ollama_fallback(
    provider:    str,
    model:       str,
    api_key:     Optional[str] = None,
    ollama_host: Optional[str] = None,
) -> tuple[str, str, Optional[str]]:
    """
    If *provider* has no API key and Ollama is reachable in Docker, transparently
    fall back to Ollama.

    Returns (resolved_provider, resolved_model, resolved_api_key).

    Fallback triggers when ALL of these are true:
      1. No api_key was provided for the current provider.
      2. The provider is not already 'ollama'.
      3. Ollama is reachable at OLLAMA_HOST (default: http://ollama:11434).
    """
    if provider == "ollama" or api_key:
        return provider, model, api_key   # nothing to do

    # Determine Ollama base URL — prefer explicit arg, then env, then Docker default
    raw_host = (
        ollama_host
        or os.getenv("OLLAMA_HOST", "http://ollama:11434")
    )
    # Normalise: strip trailing /v1 so _probe_ollama can build /api/tags cleanly
    host_base = raw_host.rstrip("/v1").rstrip("/")

    detected_model = _probe_ollama(host_base)
    if detected_model is not None:
        logger.warning(
            "[ollama_fallback] No API key for provider=%s — falling back to Ollama "
            "at %s with model=%s",
            provider, host_base, detected_model,
        )
        return "ollama", detected_model, None

    # Ollama not available either — return as-is and let the router surface the error
    logger.warning(
        "[no_key_no_ollama] provider=%s has no API key and Ollama is unreachable at %s",
        provider, host_base,
    )
    return provider, model, api_key


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
        "base_url": os.getenv("OLLAMA_HOST", "http://localhost:11434/api/chat") ,
        "model":    "qwen2.5-coder:0.5b",
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
    provider:      str           = "ollama"
    model:         str           = "qwen2.5-coder:0.5b"
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
        # ── Ollama fallback — resolve before touching defaults ─────────────────
        raw_key = config.api_key or (
            os.getenv(PROVIDER_DEFAULTS.get(config.provider, {}).get("key_env") or "")
            if PROVIDER_DEFAULTS.get(config.provider, {}).get("key_env")
            else None
        )
        resolved_provider, resolved_model, resolved_key = resolve_config_with_ollama_fallback(
            provider = config.provider,
            model    = config.model,
            api_key  = raw_key,
        )
        # Patch config in-place so the rest of the class sees consistent values
        config.provider = resolved_provider
        config.model    = resolved_model

        self.config   = config
        defaults      = PROVIDER_DEFAULTS.get(config.provider, PROVIDER_DEFAULTS["deepseek"])

        self.base_url = config.base_url or defaults["base_url"]
        self.model    = resolved_model  or defaults["model"]

        if not resolved_key:
            resolved_key = "ollama" if config.provider == "ollama" else f"MISSING_{defaults.get('key_env') or 'API_KEY'}"
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
        if self.config.provider == "ollama":
            return f"{self.base_url}/api/chat"
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

        logger.info("[llm_stream_start] endpoint=%s req=%s provider=%s model=%s", endpoint, request_id, self.config.provider, self.model)
        #logger.debug("[llm_stream_start] endpoint=%s req=%s provider=%s model=%s", endpoint, request_id, self.config.provider, self.model)

        retries = 0
        while True:
            _retry_after_stream = False   # set inside the stream ctx, acted on outside it
            _wait_s             = 0
            try:
                async with httpx.AsyncClient(timeout=self.config.timeout_s) as client:
                    async with client.stream("POST", endpoint, headers=headers, json=payload) as response:
                        if response.status_code in (429, 502, 503) and retries < self.config.max_retries:
                            # Must drain the response before leaving the stream context manager —
                            # httpx raises ResponseNotRead if we exit without reading first.
                            await response.aread()
                            retries += 1
                            _wait_s = 2 ** retries
                            logger.warning("[llm_retry] req=%s status=%d retry=%d wait=%ds",
                                           request_id, response.status_code, retries, _wait_s)
                            _retry_after_stream = True
                            # Fall through to exit the context manager cleanly — do NOT continue here.
                        else:
                            # Read error body BEFORE raise_for_status — once we leave
                            # the stream context the connection is gone and aread() fails.
                            if response.status_code >= 400:
                                await response.aread()
                                err_body = response.text[:300]
                                if response.status_code in (429, 502, 503) and retries < self.config.max_retries:
                                    retries += 1
                                    _wait_s = 2 ** retries
                                    logger.warning("[llm_retry] req=%s status=%d retry=%d wait=%ds",
                                                   request_id, response.status_code, retries, _wait_s)
                                    _retry_after_stream = True
                                else:
                                    # Store for yield after context manager closes
                                    _retry_after_stream = False
                                    _err_yield = f"\n[LLM ERROR] HTTP {response.status_code}: {err_body}"
                            else:
                                _err_yield = None
                                async for line in response.aiter_lines():
                                    if not line or line == "data: [DONE]":
                                        continue
                                    raw = line[6:] if line.startswith("data: ") else line
                                    try:
                                        chunk = json.loads(raw)
                                        token = self._extract_token(chunk)
                                        if token:
                                            yield token
                                    except json.JSONDecodeError:
                                        traceback.print_exc()
                                        continue

                # ── Outside the stream context manager ────────────────────────
                if _retry_after_stream:
                    await asyncio.sleep(_wait_s)
                    continue        # safe to retry now — context manager is closed
                if _err_yield:
                    yield _err_yield
                    return
                return              # success

            except httpx.HTTPStatusError as exc:
                traceback.print_exc()
                # Fallback: raised by raise_for_status() if we missed it above.
                # Body is likely gone at this point; surface what we can.
                err_body = getattr(exc.response, "_content", None)
                if err_body is not None:
                    try:
                        err_body = err_body.decode(errors="replace")[:300]
                    except Exception:
                        err_body = repr(err_body)[:300]
                else:
                    err_body = str(exc)[:300]
                if exc.response.status_code in (429, 502, 503) and retries < self.config.max_retries:
                    retries += 1
                    await asyncio.sleep(2 ** retries)
                    continue
                yield f"\n[LLM ERROR] HTTP {exc.response.status_code}: {err_body}"
                return

            except httpx.ConnectError:
                traceback.print_exc()
                yield f"\n[LLM ERROR] Cannot connect to {self.base_url}. Provider running?"
                return

            except httpx.TimeoutException:
                traceback.print_exc()
                yield f"\n[LLM ERROR] Request timed out after {self.config.timeout_s}s."
                return

            except Exception as exc:
                traceback.print_exc()
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
                traceback.print_exc()
                if attempt < self.config.max_retries:
                    await asyncio.sleep(2 ** (attempt + 1))
                    continue
                return f"[LLM ERROR] {type(exc).__name__}: {exc}"

        return "[LLM ERROR] Max retries exceeded"