"""Authentication at the transport boundary, before reading request bodies."""
from aiohttp import web
from core.utils.service_auth import device_service_authorized


@web.middleware
async def internal_auth(request, handler):
    path = request.path
    protected = path.startswith(('/xiaozhi/chat/', '/xiaozhi/memory/', '/xiaozhi/device/')) or path == '/xiaozhi/config/reload'
    if protected and not device_service_authorized(request):
        return web.json_response({'code': 401, 'msg': '服务认证失败'}, status=401)
    return await handler(request)
