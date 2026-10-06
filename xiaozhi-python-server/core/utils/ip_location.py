"""Best-effort city lookup for the device's public IP, never the server's IP."""
import ipaddress
import requests


def get_ip_info(ip_addr, logger):
    from core.utils.cache.manager import cache_manager, CacheType

    try:
        address = ipaddress.ip_address(str(ip_addr or "").strip())
        if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
            address = address.ipv4_mapped
        if not address.is_global or address.is_multicast:
            return {}
    except ValueError:
        return {}
    ip = str(address)
    cache_key = "device_ip_v2:" + ip
    cached = cache_manager.get(CacheType.IP_INFO, cache_key)
    if cached is not None:
        return cached
    result = {}
    try:
        response = requests.get(
            "https://whois.pconline.com.cn/ipJson.jsp",
            params={"json": "true", "ip": ip}, timeout=(2, 3),
            allow_redirects=False,
        )
        if response.status_code == 200:
            data = response.json()
            city = data.get("city") if isinstance(data, dict) else None
            if isinstance(city, str) and city.strip() and city.strip() not in {"未知", "未知位置", "内网IP", "局域网", "保留地址"}:
                result = {"city": city.strip()}
    except (requests.RequestException, ValueError):
        logger.bind(tag=__name__).debug("设备 IP 城市定位暂不可用，将使用默认城市")
    cache_manager.set(CacheType.IP_INFO, cache_key, result, ttl=21600 if result else 60)
    return result
