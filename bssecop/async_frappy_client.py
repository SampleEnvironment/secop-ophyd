from frappy.client import SecopClient

from frappy.client import Logger
import asyncio





class AsnyncFrappyClient():
    def __init__(self,host:str,port:str,loop, log=Logger) -> None:
        self.host:str = host
        self.port:str = port

        self.client:SecopClient = None

        self.loop = loop

    @classmethod
    async def create(cls,host,port,loop,log = Logger):
        self = AsnyncFrappyClient(host=host,port=port,log=log,loop=loop)
        self.client = SecopClient(uri=host + ":" + port,log=log)

        await self.connect(1)

        

    

    async def connect(self,try_period =  0):
        await asyncio.to_thread(self.client.connect,1)

    async def disconnect(self, shutdown=True):
        await asyncio.to_thread(self.client.disconnect(shutdown))

    async def getParameter(self, module, parameter, trycache=False):
        return await asyncio.to_thread(self.client.getParameter,module,parameter,trycache)

    async def setParameter(self, module, parameter, value):
        return await asyncio.to_thread(self.client.setParameter,module,parameter,value)

    async def execCommand(self, module, command, argument=None):
        return await asyncio.to_thread(self.client.execCommand,module,command,argument)

