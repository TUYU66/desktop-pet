"""隔离的语义协议存储替身；仅用于测试，不连接Java或真实记忆库。"""
import copy
import json


class MemoryFixture:
    def __init__(self, initial=()):
        self.rows = [{"id": i+1, "version": 1, "status": "active", "observedAt": "2026-09-15T10:00:00+08:00",
                      "memoryMode": "automatic", **copy.deepcopy(r)} for i, r in enumerate(initial)]
        self.revision = 0
        self.turns = {}
        self.history_rows = []
        self.operations = []

    async def snapshot(self, role):
        return {"revision": self.revision, "memories": copy.deepcopy(self.rows)}

    async def request(self, operation, payload):
        assert operation == "commit", "fixture只接受提交，不允许HTTP回退"
        if payload["turnId"] in self.turns:
            return self.turns[payload["turnId"]]
        assert payload["writeProtocolVersion"] == 4
        assert payload["expectedRevision"] == self.revision
        rows = copy.deepcopy(self.rows)
        history = []
        counts = dict(created=0, updated=0, deleted=0)
        for op in payload["operations"]:
            old = next((r for r in rows if r["id"] == op.get("targetId")), None)
            if op["action"] != "add":
                assert old and old["version"] == op["expectedVersion"]
            new = {**copy.deepcopy(op["memory"]), "id": old["id"] if old else max((r["id"] for r in rows), default=0)+1,
                   "version": old["version"]+1 if old else 1, "source": payload["source"], "observedAt": payload["observedAt"],
                   "status": "invalidated" if op["action"] == "delete" else "closed" if op["reason"] in {"completed", "cancelled"} else old.get("status", "active") if old else "active"}
            if new["status"] == "active" and new["category"] != "event":
                assert not any(r["id"] != new["id"] and r.get("status") == "active" and r["category"] == new["category"] and r["key"] == new["key"] for r in rows)
            if old:
                rows.remove(old)
            rows.append(new)
            counts[{"add":"created", "update":"updated", "delete":"deleted"}[op["action"]]] += 1
            history.append({"memoryId": new["id"], "reason": op["reason"], "oldJson": json.dumps(old, ensure_ascii=False) if old else None,
                            "newJson": json.dumps(new, ensure_ascii=False), "changedAt": payload["observedAt"]})
        self.rows = rows
        self.history_rows.extend(history)
        self.operations.extend(copy.deepcopy(payload["operations"]))
        self.revision += int(any(counts.values()))
        self.turns[payload["turnId"]] = counts
        return counts

    async def history(self, role, memory_id):
        return copy.deepcopy([r for r in reversed(self.history_rows) if r["memoryId"] == memory_id])
