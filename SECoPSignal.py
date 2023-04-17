from collections import OrderedDict, namedtuple

from AsyncSecopClient import AsyncSecopClient
from ophyd import Kind
from typing import Any, Dict, Generic, List, Optional, Type

from ophyd.v2.core import AsyncStatus,Signal,SignalW,SignalR,SignalRW,T,Callback
from bluesky.protocols import Reading, Descriptor
import asyncio
import copy
import time
from frappy.client import CacheItem
import collections.abc
import traceback

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
        self._datainfo = self._signal_desc.get('datainfo')
        #self._datainfo['SECoPtype'] = self._datainfo.pop('type')
        
        #self._datainfo = param_desc.get('datainfo')
        #self.datainfo['SECoP_dtype'] = self.datainfo.pop('type')
        #self._monitor: Optional[Monitor] = None
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
    
    async def _read_signal(self,module:str,accessible:str,trycache:bool = False) ->tuple:
        read_val =  await self._secclient.getParameter(self._module,self._parameter,trycache =True)
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
            #cahed read
            val = await self._read_signal(self._module,self._parameter,trycache =True)
        else: 
            #non cached read from sec-node          
            val =  await self._read_signal(self._module,self._parameter,trycache =False)        
        return get_read_str(value=val[0],timestamp=val[1])
        
       
    async def describe(self) -> Dict[str, Descriptor]:
        # get current Parameter description
        self._signal_desc = self._get_signal_desc()
        self._datainfo = self._signal_desc.pop('datainfo')        
        self._datainfo['SECoPtype'] = self._datainfo.pop('type')
        
        
    
        
        
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
            if property_name == 'datainfo':
                continue
            res[property_name] = prop_val
            
        for property_name, prop_val in self._datainfo.items():
            if property_name == 'type':
                property_name = 'SECoPtype' 
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
            val = self._read_signal(self._module,self._parameter,trycache =True)
        else:
            #TODO async io SECoP Frappy Client  
            val = await self._read_signal(self._module,self._parameter,trycache =False)        
        return val[0]    

    def _callback(self,reading:Reading,value:T):
        for value_listener in self._value_listeners:
            try:
                async def coro_wrap(value):
                    return value_listener(value)
                
                asyncio.run_coroutine_threadsafe(coro_wrap(value),self._secclient._ev_loop)
                #value_listener(value)
            except Exception as e:
                traceback.print_exc()
                
                print("insert into queue failed")
        #for reading_listener in self._reading_listeners:
        #    reading_listener({self.name:reading})

    def subscribe_value(self, function: Callback[T]):
        """Subscribe to updates in value of a device"""
        self._value_listeners.append(function)
        
        
        self._secclient.register_callback((self._module,self._parameter),self.updateItem)
    
    def updateItem(self,module,parameter,entry:CacheItem):
            value = entry.value
            
            reading ={parameter:{'value':value,'timestamp':entry.timestamp}}
            self._callback(reading=reading,value=value)
             
    
    def subscribe(self, function: Callback[Dict[str, Reading]]) -> None:
        """Subscribe to updates in the reading"""
        pass
        

    def clear_sub(self, function: Callback) -> None:
        """Remove a subscription."""
        try:
            self._value_listeners.remove(function)
        except ValueError:
            self._reading_listeners.remove(function)


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