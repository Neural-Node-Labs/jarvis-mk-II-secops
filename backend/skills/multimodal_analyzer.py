"""
Skill for parsing and analyzing PDFs and image files via multimodal LLM pipelines.
"""
from __future__ import annotations
import base64
import mimetypes
from pathlib import Path
from typing import Any

from core.skill_registry import SkillResult
from skills._base import Skill

# Optional PDF text extraction – install PyPDF2 if needed
try:
    from PyPDF2 import PdfReader
    HAS_PDF_SUPPORT = True
except ImportError:
    HAS_PDF_SUPPORT = False


class MultimodalAnalyzerSkill(Skill):
    def __init__(self) -> None:
        super().__init__()
        self.mm_client = None  # Will be injected by the platform bootstrap layer

    def set_mm_client(self, client: Any) -> None:
        """Injects the multimodal client (e.g., a model that can accept images/text)."""
        self.mm_client = client

    @property
    def name(self) -> str:
        return "multimodal_analyzer"

    @property
    def description(self) -> str:
        return (
            "Extracts text, identifies visual features, and performs deep analysis "
            "on images or PDF documents using multimodal capabilities."
        )

    @property
    def usage(self) -> str:
        return "Expects structured dictionary parameters: {'file_path': str, 'prompt': str}"

    @property
    def actions(self) -> list[str]:
        return ["run"]

    def run(self, args: str, mm=None) -> SkillResult:
        # Legacy string interface – bypassed to prevent string argument splitting bugs
        return SkillResult(False, None, error="Use direct action routing parameters instead of string args.")

    async def execute(self, action: str, params: dict, confirmed: bool = False) -> SkillResult:
        """
        Direct router override that intercepts dictionary fields mapped from the LLM prompt.
        """
        if action != "run":
            return SkillResult(False, None, error=f"Unknown action '{action}' for skill '{self.name}'.")

        # Robust parameter parsing
        file_path_str = params.get("file_path") or params.get("file") or params.get("args")
        prompt_instruction = params.get("prompt")

        if not file_path_str or not prompt_instruction:
            return SkillResult(
                False,
                None,
                error="Missing required routing parameters. Provide 'file_path' and 'prompt'."
            )

        file_path = Path(str(file_path_str).strip())

        if not file_path.exists():
            return SkillResult(False, None, error=f"Target file not found at: {file_path}")

        mime_type, _ = mimetypes.guess_type(file_path)
        supported_types = ["application/pdf", "image/jpeg", "image/png", "image/webp"]

        if mime_type not in supported_types:
            return SkillResult(
                False,
                None,
                error=f"Unsupported file format ({mime_type}). Use standard PDFs or images."
            )

        if not self.mm_client:
            return SkillResult(
                False,
                None,
                error="Multimodal client not configured. Call set_mm_client() first."
            )

        try:
            # Read file bytes
            with open(file_path, "rb") as f:
                file_bytes = f.read()

            # --- Image Processing Pipeline ---
            if mime_type in ("image/jpeg", "image/png", "image/webp"):
                # Encode image as base64 data URL
                b64_image = base64.b64encode(file_bytes).decode("utf-8")
                image_data_url = f"data:{mime_type};base64,{b64_image}"

                # Call multimodal model with image
                response = await self.mm_client.generate_response(
                    prompt=prompt_instruction,
                    images=[image_data_url]
                )

            # --- PDF Processing Pipeline ---
            elif mime_type == "application/pdf":
                if not HAS_PDF_SUPPORT:
                    return SkillResult(
                        False, None,
                        error="PDF support requires PyPDF2. Install it to enable PDF analysis."
                    )
                # Extract text from all pages
                reader = PdfReader(file_path)
                full_text = "\n\n".join(page.extract_text() or "" for page in reader.pages)

                # Send extracted text to the backend client wrapper
                response = await self.mm_client.generate_response(
                    prompt=prompt_instruction,
                    pdf_text=full_text
                )

            else:
                response = "Unsupported format."

            return SkillResult(
                success=True,
                output={
                    "file_analyzed": str(file_path),
                    "mime_type": mime_type,
                    "analysis": response,
                }
            )

        except Exception as e:
            return SkillResult(
                False, None,
                error=f"Multimodal analysis error: {type(e).__name__}: {str(e)}"
            )


def register(registry) -> None:
    """Dynamically registers the instantiated skill into the runtime environment loop."""
    skill = MultimodalAnalyzerSkill()
    registry.register("multimodal_analyzer", skill)