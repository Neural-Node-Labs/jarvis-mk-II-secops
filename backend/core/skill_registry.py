"""
Skill Registry - Loads, registers, and dispatches all agent skills.
Skills are black-box components with IN/OUT schemas per CBD methodology.
"""
from typing import Dict, Any, Optional
import logging

logger = logging.getLogger("skill_registry")


class SkillResult:
    def __init__(self, success: bool, output: Any, error: str = None, requires_confirm: bool = False, confirm_prompt: str = None):
        self.success = success
        self.output = output
        self.error = error
        self.requires_confirm = requires_confirm
        self.confirm_prompt = confirm_prompt

    def to_dict(self):
        return {
            "success": self.success,
            "output": self.output,
            "error": self.error,
            "requires_confirm": self.requires_confirm,
            "confirm_prompt": self.confirm_prompt,
        }


class SkillRegistry:
    def __init__(self):
        self._skills: Dict[str, Any] = {}

    def register(self, name: str, skill_instance):
        self._skills[name] = skill_instance
        logger.info(f"Registered skill: {name}")

    def get(self, name: str):
        return self._skills.get(name)

    def list_skills(self) -> list:
        result = []
        for name, skill in self._skills.items():
            result.append({
                "name": name,
                "description": getattr(skill, "description", ""),
                "actions": getattr(skill, "actions", []),
            })
        return result

    async def execute(self, skill_name: str, action: str, params: dict, confirmed: bool = False) -> SkillResult:
        skill = self._skills.get(skill_name)
        if not skill:
            return SkillResult(False, None, error=f"Skill '{skill_name}' not found.")
        try:
            return await skill.execute(action, params, confirmed=confirmed)
        except Exception as e:
            logger.error(f"Skill {skill_name}.{action} crashed: {e}", exc_info=True)
            return SkillResult(False, None, error=f"Skill execution error: {type(e).__name__}: {str(e)}")
