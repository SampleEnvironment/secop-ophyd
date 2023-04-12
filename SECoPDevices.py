from collections import OrderedDict, namedtuple

from ophyd.status import Status
from ophyd import Kind
from ophyd import BlueskyInterface

from ophyd.v2.core import StandardReadable, AsyncStatus, AsyncReadable

from bluesky.protocols import Movable, Stoppable
 
from typing import (
    Any,
    AsyncGenerator,
    Awaitable,
    Callable,
    Coroutine,
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

from bluesky.protocols import (
    Configurable,
    Descriptor,
    HasName,
    Movable,
    Readable,
    Reading,
    Stageable,
    Status,
    Subscribable,
)
 
from AsyncSecopClient import AsyncSecopClient
from frappy.logging import logger

from SECoPSignal import *

from propertykeys import * 

import time
import re
import importlib
import sys

"""_summary_
    
    SECNode
    ||
    ||
    ||---dev1(readable)
    ||---dev2(drivable)
    ||---dev3(writable)
    ||---dev4(readable)
"""

def clean_identifier(anystring):
    return str(re.sub(r'\W+|^(?=\d)', '_', anystring))

def class_from_interface(mod_properties):
        
    for interface_class in mod_properties.get(INTERFACE_CLASSES):
        try:
            return IF_CLASSES[interface_class]
        except KeyError:
            continue
    return SECoPDevice

def get_config_attrs(parameters):
    parameters_cfg = parameters.copy()
    parameters_cfg.pop("target", None)
    parameters_cfg.pop("value", None)
    return parameters_cfg





class SECoPDevice():
    def __init__(self,
                 prefix = "",
                 secclient = None,
                 *,
                 name,
                 kind = None,
                 read_attrs = None,
                 configuration_attrs = None,
                 parent = None,
                 **kwargs):
        
        self.name = name 
        self.parent = parent
        self.prefix = prefix
        self.kind = kind
        self.read_attrs = {}

        # read Attributes
        for attr_name, attr_desc in read_attrs.items():
            dtype = attr_desc.get('datainfo').get('type')
            self.read_attrs[attr_name] = PARAM_CLASS[dtype](
                name=attr_name,
                module_name=name,
                param_desc=attr_desc,
                secclient=secclient,
                prefix=prefix + name + '_',
                kind=Kind.hinted)    
            
            
        # configuration Attributes
        self.configuration_attrs = {}
        for attr_name, attr_desc in configuration_attrs.items():
            dtype = attr_desc.get('datainfo').get('type')
            self.configuration_attrs[attr_name] =  PARAM_CLASS[dtype](
                name=attr_name,
                module_name=name,
                param_desc=attr_desc,
                secclient=secclient,
                prefix=prefix + name + '_',
                kind=Kind.config)   
        
        self._secclient = secclient     
    

class SECoPReadableDevice(SECoPDevice):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        
    
    def read(self) -> Dict[str, Reading]:
        res = OrderedDict()
       
        for par_name, par in self.read_attrs.items():
            res[par_name] = par.read()
        
        return res
    
    def describe(self) -> Dict[str, Reading]:
        res = OrderedDict()
        
        for par_name, par in self.read_attrs.items():
            res[par_name] = par.describe()
        
        return res
        
    
    def read_configuration(self) -> Dict[str, Reading]:
        res = OrderedDict()
       
        for par_name, par in self.configuration_attrs.items():
            res[par_name] = par.read()
        
        return res
    
    def describe_configuration(self) -> Dict[str, Reading]:
        res = OrderedDict()
        for par_name, par in self.configuration_attrs.items():
            res[par_name] = par.describe()
        
        return res
    
    def configure(self, d: Dict[str, Any]) -> tuple[Dict[str, Any], Dict[str, Any]]:
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
        old = self.read_configuration()
        for key, val in d.items():
            if key not in self.configuration_attrs:
                # a little extra checking for a more specific error msg
                if key not in self.component_names:
                    raise ValueError("There is no signal named %s" % key)
                else:
                    raise ValueError(
                        "%s is not one of the "
                        "configuration_fields, so it cannot be "
                        "changed using configure" % key
                    )
            getattr(self, key).set(val).wait()
        new = self.read_configuration()
        return old, new
    
class SECoPWritableDevice(SECoPReadableDevice):
    pass

class SECoPMoveableDevice(SECoPReadableDevice):
    pass
    
class SECoP_Node_Device(StandardReadable):
    def __init__(
        self,
        uri: str, 
        #prefix: str, 
        #name: str = "", 
        #primary: Optional[SignalR] = None, 
        #read: Sequence[AsyncReadable] = ..., 
        #read_uncached: Sequence[SignalR] = ..., 
        #config: Sequence[AsyncReadable] = ...
        ):   
    
        
        self._secclient = conn = AsyncSecopClient(uri)
        conn.connect(5)
        
        self.equipment_Id = conn.properties[EQUIPMENT_ID]
        
        self.parent = None 
        name = '%s (%s)' % (self.equipment_Id, conn.uri)  
              
        prefix = self.equipment_Id + '_'
        
        self.modules = conn.modules
        
        self.properties = conn.properties
        self.protocolVersion = conn.secop_version
        
        self.Devices = {}
        
        
        self.init_Devices_from_Description()
        
        config = () #TODO add config signals ---> all node Properties
        
        super().__init__(prefix, name, config)
        
    

    

    def _get_prefix(self):
        if not self._secclient:
            return None
        equipment_name = clean_identifier(self.equipment_Id).lower()
        self.prefix = equipment_name
        return self.prefix
   
    def init_Devices_from_Description(self):
        
        prefix = self._get_prefix()
        setupInfo = {}
        
        # retrieve Object initialization Data for Devices from the Module Descriptions
        for module , module_description in self._secclient.modules.items():
            # Descriptive Data of Module:
            module_properties = module_description.get('properties', {})
            module_parameters = module_description.get('parameters',{})
         
            # Interfaceclass:
            cls = class_from_interface(module_properties)
            
            
            module_cfg = {}
            module_cfg["name"] = module
            module_cfg["secclient"] = self._secclient
            module_cfg["parent"] = self

            # Split into read attributes
            module_cfg["read_attrs"] = {"value" : module_parameters.get('value', {})}
            # and configuration attributes
            module_cfg["configuration_attrs"] = get_config_attrs(module_parameters)
            
            #TODO kind
            #TODO Prefix
            #TODO module properties

            setupInfo[module] = ('SECoPDevices',cls.__name__, module_cfg)
        
        # Initialize Device objects
        for devname, devcfg in setupInfo.items():  
                       
            devcls = getattr(importlib.import_module(devcfg[0]), devcfg[1])
            dev = devcls(**devcfg[2])
            
            print(devname)
            
            self.Devices[devname] = dev
            self.__setattr__(devname,dev)
            
     


    
IF_CLASSES = {
    'Drivable': SECoPMoveableDevice,
    'Writable': SECoPWritableDevice,
    'Readable': SECoPReadableDevice,
    'Module': SECoPDevice,
}




ALL_IF_CLASSES = set(IF_CLASSES.values())

# TODO
#FEATURES = {
#    'HasLimits': SecopHasLimits,
#    'HasOffset': SecopHasOffset,
#}