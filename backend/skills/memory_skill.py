from core.skill_registry import SkillResult
from core.memory_manager import MemoryManager
class MemorySkill:
    """
    Builtin skill exposing conversation history persistence and manipulation
    directly to the Agent.
    """
    def __init__(self, manager: MemoryManager):
        self.manager = manager
        self.description = "Manage and retrieve user conversation history files."
        self.actions = ["retrieve", "clear", "list_users"]

    async def execute(self, action: str, params: dict, confirmed: bool = False) -> SkillResult:
        # Defaults to the agent's current user context if none is provided explicitly
        user_id = params.get("user_id", "default")
        
        if action == "retrieve":
            n = params.get("n", 10)
            history = self.manager.retrieve_last_n(user_id, n=n)
            return SkillResult(success=True, output={"user_id": user_id, "history": history})
            
        elif action == "clear":
            # Destructive actions can require confirmation gates
            if not confirmed:
                return SkillResult(
                    success=False,
                    output=None,
                    requires_confirm=True,
                    confirm_prompt=f"Are you sure you want to permanently clear the memory history for user '{user_id}'?"
                )
            existed = self.manager.clear(user_id)
            return SkillResult(success=True, output={"user_id": user_id, "cleared": existed})
            
        elif action == "list_users":
            users = self.manager.list_users()
            return SkillResult(success=True, output={"users": users})
            
        else:
            return SkillResult(success=False, output=None, error=f"Unknown action '{action}'")