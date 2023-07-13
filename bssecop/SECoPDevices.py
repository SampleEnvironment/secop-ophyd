

import threading

from ophyd.v2.core import  StandardReadable

from ophyd.v2.core import AsyncStatus, observe_value, Device,SignalRW, SignalR

from bluesky.protocols import Movable, Stoppable, SyncOrAsync
 
from typing import (
    Any,

    Dict,
    Generic,
    Iterable,
    List,
    Optional,
    Protocol,
    Sequence,
    Set,
    TypeVar,
    cast,
    runtime_checkable,
)

from bluesky.protocols import Movable
 
from bssecop.AsyncSecopClient import AsyncSecopClient
from frappy.logging import logger

from bssecop.SECoPSignal import *

from bssecop.propertykeys import * 

from bssecop.util import deep_get, Path

import re
import importlib
import asyncio


"""_summary_
    
    SECNode
    ||
    ||
    ||---dev1(readable)
    ||---dev2(drivable)
    ||---dev3(writable)
    ||---dev4(readable)
"""

## Predefined Status Codes
DISABLED = 0
IDLE = 100
STANDBY = 130
PREPARED = 150
WARN = 200
WARN_STANDBY = 230
WARN_PREPARED = 250
NSTABLE = 270  # not in SECoP standard (yet)
BUSY = 300
DISABLING = 310
INITIALIZING = 320
PREPARING = 340
STARTING = 360
RAMPING = 370
STABILIZING = 380
FINALIZING = 390
ERROR = 400
ERROR_STANDBY = 430
ERROR_PREPARED = 450
UNKNOWN = 401  # not in SECoP standard (yet)



def clean_identifier(anystring):
    return str(re.sub(r'\W+|^(?=\d)', '_', anystring))

def class_from_interface(mod_properties : dict):
        
    for interface_class in mod_properties.get(INTERFACE_CLASSES):
        try:
            return IF_CLASSES[interface_class]
        except KeyError:
            continue
    raise Exception("no compatible Interfaceclass found in: " + str(mod_properties.get(INTERFACE_CLASSES)))

def get_config_attrs(parameters):
    parameters_cfg = parameters.copy()
    parameters_cfg.pop("target", None)
    parameters_cfg.pop("value", None)
    return parameters_cfg







class SECoPReadableDevice(StandardReadable):

    def __init__(
        self,
        secclient: AsyncSecopClient,
        module_name: str
        ):   
    
        
        self._secclient = secclient
        self._module = module_name
        module_desc = secclient.modules[module_name]
        
        self.value:SignalR 
        self.status:SignalR 
        
        
        #list for config signals
        config = [] 
        #list for read signals
        read   = []
        
        # generate Signals from Module Properties
        for property in module_desc['properties']:
            propb = PropertyBackend(property,module_desc['properties'])
            setattr(self,property,SignalR(backend=propb))
            config.append(getattr(self,property))

        # generate Signals from Module parameters eiter r or rw
        for parameter, properties in module_desc['parameters'].items():
            
            # generate new root path
            param_path = Path(parameter_name=parameter,module_name=module_name)

            dtype = properties['datainfo']['type'] == 'tuple'


            match dtype:

                case 'tuple':
                    setattr(self,parameter + '_tuple',SECoP_Tuple_Device(path = param_path, secclient= secclient))
                case 'struct':
                    setattr(self,parameter + '_struct',SECoP_Struct_Device(path = param_path, secclient= secclient))
                case _:
                    ## Normal types
                    paramb = SECoP_Param_Backend(path=param_path,secclient=secclient)
                    
                    #construct signal
                    readonly = properties.get('readonly',None)
                    if readonly == True:
                        setattr(self,parameter,SignalR(paramb))
                    elif readonly == False:
                        setattr(self,parameter,SignalRW(paramb))
                    else:
                        raise Exception('Invalid SECoP Parameter, readonly property is mandatory, but was not found, or is not bool')


                    # In SECoP only the 'value' parameter is the primary read prameter, but
                    # if the value is a SECoP-tuple all elements belonging to the tuple are appended
                    # to the read list
                    if parameter == 'value':
                        read.append(getattr(self,parameter))

                    # target should only be set through the set method. And is not part of config
                    elif parameter != 'target':
                        config.append(getattr(self,parameter))

        
        
        #TODO Commands!!!
        self.set_readable_signals(read=read,config=config)

        super().__init__(name=module_name)
        
    def set_name(self, name: str = ""):
        #if name and not self._name:
        self._name = name
        for attr_name, attr in self.__dict__.items():
            # TODO: support lists and dicts of devices
            if isinstance(attr, Device):
                attr.set_name(f"{name}-{attr_name.rstrip('_')}")
                attr.parent = self

    
    async def configure(self, d: Dict[str, Any]) -> tuple[Dict[str, Any], Dict[str, Any]]:
        """Configure the device for something during a run

        This default implementation allows the user to change any of the
        `configuration_attrs`. Subclasses might override this to perform
        additional input validation, cleanup, etc.

        Parameters
        ----------
        d : dict
            The configuration dictionary. To specify the order that
            the changes should be made, use an OrderedDict.

        Returns
        -------
        (old, new) tuple of dictionaries
        Where old and new are pre- and post-configure configuration states.
        """
        old = await self.read_configuration()
        stat = []
        for key, val in d.items():
            if key not in self._configuration_signals:
                # a little extra checking for a more specific error msg
                raise ValueError(
                    "%s is not one of the "
                    "configuration_fields, so it cannot be "
                    "changed using configure" % key
                )
            stat.append(getattr(self,key).set(val))
            
        await asyncio.gather(*stat)
        new = self.read_configuration()
        return old, new


