"""
Image Vision Skill — Analyze images using the configured LLM's vision capability.

Actions:
  analyze  — General-purpose image analysis with a custom prompt
  describe — Plain-language description of image contents
  ocr      — Extract all visible text from an image
  compare  — Compare two images and describe differences / similarities
  detect   — Detect specific objects, faces, logos, or elements in an image

Supported providers (vision-capable models):
  anthropic : claude-sonnet-4-20250514, claude-opus-4-5, claude-haiku-4-5, etc.
  openai    : gpt-4o, gpt-4o-mini, gpt-4-turbo
  ollama    : llava, bakllava, moondream (must be pulled locally)

Image input formats accepted by this skill:
  • base64  : raw base64 string (preferred)
  • url     : publicly accessible https:// URL
  • path    : local filesystem path (read & converted to base64 internally)

Params contract for every action (unless noted):
  image_b64   : str  — base64-encoded image (PNG / JPEG / GIF / WEBP)
  image_url   : str  — OR a public image URL (alternative to base64)
  image_path  : str  — OR a local file path (alternative to base64)
  mime        : str  — MIME type, e.g. "image/jpeg" (default: "image/jpeg")
  prompt      : str  — (analyze only) custom instruction for the model
  image_b64_2 : str  — (compare only) second image base64
  image_url_2 : str  — (compare only) second image URL
  target      : str  — (detect only) what to look for, e.g. "faces, logos, text"
"""
from __future__ import annotations

import base64
import json
import logging
import os
from pathlib import Path
from typing import Any, Optional

import httpx

from core.skill_registry import SkillResult

logger = logging.getLogger("skill.image_vision")

# ── Vision-capable model registry ─────────────────────────────────────────────
VISION_MODELS = {
    "anthropic": [
        "claude-sonnet-4-20250514", "claude-sonnet-4-5",
        "claude-opus-4-5", "claude-opus-4-20250514",
        "claude-haiku-4-5", "claude-haiku-4-5-20251001",
    ],
    "openai": ["gpt-4o", "gpt-4o-mini", "gpt-4-turbo", "gpt-4-vision-preview"],
    "ollama": ["llava", "llava:13b", "llava:34b", "bakllava", "moondream", "llava-phi3"],
}

# Models known NOT to support vision (skip graceful warning)
NO_VISION_MODELS = {
    "deepseek-chat", "deepseek-coder", "deepseek-v4-pro", "deepseek-v4-flash",
    "deepseek-reasoner", "gpt-3.5-turbo", "codellama", "llama3", "mistral", "phi3",
}

# ── Action prompts ─────────────────────────────────────────────────────────────
ACTION_PROMPTS = {
    "describe": (
        "Describe this image in detail. Cover: the main subject, setting/background, "
        "colours, mood, any text visible, and anything notable or unusual."
    ),
    "ocr": (
        "Extract ALL text visible in this image. Preserve the original layout as closely "
        "as possible. If no text is visible, say 'No text found'."
    ),
    "detect": "Identify and locate the following in the image: {target}. "
              "For each item found, describe its position, size, and any relevant details. "
              "If something is not found, explicitly state that.",
    "compare": (
        "You are given two images. Compare them thoroughly:\n"
        "1. Key similarities\n2. Key differences\n3. Quality/composition comparison\n"
        "4. Notable changes (if they appear to show the same subject at different times)"
    ),
}

MAX_IMAGE_B64_CHARS = 20 * 1024 * 1024   # 20 MB base64 hard limit (~15 MB raw)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _load_image(
    b64: Optional[str] = None,
    url: Optional[str] = None,
    path: Optional[str] = None,
    mime: str = "image/jpeg",
) -> tuple[str, str]:
    """
    Resolve an image from one of the three input formats.
    Returns (base64_string, mime_type).
    """
    if b64:
        # Strip data URI prefix if present: "data:image/png;base64,..."
        if "," in b64:
            header, b64 = b64.split(",", 1)
            if "image/" in header:
                mime = header.split(":")[1].split(";")[0]
        if len(b64) > MAX_IMAGE_B64_CHARS:
            raise ValueError(f"Image base64 too large ({len(b64)} chars, max {MAX_IMAGE_B64_CHARS})")
        return b64, mime

    if path:
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(f"Image path not found: {path}")
        ext = p.suffix.lower()
        mime_map = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
                    ".gif": "image/gif", ".webp": "image/webp"}
        mime = mime_map.get(ext, "image/jpeg")
        raw = p.read_bytes()
        return base64.b64encode(raw).decode("ascii"), mime

    if url:
        # For URL-based images we return a sentinel so the caller can use the URL directly
        return "__URL__", url   # handled specially in payload builders

    raise ValueError("One of image_b64, image_url, or image_path must be provided.")


def _is_url_image(b64: str) -> bool:
    return b64 == "__URL__"


# ─────────────────────────────────────────────────────────────────────────────
# Provider-specific payload builders
# ─────────────────────────────────────────────────────────────────────────────

