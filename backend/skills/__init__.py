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
]


def load_all_skills(registry) -> None:
    """
    Dynamically import every module in _SKILL_MODULES and call its
    ``register(registry)`` hook.  Import errors are logged but do not
    abort the remaining modules.
    """
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
            logger.error("Failed to import skill module '%s': %s", module_path, exc)