class SECoP_Tuple_Device(StandardReadable):
    def __init__(self,
        path: Path,
        secclient: AsyncSecopClient):
        
        self._path = path
        self._secclient:AsyncSecopClient = secclient

        dev_name:str = path.get_signal_name() + "_tuple"

       
        props = secclient.modules[path._module_name]['parameters'][path._parameter_name]
        
        datainfo = props[DATAINFO]
        

        #list for read signals
        read   = []
        



        for ix , member_info in enumerate(deep_get(datainfo,path.get_memberinfo_path())):
            #new path object for tuple member
            tuplemember_path = path.append(ix)
            attr_name = tuplemember_path.get_signal_name()

            match member_info['type']:
                
                case 'tuple':
                    setattr(self,attr_name + '_tuple',SECoP_Tuple_Device(path = tuplemember_path, secclient= secclient))
                case 'struct':
                    setattr(self,attr_name + '_struct',SECoP_Struct_Device(path = tuplemember_path, secclient= secclient))

                # atomic datatypes & arrays
                case _:
                
                    tparamb = SECoP_Param_Backend(
                        path=tuplemember_path,
                        client= secclient)
                    
                    
                    #construct signal
                    readonly = props.get('readonly',None)

                    
            
                    
                    if readonly == True:
                        setattr(self,attr_name,SignalR(tparamb))
                    elif readonly == False:
                        setattr(self,attr_name,SignalRW(tparamb))
                    else:
                        raise Exception('Invalid SECoP Parameter, readonly property is mandatory, but was not found, or is not bool')

                    read.append(getattr(self,attr_name))
        
            self.set_readable_signals(read=read)

        

        
        super().__init__(dev_name)

class SECoPWritableDevice(SECoPReadableDevice,Movable):
    """Fast settable device target"""
    pass
    

class SECoPMoveableDevice(SECoPWritableDevice,Movable,Stoppable):
    def __init__(self, secclient: AsyncSecopClient, module_name: str):
        super().__init__(secclient, module_name)
        self._success = True
        
    def set(self,new_target,timeout: Optional[float] = None) -> AsyncStatus:
        
        coro = asyncio.wait_for(self._move(new_target), timeout=timeout)
        return AsyncStatus(coro)
    
    async def _move(self,new_target):
        self._success = True
        await self.target.set(new_target,wait=False)
        async for current_stat in observe_value(self.status):
            v = current_stat[0].value
            
           
            #Error State or DISABLED
            if v >= ERROR or v < IDLE:
                self._success = False
                break
            
            #Module is in IDLE/WARN state
            if IDLE <= v  < BUSY:
                break
            
            #TODO other status transitions
        
        if not self._success:
            raise RuntimeError("Module was stopped")
        
    async def stop(self, success=True) -> SyncOrAsync[None]:
        self._success = success
        await self._secclient.execCommand(self._module,'stop')
        