def _build_anthropic_message(
    prompt: str,
    b64: str, mime: str,
    b64_2: Optional[str] = None, mime_2: Optional[str] = None,
) -> list[dict]:
    """Build Anthropic Messages API content blocks."""
    content = []

    def _img_block(image_b64: str, image_mime: str) -> dict:
        if _is_url_image(image_b64):
            return {"type": "image", "source": {"type": "url", "url": image_mime}}
        return {
            "type": "image",
            "source": {"type": "base64", "media_type": image_mime, "data": image_b64},
        }

    content.append(_img_block(b64, mime))
    if b64_2 and mime_2:
        content.append(_img_block(b64_2, mime_2))
    content.append({"type": "text", "text": prompt})
    return [{"role": "user", "content": content}]


def _build_openai_message(
    prompt: str,
    b64: str, mime: str,
    b64_2: Optional[str] = None, mime_2: Optional[str] = None,
) -> list[dict]:
    """Build OpenAI-compatible vision message."""
    content = []

    def _img_part(image_b64: str, image_mime: str) -> dict:
        if _is_url_image(image_b64):
            return {"type": "image_url", "image_url": {"url": image_mime, "detail": "high"}}
        return {
            "type": "image_url",
            "image_url": {"url": f"data:{image_mime};base64,{image_b64}", "detail": "high"},
        }

    content.append(_img_part(b64, mime))
    if b64_2 and mime_2:
        content.append(_img_part(b64_2, mime_2))
    content.append({"type": "text", "text": prompt})
    return [{"role": "user", "content": content}]


# ─────────────────────────────────────────────────────────────────────────────
# Core vision call (async, non-streaming)
# ─────────────────────────────────────────────────────────────────────────────

