from collections import OrderedDict, namedtuple

from bssecop.AsyncSecopClient import AsyncSecopClient
from ophyd import Kind
from typing import Any, Dict, Generic, List, Optional, Type

from ophyd.v2.core import AsyncStatus, Monitor, ReadingValueCallback,Signal,SignalW,SignalR,SignalRW,T,Callback,SignalBackend
from bluesky.protocols import Reading, Descriptor
import asyncio
import copy
import time
from frappy.client import CacheItem
import collections.abc
import traceback

from typing import Callable

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



class ParameterBackend(SignalBackend):
    def __init__(self,path:tuple[str,str],secclient:AsyncSecopClient) -> None:
        # secclient 
        self._secclient:AsyncSecopClient = secclient
               
        # module:acessible Path for reading/writing (module,accessible)
        self._module = path[0]
        self._parameter = path[1]
        

        self._signal_desc = self._get_signal_desc()        
        self._datainfo = self._signal_desc.get('datainfo')    

        self.datatype = self._get_dtype()
        
        self.source   = secclient.uri  + ":" +secclient.nodename + ":" + self._module + ":" +self._parameter

     
    async def connect(self):
        pass
    
    async def put(self, value: Any | None, wait=True, timeout=None):
        #TODO wait + timeout
        if self._signal_desc.get('readonly',None) != True:
            await self._secclient.setParameter(
                module = self._module,
                parameter= self._parameter,
                value=value)
            
        
    async def get_descriptor(self) ->  Descriptor:
        # get current Parameter description
        self._signal_desc = self._get_signal_desc()
        self._datainfo = self._signal_desc.get('datainfo')        
        
        res  = {}
        
        res['source'] = self.source
        
        # convert SECoP datattype to a datatype Accepted by bluesky
        res['dtype']  = self.datatype
        
        # get shape from datainfo and SECoPtype
        
        # SECoP tuples ar transmitted as JSON array
        if self._datainfo['type'] == 'tuple':
            res['shape'] = [1, len(self._datainfo.get('members'))]
        #TODO if array is ragged only first dimension is used otherwise parse the array
        elif self._datainfo['type'] == 'array':
            res['shape'] = [ 1,  self._datainfo.get('maxlen',None)]
        else:
            res['shape']  = []
         
        for property_name, prop_val in self._signal_desc.items():
            if property_name == 'datainfo' or property_name == 'datatype' :
                continue
            res[property_name] = prop_val
            
        for property_name, prop_val in self._datainfo.items():
            if property_name == 'type':
                property_name = 'SECoPtype' 
            res[property_name] = prop_val
            
        return {self._parameter:res}
        
    async def get_reading(self) -> Reading:
        dataset = await self._secclient.getParameter(self._module,self._parameter,trycache =False)
       
        return dataset.get_reading()
    
    async def get_value(self) -> T:
        dataset = await self._secclient.getParameter(self._module,self._parameter,trycache =False)
       
        return dataset.get_value
    
    def monitor_reading_value(self, callback: Callable[[Reading, Any], None]) -> Monitor:
            def updateItem(module,parameter,entry:CacheItem):
                value = entry.value
            
                reading ={parameter:{'value':value,'timestamp':entry.timestamp}}
                callback(reading=reading,value=value)
                
            self._secclient.register_callback((self._module,self._parameter),updateItem)
            return SECoPMonitor(callback= updateItem, backend= self)
            
    def _get_signal_desc(self):
        return self._secclient.modules.get(self._module).get('parameters').get(self._parameter)
    
    def _get_dtype(self) -> str:
        return SECOP2DTYPE.get(self._datainfo.get('type'),None) 
    
    

class PropertyBackend(SignalBackend):
    """A read/write/monitor backend for a Signals"""

    def __init__(self, prop_key:str, propertyDict:Dict[str,T]) -> None:
          # secclient 
          
        self._property_dict = propertyDict
        self._prop_key = prop_key
        self._datatype = self._get_datatype()
        
        #TODO full property path
        self.source = prop_key

    def _get_datatype(self) -> str:
        prop_val = self._property_dict[self._prop_key]
        
        if isinstance(prop_val,str):
            return 'string'
        if isinstance(prop_val,(int,float)):
            return'number'
        if isinstance(prop_val,collections.abc.Sequence):
            return 'array'
        if isinstance(prop_val,bool):
            return 'bool'
        
        raise Exception('unsupported datatype in Node Property: ' + str(prop_val.__class__.__name__) )


    async def connect(self):
        """Connect to underlying hardware"""
        pass

    async def put(self, value: Optional[T], wait=True, timeout=None):
        """Put a value to the PV, if wait then wait for completion for up to timeout"""
        #Properties are readonly
        pass

    async def get_descriptor(self) -> Descriptor:
        """Metadata like source, dtype, shape, precision, units"""
        description  = {}
        
        description['source'] = self.source()
        description['dtype']  = self._get_datatype()
        description['shape']  = []
               
        return {self._prop_key:description}


    async def get_reading(self) -> Reading:
        """The current value, timestamp and severity"""
        #TODO correct timestamp
        return get_read_str(self._property_dict[self._prop_key],timestamp=time.time())
        

    async def get_value(self) -> T:
        """The current value"""
        #TODO correct timestamp
        return self._property_dict[self._prop_key]

    def monitor_reading_value(self, callback: ReadingValueCallback[T]) -> Monitor:
        """Observe changes to the current value, timestamp and severity"""
        return Monitor()


