from frappy.client import SecopClient, CacheItem

from frappy.client import Logger
import asyncio
from bluesky.protocols import Reading
from frappy.datatypes import TupleOf,ArrayOf,EnumType,StructOf

from typing import TypeVar

import time

T = TypeVar("T")

class SECoPReading():
    def __init__(self,entry:CacheItem = None) -> None:
        if entry == None:
            self.timestamp = None
            self.value = None
            self.readerror = None
            return


        if isinstance(entry.value,EnumType):
            self.value = entry.value.value
              

        else:
            self.value = entry.value 
            
        self.timestamp = entry.timestamp

        self.readerror = entry.readerror
        
        
    def get_reading(self) -> Reading:
        return {'value':self.value,'timestamp':self.timestamp}
    def get_value(self) -> T:
        return self.value

    def set_reading(self,value) -> None:
        self.value = value
        self.timestamp = time.time()



class AsyncFrappyClient():
    def __init__(self,host:str,port:str,loop) -> None:
        self.host:str = host
        self.port:str = port

        self.client:SecopClient = None

        self.loop = loop

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
    async def create(cls,host,port,loop,log = Logger):
        self = AsyncFrappyClient(host=host,port=port,loop=loop)
        self.client = SecopClient(uri=host + ":" + port,log=log)

        await self.connect(3)

        return self

    

    async def connect(self,try_period =  0):
        await asyncio.to_thread(self.client.connect,try_period)

    async def disconnect(self, shutdown=True):
        await asyncio.to_thread(self.client.disconnect,shutdown)

    async def getParameter(self, module, parameter, trycache=False):
        paramerter_reading = await asyncio.to_thread(self.client.getParameter,module,parameter,trycache)
        return SECoPReading(paramerter_reading)

    async def setParameter(self, module, parameter, value):
        paramerter_reading =  await asyncio.to_thread(self.client.setParameter,module,parameter,value)
        return SECoPReading(paramerter_reading)

    async def execCommand(self, module, command, argument=None) -> tuple[T,dict]:
        return await asyncio.to_thread(self.client.execCommand,module,command,argument)
    
    def register_callback(self, key, *args, **kwds):
        self.client.register_callback(key,*args,**kwds)


    def unregister_callback(self, key, *args, **kwds):
        self.client.unregister_callback(key,*args,**kwds)
