"""复用已有Java API的版本、revision、事务、幂等和历史接口。"""


class JavaMemoryStore:
    async def revision(self, role_id):
        from config.manage_api_client import semantic_memory_revision
        return await semantic_memory_revision(role_id)

    async def snapshot(self, role_id):
        from config.manage_api_client import semantic_memory_snapshot
        return await semantic_memory_snapshot(role_id)

    async def commit(self, payload):
        from config.manage_api_client import semantic_memory_request, SemanticMemoryConflict
        from .models import CommitConflict
        # turnId对应数据库source_turn_id，即本次真实sourceMessageId，无须新增表字段。
        try:
            return await semantic_memory_request("commit", payload, strict=True)
        except SemanticMemoryConflict as exc:
            raise CommitConflict("commit_conflict") from exc

    async def history(self, role_id, memory_id):
        from config.manage_api_client import semantic_memory_history
        return await semantic_memory_history(role_id, memory_id)
