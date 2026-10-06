from abc import ABC, abstractmethod
from config.logger import setup_logging

TAG = __name__
logger = setup_logging()


class MemoryProviderBase(ABC):
    def __init__(self, config):
        self.config = config
        self.role_id = None
        self.session_id = None

    def set_llm(self, llm):
        self.llm = llm

    @abstractmethod
    async def save_memory(self, msgs, session_id=None, user_context=None):
        """Save a new memory for specific role and return memory ID"""
        print("this is base func", msgs)

    @abstractmethod
    async def query_memory(self, query: str) -> str:
        """Query memories for specific role based on similarity"""
        return "please implement query method"

    def init_memory(self, role_id, llm, session_id=None, **kwargs):
        self.role_id = role_id
        self.llm = llm
        if session_id:
            self.session_id = session_id
