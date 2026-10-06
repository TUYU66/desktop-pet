"""Shared, read-only management-service health; never infer it from device pings."""
import asyncio
import os
import time

import httpx


class ServiceHealth:
    INTERVAL = 3
    MAX_AGE = 10

    def __init__(self, base_url=None, clock=time.monotonic):
        self.url = (base_url or os.environ.get("JAVA_SERVER_URL", "http://localhost:8000")).rstrip("/")
        self._clock = clock
        self._checked_at = None
        self._java = "checking"

    def snapshot(self):
        state = self._java
        if self._checked_at is not None and self._clock() - self._checked_at > self.MAX_AGE:
            state = "unknown"
        return {"java": state}

    async def check(self, client):
        try:
            # This endpoint doesn't read or initialize a user's configuration.
            response = await asyncio.wait_for(
                client.get(f"{self.url}/xiaozhi/api/health"), timeout=2.5
            )
            self._java = "online" if response.status_code == 200 and response.json() == {"status": "online"} else "offline"
        except (httpx.HTTPError, TimeoutError, ValueError):
            self._java = "offline"
        self._checked_at = self._clock()

    async def run(self):
        async with httpx.AsyncClient(timeout=2, follow_redirects=False) as client:
            while True:
                await self.check(client)
                await asyncio.sleep(self.INTERVAL)


def connection_services(conn):
    health = getattr(getattr(conn, "server", None), "service_health", None)
    return health.snapshot() if health is not None else {"java": "unknown"}
