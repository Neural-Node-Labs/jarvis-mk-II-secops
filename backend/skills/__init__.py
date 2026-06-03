"""
Skills package — auto-loads all registered skill modules.

To add a new skill:
  1. Create skills/<your_skill>.py with a `register(registry)` hook.
  2. Append the dotted module path to _SKILL_MODULES below.
"""

import importlib
import logging

logger = logging.getLogger("skills")

# ---------------------------------------------------------------------------
# Canonical list of skill modules.  Order is preserved but not significant.
# ---------------------------------------------------------------------------
_SKILL_MODULES = [
    "skills.port_scanner",
    "skills.dns_lookup",
    "skills.whois_lookup",
    "skills.ssl_cert_inspector",
    "skills.http_header_analyzer",
    "skills.network_recon",
    "skills.dns_security",
    "skills.cve_lookup",
    "skills.ip_reputation",
    "skills.hash_lookup",
    "skills.ioc_extractor",
    "skills.log_analyzer",
    "skills.vulnerability_scorer",
    "skills.vulnerability_assessment",
    "skills.web_app_scanner",
    "skills.api_security_audit",
    "skills.firewall_auditor",
    "skills.password_audit",
    "skills.cloud_posture",
    "skills.container_scanner",
    "skills.utility",
    "skills.file_streamer",   # ← chunked large-file writer (anti-truncation)
    "skills.memory_skills",    # ← skills for interacting with user memory (read/write/search)
    "skills.multimodal_analyzer",
    "skills.image_vision_skill",
    "skills.folder_reader",    # ← folder tree reader + LLM analyzer
]


def load_all_skills(registry) -> None:
    """
    Dynamically import every module in _SKILL_MODULES and call its
    ``register(registry)`` hook.  Import errors are logged but do not
    abort the remaining modules.

    FIX-1: Failures are now logged at ERROR level with full exc_info so the
    traceback appears in structured logs, making silent import failures
    immediately visible rather than requiring active debugging.
    A final summary line lists every module that failed so operators know
    exactly which skills are unavailable without grepping through log lines.
    """
    failed: list[str] = []

    for module_path in _SKILL_MODULES:
        try:
            module = importlib.import_module(module_path)
            if hasattr(module, "register"):
                module.register(registry)
                logger.info("Loaded skill module: %s", module_path)
            else:
                logger.warning(
                    "Skill module '%s' has no register() hook — skipped.",
                    module_path,
                )
        except ImportError as exc:
            # FIX-1: log full traceback so the root cause (missing dep, typo,
            # circular import, etc.) is immediately visible in the log stream.
            logger.error(
                "Failed to import skill module '%s': %s",
                module_path, exc, exc_info=True,
            )
            failed.append(module_path)
        except Exception as exc:  # noqa: BLE001 — catch-all so one bad skill can't block others
            logger.error(
                "Unexpected error registering skill module '%s': %s",
                module_path, exc, exc_info=True,
            )
            failed.append(module_path)

    # FIX-1: emit a single summary so operators see all failures at a glance.
    if failed:
        logger.error(
            "load_all_skills completed with %d failure(s): %s",
            len(failed), ", ".join(failed),
        )
    else:
        logger.info(
            "load_all_skills completed — all %d modules loaded successfully.",
            len(_SKILL_MODULES),
        )