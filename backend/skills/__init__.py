"""
Skills package — auto-registers all 21 SecOps skills into a SkillRegistry.

Usage:
    from skill_registry import SkillRegistry
    from skills import load_all_skills

    registry = SkillRegistry()
    load_all_skills(registry)
    # registry now has all 21 skills ready
"""

from core.skill_registry import SkillRegistry, SkillResult

_SKILL_MODULES = [
    # NETWORK
    "skills.port_scanner",
    "skills.dns_lookup",
    "skills.whois_lookup",
    "skills.ssl_cert_inspector",
    "skills.http_header_analyzer",
    "skills.network_recon",
    "skills.dns_security",
    # THREAT
    "skills.cve_lookup",
    "skills.ip_reputation",
    "skills.hash_lookup",
    "skills.ioc_extractor",
    # ANALYSIS
    "skills.log_analyzer",
    "skills.vulnerability_scorer",
    "skills.vulnerability_assessment",
    "skills.web_app_scanner",
    "skills.api_security_audit",
    "skills.firewall_auditor",
    # CLOUD / CONTAINER
    "skills.cloud_posture",
    "skills.container_scanner",
    # AUTH
    "skills.password_audit",
    # UTILITY
    "skills.utility",
]


def load_all_skills(registry: SkillRegistry) -> None:
    """Import every skill module and call its register(registry) function."""
    import importlib
    for mod_name in _SKILL_MODULES:
        mod = importlib.import_module(mod_name)
        mod.register(registry)