class SECoPMonitor:
    def __init__(self,callback,backend:ParameterBackend) -> None:
        self._secclient:AsyncSecopClient = backend._secclient
        self._callback:Callable          = callback
        self._module:str                 = backend._module
        self._parameter:str              = backend._parameter
        
        
    def close(self):
        self._secclient.unregister_callback((self._module,self._parameter),self._callback)

class SECoPSignalR(SignalR[T]):
    def __init__(self,path: tuple[str,str],secclient: AsyncSecopClient) -> None:

        # secclient 
        self._secclient = secclient
        
        
        # module:acessible Path for reading/writing (module,accessible)
 
        self._module = path[0]
        self._parameter = path[1]
        
        self._signal_desc : Dict[str,T] 
        self._datainfo : Dict[str,T]  

        self._signal_desc = self._get_signal_desc()        
        self._datainfo  = self._signal_desc.get('datainfo')
        self._SECoPtype = self._datainfo.get('type')
        
        #self._datainfo = param_desc.get('datainfo')
        #self.datainfo['SECoP_dtype'] = self.datainfo.pop('type')
        self._monitor: bool = False
        #self._valid = asyncio.Event()
        #self._value: Optional[T] = None
        #self._reading: Optional[Reading] = None
        self._value_listeners: List[Callback[T]] = []
        self._reading_listeners: List[Callback[Dict[str, Reading]]] = []
        
        self._staged = False
        


    def _get_signal_desc(self):
        return self._secclient.modules.get(self._module).get('parameters').get(self._parameter)
         
        
    def _check_cached(self, cached: Optional[bool]) -> bool:
        if cached is None:
            cached = self._secclient.activate
        elif cached:
            assert self._secclient.activate, f"{self.source} not being monitored"
        return cached
    
    def _get_dtype(self) -> str:
        return SECOP2DTYPE.get(self._datainfo.get('type'),None)          
    

            
            

    async def read(self,cached: Optional[bool] = None) -> Dict[str,Reading]:
        trycache = self._check_cached(cached)
        
        reading = await self._secclient.getParameter(self._module,self._parameter,trycache =trycache)
        
        return {self._parameter:reading.get_reading()}
        
       
    async def describe(self) -> Dict[str, Descriptor]:
        # get current Parameter description
        self._signal_desc = self._get_signal_desc()
        self._datainfo = self._signal_desc.get('datainfo')        
        
        
        
        res  = {}
        
        res['source'] = self.source()
        
        # convert SECoP datattype to a datatype Accepted by bluesky
        res['dtype']  = self._get_dtype()
        
        # get shape from datainfo and SECoPtype
        if self._datainfo['type'] == 'tuple':
            res['shape'] = [1, len(self._datainfo.get('members'))]
        elif self._datainfo['type'] == 'array':
            res['shape'] = [ 1,  self._datainfo.get('maxlen',None)]
        else:
            res['shape']  = []
         
        for property_name, prop_val in self._signal_desc.items():
            if property_name == 'datainfo' or property_name == 'datatype' :
                continue
            res[property_name] = prop_val
            
        for property_name, prop_val in self._datainfo.items():
            if property_name == 'type':
                property_name = 'SECoPtype' 
            res[property_name] = prop_val
            

        
        return {self._parameter:res}

    

    def stage(self) -> List[Any]:
        """Start caching this signal"""
        self._staged = True
        self._monitor_if_needed()
        return [self]


    def unstage(self) -> List[Any]:
        """Stop caching this signal"""
        self._staged = False
        self._monitor_if_needed()
        return [self]

    async def get_value(self, cached: Optional[bool] = None) -> T:
        """The current value"""
        trycache = self._check_cached(cached)
        
        reading = await self._secclient.getParameter(self._module,self._parameter,trycache =trycache)  
        return reading.get_value()    

    def _callback(self,reading:Reading,value:T):
        for value_listener in self._value_listeners:
            async def coro_wrap_value_listener(value:T):
                return value_listener(value)
                
            asyncio.run_coroutine_threadsafe(coro_wrap_value_listener(value),self._secclient._ev_loop)
                
       
        for reading_listener in self._reading_listeners:
            async def coro_wrap_reading_listener(reading:Reading):
                return reading_listener(reading)
                
            asyncio.run_coroutine_threadsafe(coro_wrap_reading_listener(reading),self._secclient._ev_loop)

    def subscribe_value(self, function: Callback[T]):
        """Subscribe to updates in value of a device"""
        self._value_listeners.append(function)
        self._secclient.register_callback((self._module,self._parameter),self.updateItem)

        self._monitor_if_needed()
        
    def updateItem(self,module,parameter,entry:CacheItem):
            value = entry.value
            
            reading ={parameter:{'value':value,'timestamp':entry.timestamp}}
            self._callback(reading=reading,value=value)
             
    
    def subscribe(self, function: Callback[Dict[str, Reading]]) -> None:
        """Subscribe to updates in the reading"""
        self._reading_listeners.append(function)
        self._secclient.register_callback((self._module,self._parameter),self.updateItem)

        self._monitor_if_needed()
    def clear_sub(self, function: Callback) -> None:
        """Remove a subscription."""
        try:
            self._value_listeners.remove(function)
        except ValueError:
            self._reading_listeners.remove(function)
            
    def _monitor_if_needed(self) -> None:
        should_monitor = (
            self._value_listeners or self._reading_listeners or self._staged
        )
        if should_monitor and not self._monitor:
            # Start a monitor
            self._secclient.register_callback((self._module,self._parameter),self.updateItem)
            self._monitor = True
            
        elif self._monitor and not should_monitor:
            # Stop the monitor
            self._monitor = False
            
            self._secclient.unregister_callback((self._module,self._parameter),self.updateItem)





    def source(self) -> str:
        return self.name
    
    
    def connect(self, prefix: str = "", sim=False):
        pass 





