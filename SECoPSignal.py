from collections import OrderedDict, namedtuple
from frappy.client import SecopClient, CacheItem
from ophyd import Kind
from typing import Any, Dict, Generic, List, Optional, Type

from ophyd.v2.core import AsyncStatus,Signal,SignalR,SignalRW,T,Callback
from bluesky.protocols import Reading, Descriptor
import asyncio
import copy

def get_read_str(value,timestamp):
    return {"value":value,"timestamp":timestamp}

def get_shape(datainfo):
    #print(datainfo)
    SECoPdtype = datainfo.get('type',None)
    
    if   SECoPdtype.__eq__('array'):
        return [ 1, datainfo.get('maxlen',None)]
    elif SECoPdtype.__eq__('tuple'):
        memeberArr = datainfo.get('members',None)
        return [1, len(memeberArr)]
    else:
        return []


class _WithDatatype(Generic[T]):
    datatype: Type[T]

class SECoPSignalR(SignalR[T], _WithDatatype[T]):
    def __init__(self,path: tuple[str,str], prefix: str,secclient: SecopClient) -> None:

        
        # secclient 
        self._secclient = secclient
        
        
        # module:acessible Path for reading/writing (module,accessible)
 
        self._module = path[0]
        self._accessible = path[1]
        
        self._signal_desc : Dict[str,T] 
        self._datainfo : Dict[str,T]  

        self._signal_desc = self._get_signal_desc()        
        self._datainfo = self._signal_desc.pop('datainfo')
        self._datainfo['SECoPtype'] = self._datainfo.pop('type')
        
        #self._datainfo = param_desc.get('datainfo')
        #self.datainfo['SECoP_dtype'] = self.datainfo.pop('type')
        #self._monitor: Optional[Monitor] = None
        #self._valid = asyncio.Event()
        #self._value: Optional[T] = None
        #self._reading: Optional[Reading] = None
        #self._value_listeners: List[Callback[T]] = []
        
        #self._reading_listeners: List[Callback[Dict[str, Reading]]] = []
        
        self._staged = False
        
        self._prefix = prefix

    def _get_signal_desc(self):
        signal_desc = self._secclient.modules.get(self._module).get('accessibles').get(self._accessible)
        return copy.deepcopy(signal_desc)
        
    def _check_cached(self, cached: Optional[bool]) -> bool:
        if cached is None:
            cached = self._secclient.activate
        elif cached:
            assert self._secclient.activate, f"{self.source} not being monitored"
        return cached
    def _get_dtype(self) -> str:
        return SECOP2DTYPE.get(self._datainfo.get('SECoPtype'),None)          
    
    async def _read_signal(self,module:str,accessible:str,trycache:bool = False) ->tuple:
        read_val = self._secclient.getParameter(self._module,self._accessible,trycache =True)
        ts  = read_val.timestamp
        val = read_val.value
        
        if self._datainfo['SECoPtype'] == 'tuple':
            conv2list = list(val)  
            return (conv2list,ts)
        if self._datainfo['SECoPtype'] == 'enum':
            return (val.value,ts)
        #TODO array size
        if self._datainfo['SECoPtype'] == 'array':
            return (val,ts)
        
        return (val,ts)
        
            
            

    async def read(self,cached: Optional[bool] = None) -> Dict[str,Reading]:
        if self._check_cached(cached):
            val = await self._read_signal(self._module,self._accessible,trycache =True)
        else:
            #TODO async io SECoP Frappy Client  
            val =  await self._read_signal(self._module,self._accessible,trycache =False)        
        return get_read_str(value=val[0],timestamp=val[1])
        
       
    async def describe(self) -> Dict[str, Descriptor]:
        # get current Parameter description
        self._signal_desc = self._get_signal_desc()
        self._datainfo = self._signal_desc.pop('datainfo')        
        self._datainfo['SECoPtype'] = self._datainfo.pop('type')
        
        # convert SECoP datattype to a datatype Accepted by bluesky
        dtype = self._get_dtype()
        
        
        res  = {}
        
        res['source'] = self.source()
        res['dtype']  = dtype
        
        # get shape from datainfo and SECoPtype
        if self._datainfo['SECoPtype'] == 'tuple':
            res['shape'] = [1, len(self._datainfo.get('members'))]
        elif self._datainfo['SECoPtype'] == 'array':
            res['shape'] = [ 1,  self._datainfo.get('maxlen',None)]
        else:
            res['shape']  = []
         
        for property_name, prop_val in self._signal_desc.items():
            res[property_name] = prop_val
            
        for property_name, prop_val in self._datainfo.items():
            res[property_name] = prop_val
            

        
        return res

    

    def stage(self) -> List[Any]:
        """Start caching this signal"""
        return []


    def unstage(self) -> List[Any]:
        """Stop caching this signal"""
        return []

    async def get_value(self, cached: Optional[bool] = None) -> T:
        """The current value"""
        if self._check_cached(cached):
            val = self._read_signal(self._module,self._accessible,trycache =True)
        else:
            #TODO async io SECoP Frappy Client  
            val = await self._read_signal(self._module,self._accessible,trycache =False)        
        return val[0]    


    def subscribe_value(self, function: Callback[T]):
        """Subscribe to updates in value of a device"""
        pass

    def subscribe(self, function: Callback[Dict[str, Reading]]) -> None:
        """Subscribe to updates in the reading"""
        pass


    def clear_sub(self, function: Callback) -> None:
        """Remove a subscription."""
        pass

    def source(self) -> str:
        return self._prefix + self._accessible
    
    async def connect(self, prefix: str = "", sim=False):
        #TODO reconnect and exception handling
        if self._secclient.state == 'connected':
            return
        if self._secclient.state == 'disconnected':
            self._secclient.connect(1)
            return
    