async def _call_vision_llm(
    prompt: str,
    b64: str, mime: str,
    config,   # LLMConfig
    b64_2: Optional[str] = None,
    mime_2: Optional[str] = None,
) -> str:
    """
    Send an image + prompt to the configured LLM and return the response text.
    Raises ValueError if the current model is not vision-capable.
    """
    provider = config.provider
    model = config.model

    # Soft warning for known non-vision models
    if model in NO_VISION_MODELS:
        return (
            f"⚠️ Model '{model}' does not support vision/image inputs. "
            f"Switch to a vision-capable model such as:\n"
            f"  • Anthropic: claude-sonnet-4-20250514, claude-opus-4-5\n"
            f"  • OpenAI: gpt-4o, gpt-4o-mini\n"
            f"  • Ollama: llava, moondream\n"
            f"You can switch models using the model selector in the header."
        )

    base_url = config.base_url or _get_default_base_url(provider)
    api_key = config.api_key or ""
    max_tokens = min(config.max_tokens, 4096)

    if provider == "anthropic":
        messages = _build_anthropic_message(prompt, b64, mime, b64_2, mime_2)
        headers = {
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        }
        payload = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": messages,
        }
        endpoint = f"{base_url}/messages"

    else:
        # OpenAI-compatible (openai, ollama)
        messages = _build_openai_message(prompt, b64, mime, b64_2, mime_2)
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }
        payload = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": config.temperature,
        }
        endpoint = f"{base_url}/chat/completions"

    try:
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(endpoint, headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()

        if provider == "anthropic":
            return data["content"][0]["text"]
        else:
            return data["choices"][0]["message"]["content"]

    except httpx.HTTPStatusError as e:
        body = e.response.text[:400]
        raise RuntimeError(f"Vision API HTTP {e.response.status_code}: {body}") from e
    except httpx.ConnectError as e:
        raise RuntimeError(f"Cannot connect to {base_url}") from e


def _get_default_base_url(provider: str) -> str:
    DEFAULTS = {
        "anthropic": "https://api.anthropic.com/v1",
        "openai": "https://api.openai.com/v1",
        "ollama": os.getenv("OLLAMA_HOST", "http://localhost:11434") + "/v1",
        "deepseek": "https://api.deepseek.com/v1",
    }
    return DEFAULTS.get(provider, "https://api.openai.com/v1")


# ─────────────────────────────────────────────────────────────────────────────
# Skill class
# ─────────────────────────────────────────────────────────────────────────────

class ImageVisionSkill:
    name = "image_vision"
    description = (
        "Analyze images using the LLM's vision capability. "
        "Supports general analysis, description, OCR text extraction, "
        "object/face/logo detection, and two-image comparison. "
        "Accepts base64, URL, or local file path as input."
    )
    actions = ["analyze", "describe", "ocr", "detect", "compare", "check_support"]

    # ── Injected by registry setup (see register() below) ─────────────────────
    _config_getter = None   # callable that returns the current LLMConfig

    async def execute(self, action: str, params: dict, confirmed: bool = False) -> SkillResult:
        try:
            if action == "check_support":
                return await self._check_support()
            elif action == "analyze":
                return await self._analyze(params)
            elif action == "describe":
                return await self._describe(params)
            elif action == "ocr":
                return await self._ocr(params)
            elif action == "detect":
                return await self._detect(params)
            elif action == "compare":
                return await self._compare(params)
            else:
                return SkillResult(False, None, error=f"Unknown action: '{action}'. Valid: {self.actions}")
        except (FileNotFoundError, ValueError) as e:
            return SkillResult(False, None, error=str(e))
        except RuntimeError as e:
            return SkillResult(False, None, error=f"Vision API error: {str(e)}")
        except Exception as e:
            logger.exception("image_vision.%s crashed", action)
            return SkillResult(False, None, error=f"{type(e).__name__}: {str(e)}")

    # ── Action implementations ────────────────────────────────────────────────

    async def _get_config(self):
        if self._config_getter:
            return self._config_getter()
        raise RuntimeError("ImageVisionSkill not properly registered — config_getter missing.")

    async def _analyze(self, params: dict) -> SkillResult:
        """General-purpose image analysis with a custom prompt."""
        prompt = params.get("prompt", "Analyze this image thoroughly and describe everything you observe.")
        b64, mime = _load_image(
            b64=params.get("image_b64"),
            url=params.get("image_url"),
            path=params.get("image_path"),
            mime=params.get("mime", "image/jpeg"),
        )
        config = await self._get_config()
        result = await _call_vision_llm(prompt, b64, mime, config)
        return SkillResult(True, {
            "action": "analyze",
            "model": config.model,
            "prompt": prompt,
            "analysis": result,
        })

    async def _describe(self, params: dict) -> SkillResult:
        """Plain-language image description."""
        b64, mime = _load_image(
            b64=params.get("image_b64"),
            url=params.get("image_url"),
            path=params.get("image_path"),
            mime=params.get("mime", "image/jpeg"),
        )
        config = await self._get_config()
        result = await _call_vision_llm(ACTION_PROMPTS["describe"], b64, mime, config)
        return SkillResult(True, {
            "action": "describe",
            "model": config.model,
            "description": result,
        })

    async def _ocr(self, params: dict) -> SkillResult:
        """Extract text from image via OCR prompt."""
        b64, mime = _load_image(
            b64=params.get("image_b64"),
            url=params.get("image_url"),
            path=params.get("image_path"),
            mime=params.get("mime", "image/jpeg"),
        )
        config = await self._get_config()
        result = await _call_vision_llm(ACTION_PROMPTS["ocr"], b64, mime, config)
        return SkillResult(True, {
            "action": "ocr",
            "model": config.model,
            "extracted_text": result,
        })

    async def _detect(self, params: dict) -> SkillResult:
        """Detect specific elements in an image."""
        target = params.get("target", "objects, faces, text, logos")
        prompt = ACTION_PROMPTS["detect"].format(target=target)
        b64, mime = _load_image(
            b64=params.get("image_b64"),
            url=params.get("image_url"),
            path=params.get("image_path"),
            mime=params.get("mime", "image/jpeg"),
        )
        config = await self._get_config()
        result = await _call_vision_llm(prompt, b64, mime, config)
        return SkillResult(True, {
            "action": "detect",
            "model": config.model,
            "target": target,
            "detections": result,
        })

    async def _compare(self, params: dict) -> SkillResult:
        """Compare two images."""
        # Image 1
        b64_1, mime_1 = _load_image(
            b64=params.get("image_b64"),
            url=params.get("image_url"),
            path=params.get("image_path"),
            mime=params.get("mime", "image/jpeg"),
        )
        # Image 2
        b64_2, mime_2 = _load_image(
            b64=params.get("image_b64_2"),
            url=params.get("image_url_2"),
            path=params.get("image_path_2"),
            mime=params.get("mime_2", "image/jpeg"),
        )
        config = await self._get_config()
        result = await _call_vision_llm(
            ACTION_PROMPTS["compare"], b64_1, mime_1, config, b64_2, mime_2
        )
        return SkillResult(True, {
            "action": "compare",
            "model": config.model,
            "comparison": result,
        })

    async def _check_support(self) -> SkillResult:
        """Return info about whether the current model supports vision."""
        config = await self._get_config()
        provider = config.provider
        model = config.model
        supported_models = VISION_MODELS.get(provider, [])
        is_supported = model in supported_models or (
            # Ollama: treat any llava-* or moondream variant as supported
            provider == "ollama" and any(v in model for v in ["llava", "moondream", "bakllava"])
        )
        return SkillResult(True, {
            "current_model": model,
            "provider": provider,
            "vision_supported": is_supported,
            "vision_capable_models": {p: m for p, m in VISION_MODELS.items()},
            "note": (
                "Vision is supported for this model." if is_supported
                else f"Model '{model}' may not support vision. Consider switching to one of the listed models."
            ),
        })


# ─────────────────────────────────────────────────────────────────────────────
# Registry hook
# ─────────────────────────────────────────────────────────────────────────────

def register(registry, config_getter=None):
    """
    Register the ImageVisionSkill.

    config_getter: a zero-argument callable that returns the current LLMConfig.
    Typically passed from main.py as:  lambda: current_config
    """
    skill = ImageVisionSkill()
    skill._config_getter = config_getter
    registry.register("image_vision", skill)
    logger.info("Registered skill: image_vision")
