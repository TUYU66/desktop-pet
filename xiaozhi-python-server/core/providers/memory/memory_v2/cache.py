"""Chat-only snapshot/history cache. Decision and Commit always use the real store."""
import asyncio
import copy
import time
from collections import OrderedDict
from weakref import WeakKeyDictionary
from .retriever import read_snapshot
from .models import StoreUnavailable


class RecallCache:
    def __init__(self, ttl=2):
        self.ttl = ttl
        self.snapshot_value = None
        self.checked = 0
        self.histories = OrderedDict()
        self.lock = asyncio.Lock()
        self.generation = 0

    def invalidate(self):
        self.generation += 1
        self.snapshot_value = None
        self.histories.clear()
        self.checked = 0

    async def snapshot(self, store, role, fresh=False):
        async with self.lock:
            generation = self.generation
            if self.snapshot_value is not None and not fresh and time.monotonic() - self.checked < self.ttl:
                return copy.deepcopy(self.snapshot_value)
            revision = await store.revision(role) if callable(getattr(store, 'revision', None)) else None
            if self.snapshot_value is None or revision is None or revision != self.snapshot_value['revision']:
                snapshot = await store.snapshot(role)
                read_snapshot(snapshot)  # fail closed; never cache an unavailable/invalid snapshot
                if generation != self.generation:
                    raise StoreUnavailable('cache_changed_during_read')
                self.snapshot_value = copy.deepcopy(snapshot)
                self.histories.clear()
            if generation != self.generation:
                raise StoreUnavailable('cache_changed_during_read')
            self.checked = time.monotonic()
            return copy.deepcopy(self.snapshot_value)

    def view(self, store, snapshot):
        cache = self
        revision = snapshot['revision']
        generation = self.generation
        class View:
            async def snapshot(self, role):
                return copy.deepcopy(snapshot)

            async def history(self, role, memory_id):
                key = (revision, memory_id)
                if key in cache.histories:
                    return copy.deepcopy(cache.histories[key])
                history = await store.history(role, memory_id)
                if history is not None and cache.generation == generation:
                    cache.histories[key] = copy.deepcopy(history)
                    while len(cache.histories) > 256:
                        cache.histories.popitem(last=False)
                return history
        return View()


_caches = WeakKeyDictionary()


def get_cache(scope):
    caches = _caches.setdefault(asyncio.get_running_loop(), {})
    return caches.setdefault(scope, RecallCache())