class SECoP_Struct_Device(StandardReadable):
    def __init__(self,
        path:Path,
        secclient: AsyncSecopClient):

        dev_name:str = path.get_signal_name() + "_struct"

        self._secclient:AsyncSecopClient = secclient


        props = secclient.modules[path._module_name]['parameters'][path._parameter_name]
        
        datainfo = props[DATAINFO]
        

        #list for read signals
        read   = []

        
        

        for member_name, member_info  in deep_get(datainfo,path.get_memberinfo_path()).items():
            
            # new path object for tuple member
            struct_member_path = path.append(member_name)
            attr_name = struct_member_path.get_signal_name()

            match member_info['type']:
                
                case 'tuple':
                    setattr(self,attr_name + '_tuple',SECoP_Tuple_Device(path = struct_member_path, secclient= secclient))
                case 'struct':
                    setattr(self,attr_name + '_struct',SECoP_Struct_Device(path = struct_member_path, secclient= secclient))

                # atomic datatypes & arrays
                case _:
                
                    struct_param_backend = SECoP_Param_Backend(
                        path=struct_member_path,
                        client= secclient)
                    
                    
                    #construct signal
                    readonly = props.get('readonly',None)

                               
                    
                    if readonly == True:
                        setattr(self,attr_name,SignalR(struct_param_backend))
                    elif readonly == False:
                        setattr(self,attr_name,SignalRW(struct_param_backend))
                    else:
                        raise Exception('Invalid SECoP Parameter, readonly property is mandatory, but was not found, or is not bool')

                    read.append(getattr(self,member_name))
        

            self.set_readable_signals(read=read)

        



        super().__init__(name=dev_name)



    


class SECoP_Node_Device(StandardReadable):
    def __init__(self,secclient:AsyncSecopClient):   

        self._secclient:AsyncSecopClient = secclient
        
        self.modules :   Dict[str,T] = self._secclient.modules
        self.Devices : Dict[str,T] = {}
        
              
        #Name is set to sec-node equipment_id
        name = self._secclient.properties[EQUIPMENT_ID].replace('.','-')
        
        config = [] 
        
        for property in self._secclient.properties:
            propb = PropertyBackend(property,self._secclient.properties)
            setattr(self,property,SignalR(backend=propb))
            config.append(getattr(self,property))
    
        
        
        for module, module_desc in self._secclient.modules.items():
            SECoPDeviceClass = class_from_interface(module_desc['properties'])
            
            setattr(self,module,SECoPDeviceClass(self._secclient,module))
            

        self.set_readable_signals(config=config)

        super().__init__(name=name)
    

    @classmethod
    async def create(cls,
                    host:str,
                    port:str,
                    loop):
        
        #check if asyncio eventloop is running in the same thread
        if loop._thread_id == threading.current_thread().ident and loop.is_running():
            secclient = await AsyncSecopClient.create(host=host,port=port,loop = loop)
            return SECoP_Node_Device(secclient=secclient)
        else:
            raise Exception
    
    
    @classmethod
    def create_external_loop(cls,
                    host:str,
                    port:str,
                    loop):
        if loop._thread_id == threading.current_thread().ident and loop.is_running():
            raise Exception
        else:
            #Event loop is running in a different thread
            future = asyncio.run_coroutine_threadsafe(
                AsyncSecopClient.create(host=host,port=port,loop = loop),
                loop)
            
            #TODO checking if connect fails 
            return SECoP_Node_Device(future.result(2))
             
    
    async def disconnect(self):
        if self._secclient.loop._thread_id == threading.current_thread().ident and self._secclient.loop.is_running():
            await self._secclient.disconnect(True)
        else:
            raise Exception
    
    def disconnect_external(self):
        if self._secclient.loop._thread_id == threading.current_thread().ident and self._secclient.loop.is_running():
            raise Exception
        else:    
            future = asyncio.run_coroutine_threadsafe(
                self._secclient.disconnect(True),
                self._secclient.loop)
        
            future.result(2)    


  


    
IF_CLASSES = {
    'Drivable': SECoPMoveableDevice,
    'Writable': SECoPWritableDevice,
    'Readable': SECoPReadableDevice,
    'Module'  : SECoPReadableDevice
}



ALL_IF_CLASSES = set(IF_CLASSES.values())

# TODO
#FEATURES = {
#    'HasLimits': SecopHasLimits,
#    'HasOffset': SecopHasOffset,
#}