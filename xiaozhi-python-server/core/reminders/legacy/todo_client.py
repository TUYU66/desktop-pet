# Retired todo feature: retained for historical reference; no active entry imports this module.
"""语音与网页任务使用同一管理服务；失败不宣称已保存。"""
import os
import httpx
from config.manage_api_client import _java_base_url


class OperationRejected(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code=code


async def call(method, path, device, body=None, params=None):
    headers={"Service-Key":os.environ.get("XIAOZHI_REMINDER_SERVICE_KEY") or "xiaozhi-reminders"}
    async with httpx.AsyncClient(timeout=5) as client:
        response=await client.request(method,_java_base_url()+"/xiaozhi/api/tasks/internal/"+path,
            headers=headers,params={"deviceId":device,**(params or {})} if method=="GET" else None,
            json={**(body or {}),"deviceId":device} if method!="GET" else None)
        response.raise_for_status()
        result=response.json()
        if isinstance(result,dict) and result.get("code") in (400,401,403,404,409):
            raise OperationRejected(result['code'],result.get('msg','任务操作未执行'))
        if not isinstance(result,dict) or result.get("code")!=0:
            raise ValueError("任务回执无效")
        return result["data"]


async def state(device, scope="all", offset=0, limit=200, match=None):
    params={"scope":scope,"offset":offset,"limit":limit}
    if match is not None: params['match']=match
    return await call("GET","state",device,params=params)


async def command(device, body): return await call("POST","commands",device,body)


async def item(device, task_id): return await call("GET","task",device,params={"id":task_id})
