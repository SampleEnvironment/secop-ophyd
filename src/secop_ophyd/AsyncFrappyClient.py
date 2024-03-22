import asyncio
import time
from typing import Any, TypeVar

from frappy.client import CacheItem, Logger, SecopClient

T = TypeVar("T")


class AsyncFrappyClient:
    def __init__(self, host: str, port: str, loop) -> None:
        self.host: str = host
        self.port: str = port

        self.client: SecopClient = None

        self.loop = loop

        self.external: bool = False

        self.conn_timestamp: float

        self.log = None

    @property
    def state(self):
        return self.client.state

    @property
    def online(self):
        return self.client.online

    @property
    def modules(self):
        return self.client.modules

    @property
    def properties(self):
        return self.client.properties

    @property
    def uri(self):
        return self.client.uri

    @property
    def nodename(self):
        return self.client.nodename

    @classmethod
    async def create(cls, host, port, loop, log=Logger):
        self = AsyncFrappyClient(host=host, port=port, loop=loop)
        self.client = SecopClient(uri=host + ":" + port, log=log)

        self.log = self.client.log

        await self.connect(3)

        return self

    async def connect(self, try_period=0):
        await asyncio.to_thread(self.client.connect, try_period)
        self.conn_timestamp = time.time()

    async def disconnect(self, shutdown=True):
        await asyncio.to_thread(self.client.disconnect, shutdown)

    async def get_parameter(self, module, parameter, trycache=False) -> CacheItem:
        paramerter_reading = await asyncio.to_thread(
            self.client.getParameter, module, parameter, trycache
        )
        return paramerter_reading

    async def set_parameter(self, module, parameter, value) -> CacheItem:
        paramerter_reading = await asyncio.to_thread(
            self.client.setParameter, module, parameter, value
        )
        return paramerter_reading

    async def exec_command(self, module, command, argument=None) -> tuple[Any, dict]:
        return await asyncio.to_thread(
            self.client.execCommand, module, command, argument
        )

    def register_callback(self, key, *args, **kwds):
        self.client.register_callback(key, *args, **kwds)

    def unregister_callback(self, key, *args, **kwds):
        self.client.unregister_callback(key, *args, **kwds)
