"""Authentication shared by internal device/configuration routes."""
import hmac
import ipaddress
import os


def device_service_authorized(request):
    key = os.environ.get('XIAOZHI_DEVICE_SERVICE_KEY', '')
    if not key:
        try:
            address = ipaddress.ip_address(request.remote or '')
            if not (address.is_loopback or getattr(address, 'ipv4_mapped', None)
                    and address.ipv4_mapped.is_loopback):
                return False
        except ValueError:
            return False
    return hmac.compare_digest(request.headers.get('Service-Key', ''), key or 'xiaozhi-device')
