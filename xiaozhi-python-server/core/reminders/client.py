"""提醒服务专用通道；不使用旧通用 Service-Key，不跨设备操作。"""
import os
import httpx
from config.manage_api_client import _java_base_url


class OperationRejected(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code=code


async def call(method, path, device, body=None):
    headers={"Service-Key":os.environ.get("XIAOZHI_REMINDER_SERVICE_KEY") or "xiaozhi-reminders"}
    async with httpx.AsyncClient(timeout=5) as client:
        url=_java_base_url()+"/xiaozhi/api/reminders/internal/"+path
        response=await client.request(method,url,headers=headers,
            params={"deviceId":device} if method=="GET" else None,
            json={**(body or {}),"deviceId":device} if method!="GET" else None)
        result=response.json()
        if isinstance(result,dict) and result.get('code') in (400,401,403,404,409):
            raise OperationRejected(result['code'],result.get('msg','提醒操作被拒绝'))
        response.raise_for_status()
        if not isinstance(result,dict) or result.get("code")!=0:
            raise ValueError("提醒保存或操作未确认，请刷新后重试")
        return result["data"]


async def state(device): return await call("GET","state",device)
async def catchup(device): return await call("GET","catchup",device)
async def command(device,body): return await call("POST","commands",device,body)