class SECoPSignalRW(SECoPSignalR[T], SignalRW[T]):
    def __init__(self, path: tuple[str, str], secclient: AsyncSecopClient) -> None:
        super().__init__(path, secclient)
        
        if self._datainfo.get('readonly'):
            raise ReadonlyError
        
        
    #TODO wait???
    def set(self, value: T, wait=True) -> AsyncStatus:
        """Set the value and return a status saying when it's done"""
        return AsyncStatus(
            self._secclient.setParameter(
                module = self._module,
                parameter= self._parameter,
                value=value)
            )

class SECoPPropertySignal(SignalR[T]):
    def __init__(self, prop_key:str, propertyDict:Dict[str,T]) -> None:
          # secclient 
          
        self._property_dict = propertyDict
        self._prop_key = prop_key
        self._datatype = self._get_datatype()
                
        self._staged = False

    def _get_datatype(self) -> str:
        prop_val = self._property_dict[self._prop_key]
        
        if isinstance(prop_val,str):
            return 'string'
        if isinstance(prop_val,(int,float)):
            return'number'
        if isinstance(prop_val,collections.abc.Sequence):
            return 'array'
        if isinstance(prop_val,bool):
            return 'bool'
        
        raise Exception('unsupported datatype in Node Property: ' + str(prop_val.__class__.__name__) )
        
    async def read(self,cached: Optional[bool] = None) -> Dict[str,Reading]:
      
        return {self._prop_key:get_read_str(self._property_dict[self._prop_key],timestamp=time.time())}
        
       
    async def describe(self) -> Dict[str, Descriptor]:
              
        description  = {}
        
        description['source'] = self.source()
        description['dtype']  = self._get_datatype()
        description['shape']  = []
        


        
        return {self._prop_key:description}

    

    def stage(self) -> List[Any]:
        """Start caching this signal"""
        return []


    def unstage(self) -> List[Any]:
        """Stop caching this signal"""
        return []

    async def get_value(self, cached: Optional[bool] = None) -> T:
        """The current value"""
        return self._property_dict[self._prop_key]   


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
        return self.name
    
    def connect(self, prefix: str = "", sim=False):
        pass
    






class ReadonlyError(Exception):
    "Raised, when Secop parameter is readonly, but was used to construct rw ophyd Signal"
    pass
    


    def __init__(self, prefix, name, module_name, param_desc, secclient, kind) -> None:
        super().__init__(prefix, name, module_name, param_desc, secclient, kind)
        self.dtype = 'string'

#TODO: Assay: shape for now only for the first Dim, later maybe recursive??

#TODO: status tuple 

#TODO: is dtype = 'object' allowed???

    


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