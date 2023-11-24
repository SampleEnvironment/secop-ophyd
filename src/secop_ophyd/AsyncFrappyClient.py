import asyncio
import time
from typing import Any, TypeVar

from bluesky.protocols import Reading

from frappy.client import CacheItem, Logger, SecopClient

T = TypeVar("T")


class SECoPReading:
    def __init__(self, entry: CacheItem = None) -> None:
        if entry is None:
            self.timestamp: float
            self.value = None
            self.readerror = None
            return

        self.value = entry.value

        self.timestamp = entry.timestamp

        self.readerror = entry.readerror

    def get_reading(self) -> Reading:
        return {"value": self.value, "timestamp": self.timestamp}

    def get_value(self):
        return self.value

    def set_reading(self, value) -> None:
        self.value = value
        self.timestamp = time.time()


class AsyncFrappyClient:
    def __init__(self, host: str, port: str, loop) -> None:
        self.host: str = host
        self.port: str = port

        self.client: SecopClient = None

        self.loop = loop

        self.external: bool = False

        self.conn_timestamp: float

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

        await self.connect(3)

        return self

    async def connect(self, try_period=0):
        await asyncio.to_thread(self.client.connect, try_period)
        self.conn_timestamp = time.time()

    async def disconnect(self, shutdown=True):
        await asyncio.to_thread(self.client.disconnect, shutdown)

    async def getParameter(self, module, parameter, trycache=False):
        paramerter_reading = await asyncio.to_thread(
            self.client.getParameter, module, parameter, trycache
        )
        return SECoPReading(paramerter_reading)

    async def setParameter(self, module, parameter, value):
        paramerter_reading = await asyncio.to_thread(
            self.client.setParameter, module, parameter, value
        )
        return SECoPReading(paramerter_reading)

    async def execCommand(self, module, command, argument=None) -> tuple[Any, dict]:
        return await asyncio.to_thread(
            self.client.execCommand, module, command, argument
        )

    def register_callback(self, key, *args, **kwds):
        self.client.register_callback(key, *args, **kwds)

    def unregister_callback(self, key, *args, **kwds):
        self.client.unregister_callback(key, *args, **kwds)