class SECoPSignalRW(SECoPSignalR[T], SignalRW[T]):
    pass

class SECoPNodeProperty(SECoPSignalR):
    pass



    

class SECoPParameter:
    def __init__(self,prefix,name,module_name,param_desc,secclient : SecopClient,kind) -> None:
        self._secclient = secclient
        self.kind = kind
        self.name = name
        self.module_name = module_name
        
        self.datainfo = param_desc.get('datainfo')
        self.datainfo['SECoP_dtype'] = self.datainfo.pop('type')
        
        self.dtype  = None
        self.source = prefix+name
        self.shape  = []
        
    def describe(self):
        res  = {}
        
        res['source'] = self.source
        res['dtype']  = self.dtype
        res['shape']  = self.shape
        

        for property_name, prop_val in self.datainfo.items():
            res[property_name] = prop_val
            
        return res
    
    def read(self):
        val =  self._secclient.getParameter(self.module_name,self.name,trycache =True)        
        return get_read_str(value=val[0],timestamp=val[1])
    
class SECoPParameterDouble(SECoPParameter):
    def __init__(self, prefix, name, module_name, param_desc, secclient, kind) -> None:
        super().__init__(prefix, name, module_name, param_desc, secclient, kind)
        
        self.dtype = 'number'
            
    
    

class SECoPParameterInt(SECoPParameter):
    def __init__(self, prefix, name, module_name, param_desc, secclient, kind) -> None:
        super().__init__(prefix, name, module_name, param_desc, secclient, kind)

        self.dtype = 'number'

class SECoPParameterScaled(SECoPParameter):
    def __init__(self, prefix, name, module_name, param_desc, secclient, kind) -> None:
        super().__init__(prefix, name, module_name, param_desc, secclient, kind)
        
        self.dtype = 'number'

class SECoPParameterBool(SECoPParameter):
    def __init__(self, prefix, name, module_name, param_desc, secclient, kind) -> None:
        super().__init__(prefix, name, module_name, param_desc, secclient, kind)
        
        self.dtype = 'boolean'

class SECoPParameterEnum(SECoPParameter):
    def __init__(self, prefix, name, module_name, param_desc, secclient, kind) -> None:
        super().__init__(prefix, name, module_name, param_desc, secclient, kind)
        self.dtype = 'number'
        
    def read(self):
        val =  self._secclient.getParameter(self.module_name,self.name,trycache =True)        
        return get_read_str(value=val[0].value,timestamp=val[1])

class SECoPParameterString(SECoPParameter):
    def __init__(self, prefix, name, module_name, param_desc, secclient, kind) -> None:
        super().__init__(prefix, name, module_name, param_desc, secclient, kind)
        self.dtype = 'string'

class SECoPParameterBlob(SECoPParameter):
    def __init__(self, prefix, name, module_name, param_desc, secclient, kind) -> None:
        super().__init__(prefix, name, module_name, param_desc, secclient, kind)
        self.dtype = 'string'

#TODO: shape for now only for the first Dim, later maybe recursive??
class SECoPParameterArray(SECoPParameter):
    def __init__(self, prefix, name, module_name, param_desc, secclient, kind) -> None:
        super().__init__(prefix, name, module_name, param_desc, secclient, kind)
        self.dtype = 'array'
        self.shape = [ 1,  self.datainfo.get('maxlen',None)]
        
        

    
        


#TODO: status tuple 
class SECoPParameterTuple(SECoPParameter):
    def __init__(self, prefix, name, module_name, param_desc, secclient, kind) -> None:
        super().__init__(prefix, name, module_name, param_desc, secclient, kind)
        self.dtype = 'array'
        self.shape = [1, len(self.datainfo.get('members'))]
        
    def read(self):
        val =  self._secclient.getParameter(self.module_name,self.name,trycache =True)
        conv2list = list(val[0])  
        return get_read_str(value=conv2list,timestamp=val[1])
    
    
# TODO: is dtype = 'object' allowed???
class SECoPParameterStruct(SECoPParameter):
    def __init__(self, prefix, name, module_name, param_desc, secclient, kind) -> None:
        super().__init__(prefix, name, module_name, param_desc, secclient, kind)
        self.dtype = 'object'
    
PARAM_CLASS = {
#JSON-Transport Datatype : SECoP Datatype  
    'double' : SECoPParameterDouble,
    'int'    : SECoPParameterInt,
    'scaled' : SECoPParameterScaled,
    'bool'   : SECoPParameterBool,
    'enum'   : SECoPParameterEnum,
    'string' : SECoPParameterString,
    'blob'   : SECoPParameterBlob,
    'array'  : SECoPParameterArray,
    'tuple'  : SECoPParameterTuple,
    'struct' : SECoPParameterStruct
     
}

SECOP2DTYPE = {
    'double' : 'number',
    'int'    : 'number',
    'scaled' : 'number',
    'bool'   : 'boolean',
    'enum'   : 'number',
    'string' : 'string',
    'blob'   : 'string',
    'array'  : 'array',
    'tuple'  : 'array',
    'struct' : 'object'
}