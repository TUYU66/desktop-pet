"""Authentication shared by internal device/configuration routes."""
import hmac
import json
import os
from aiohttp import web


def service_authorized(request, key_variable):
    key = os.environ.get(key_variable, '')
    # Missing configuration must never activate a public compatibility password.
    if not key.strip():
        raise web.HTTPServiceUnavailable(
            text=json.dumps({'code': 503, 'msg': '服务认证尚未配置'}, ensure_ascii=False),
            content_type='application/json',
        )
    supplied = request.headers.get('Service-Key', '')
    return hmac.compare_digest(supplied.encode('utf-8'), key.encode('utf-8'))


def device_service_authorized(request):
    return service_authorized(request, 'XIAOZHI_DEVICE_SERVICE_KEY')
