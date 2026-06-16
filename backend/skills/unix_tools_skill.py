from skill_registry import SkillResult
from core.skills.os_execution import OsExecutionSkill

class UnixToolsSkill:
    def __init__(self):
        # Leverages your pre-existing os executor underneath
        self.executor = OsExecutionSkill()

    async def execute(self, action: str, params: dict, confirmed: bool = False) -> SkillResult:
        # Example command routing for core utilities
        command = params.get("command")
        if not command:
            return SkillResult.fail("No 'command' argument provided to unix-tools.")

        # Pass context execution down to underlying system handler
        return await self.executor.execute(action="run", params={"cmd": command}, confirmed=confirmed)