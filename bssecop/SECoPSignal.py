

from bssecop.AsyncSecopClient import AsyncSecopClient, SECoPReading

from typing import Any, Dict, Optional, Type

from ophyd.v2.core import  ReadingValueCallback, T,SignalBackend
from bluesky.protocols import Reading, Descriptor


import time
from frappy.client import CacheItem
import collections.abc

from functools import reduce


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



def deep_get(dictionary, keys, default=None)-> dict:
    return reduce(lambda d, key: d.get(key, default) if isinstance(d, dict) else default, keys, dictionary)


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
        await self._secclient.connect(2)
    
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
            
        return res
        
    async def get_reading(self) -> Reading:
        dataset = await self._secclient.getParameter(self._module,self._parameter,trycache =False)
       
        return dataset.get_reading()
    
    async def get_value(self) -> T:
        dataset = await self._secclient.getParameter(self._module,self._parameter,trycache =False)
       
        return dataset.get_value
    


    def set_callback(self, callback: Callable[[Reading, Any], None] | None) -> None:
            def updateItem(module,parameter,entry:CacheItem):
                           
                data =SECoPReading(entry)
                callback(reading=data.get_reading(),value=data.get_value())

            if callback != None:
                self._secclient.register_callback((self._module,self._parameter),updateItem)


            else:
                self._secclient.unregister_callback((self._module,self._parameter),updateItem)



    def _get_signal_desc(self):
        return self._secclient.modules.get(self._module).get('parameters').get(self._parameter)
    
    def _get_dtype(self) -> str:
        return SECOP2DTYPE.get(self._datainfo.get('type'),None) 
    
class TupleParamBackend(SignalBackend):
    def __init__(
            self,
            path:tuple[str,str,int],
            secclient:AsyncSecopClient) -> None:
        
        # secclient 
        self._secclient:AsyncSecopClient = secclient
               
        # module:acessible Path for reading/writing (module,accessible)
        self._module = path[0]
        self._parameter = path[1]
        self._tuple_member = path[2]

        self._param_desc = self._get_param_desc()        
        self._datainfo = self._param_desc['datainfo']    
        self._memberinfo = self._datainfo['members'][self._tuple_member]
            
        self.datatype = self._get_dtype()
        
        
        
        
        self.source   = secclient.uri  + ":" +secclient.nodename + ":" + self._module + ":" +self._parameter + ":" + "member_" + str(self._tuple_member)

     
    async def connect(self):
        pass
    
    async def put(self, value: Any | None, wait=True, timeout=None):
        #TODO wait + timeout
        if self._param_desc.get('readonly',None):
            return
        
        reading = await self._secclient.getParameter(
            module=self._module,
            parameter=self._parameter,
            trycache=True)
            
        currVal = list(reading.get_value)    
        
        currVal[self._tuple_member] = value
        
        newTuple = tuple(currVal)
        
        await self._secclient.setParameter(
            module = self._module,
            parameter= self._parameter,
            value = newTuple)
            
        
    async def get_descriptor(self) ->  Descriptor:
        # get current Parameter description
        self._param_desc = self._get_param_desc()
        self._datainfo = self._param_desc.get('datainfo')        
        
        res  = {}
        
        res['source'] = self.source
        
        # convert SECoP datattype to a datatype Accepted by bluesky
        res['dtype']  = self.datatype
        
        # get shape from datainfo and SECoPtype
        
        #TODO if array is ragged only first dimension is used otherwise parse the array
        if self._datainfo['type'] == 'array':
            res['shape'] = [ 1,  self._datainfo.get('maxlen',None)]
        else:
            res['shape']  = []
        
        for property_name, prop_val in self._param_desc.items():
            if property_name == 'datainfo' or property_name == 'datatype' :
                continue
            res[property_name] = prop_val
            
        for property_name, prop_val in self._memberinfo.items():
            if property_name == 'type':
                property_name = 'SECoPtype' 
            res[property_name] = prop_val
            
        return res
        
    async def get_reading(self) -> Reading:
        dataset = await self._secclient.getParameter(self._module,self._parameter,trycache =False)
       
        # select only the tuple member corresponding to the signal
        dataset.value = dataset.value[self._tuple_member]
        
        return dataset.get_reading()
    
    async def get_value(self) -> T:
        dataset = await self._secclient.getParameter(self._module,self._parameter,trycache =False)
        
        # select only the tuple member corresponding to the signal
        dataset.value = dataset.value[self._tuple_member]
        
        return dataset.get_value
    
    def set_callback(self, callback: Callable[[Reading, Any], None] | None) -> None:
            def updateItem(module,parameter,entry:CacheItem):
                           
                data =SECoPReading(entry)
                callback(reading=data.get_reading(),value=data.get_value())

            if callback != None:
                self._secclient.register_callback((self._module,self._parameter),updateItem)


            else:
                self._secclient.unregister_callback((self._module,self._parameter),updateItem)
            
    def _get_param_desc(self):
        return self._secclient.modules[self._module]['parameters'][self._parameter]
    
    def _get_dtype(self) -> str:
        SECoPdype = self._memberinfo['type']
        
        if SECoPdype == 'tuple':
            raise NotImplementedError("nested Tuples are not yet supported")
        if SECoPdype == 'struct':
            raise NotImplementedError("nested Structs are not yet supported")
        
        return SECOP2DTYPE.get(SECoPdype,None) 

class StructParamBackend(SignalBackend):
    pass

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
        
        description['source'] = str(self.source)
        description['dtype']  = self._get_datatype()
        description['shape']  = []
               
        return description


    async def get_reading(self) -> Reading:
        """The current value, timestamp and severity"""
        #TODO correct timestamp
        return get_read_str(self._property_dict[self._prop_key],timestamp=time.time())
        

    async def get_value(self) -> T:
        """The current value"""
        #TODO correct timestamp
        return self._property_dict[self._prop_key]
    
    def set_callback(self, callback: Callable[[Reading, Any], None] | None) -> None:
        pass

    










class ReadonlyError(Exception):
    "Raised, when Secop parameter is readonly, but was used to construct rw ophyd Signal"
    pass
    


    def __init__(self, prefix, name, module_name, param_desc, secclient, kind) -> None:
        super().__init__(prefix, name, module_name, param_desc, secclient, kind)
        self.dtype = 'string'

#TODO: Array: shape for now only for the first Dim, later maybe recursive??

#TODO: status tuple 



    
# Tuple and struct are handled in a special way. They are unfolded into subdevices

SECOP2DTYPE = {
    'double' : 'number',
    'int'    : 'number',
    'scaled' : 'number',
    'bool'   : 'boolean',
    'enum'   : 'number',
    'string' : 'string',
    'blob'   : 'string',
    'array'  : 'array',
    'tuple'  : 'string', # but variing types of array elements 
    'struct' : 'string'
}