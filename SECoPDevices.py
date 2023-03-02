from collections import OrderedDict, namedtuple

from ophyd.status import Status
from ophyd import Kind
from ophyd import BlueskyInterface

from ophyd.device import *


from frappy.client import SecopClient

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

def get_read_str(value,timestamp):
    return {"value":value,"timestamp":timestamp}
    
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
        self.read_attrs = read_attrs
        self.configuration_attrs = configuration_attrs
        self._secclient = secclient
        
        
    pass

class SECoPReadableDevice(SECoPDevice):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        
    
    def read(self) -> OrderedDictType[str, Dict[str, Any]]:
        res = OrderedDict()
       
        for var in self.read_attrs:
            val = self._secclient.getParameter(self.name,var,trycache =True)        
            res[var] =get_read_str(value=val[0],timestamp=val[1])
        
        return res
    
    def describe(self) -> OrderedDictType[str, Dict[str, Any]]:
        res = OrderedDict
        
        for var, meta in self.read_attrs.items():
            print(meta.get('datainfo'))     
        
    
    def read_configuration(self) -> OrderedDictType[str, Dict[str, Any]]:
        pass    
    
    def describe_configuration(self) -> OrderedDictType[str, Dict[str, Any]]:
        pass
    
    def configure(self, d: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
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
    
class SECoP_Node_Device(SECoPDevice):
    
    def __init__(self,uri) -> None:
      
        print(sys.path)
        
        
        self._secclient = conn = SecopClient(uri)
        conn.connect(5)
        
        self.equipment_Id = conn.properties[EQUIPMENT_ID]
        
        self.parent = None 
        self.name = '%s (%s)' % (self.equipment_Id, conn.uri)        
        self.prefix = None
        
        self.modules = conn.modules
        
        self.properties = conn.properties
        self.protocolVersion = conn.secop_version
        
        self.Devices = {}
        
        
        self.init_Devices_from_Description()
        
    
    def read_configuration(self) -> OrderedDictType[str, Dict[str, Any]]:
        res = OrderedDict()
        
        # TODO timestamp on node properties
        for property, value in self.properties.items():
            res[property] = get_read_str(value=value,timestamp=time.time)    
        return res
    
    def describe_configuration(self) -> OrderedDictType[str, Dict[str, Any]]:
       res = OrderedDict()
       
       # TODO shape...
       for property, value in self.properties.items():
            res[property] = {"source":self.equipment_Id,"dtype":value.__class__.__name__,"shape":[]}    
       return res
   
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
            module_properties = module_description.get('properties', {})
            module_parameters = module_description.get('parameters',{})
         
            cls = class_from_interface(module_properties)
            module_cfg = {}
            
            module_cfg["name"] = module
            module_cfg["secclient"] = self._secclient
            module_cfg["parent"] = self
        
            module_cfg["read_attrs"] = {"value" : module_parameters.get('value', {})}
            
            module_cfg["configuration_attrs"] = get_config_attrs(module_parameters)
            
           
            
            
            module_cfg["configuration_attrs"] =[]
            #TODO target
            #TODO kind
            #TODO Prefix

            setupInfo[prefix+module] = ('SECoPDevices',cls.__name__, module_cfg)
        
        # Initialize Device objects
        for devname, devcfg in setupInfo.items():  
                       
            devcls = getattr(importlib.import_module(devcfg[0]), devcfg[1])
            dev = devcls(**devcfg[2])
            
            print(dev.__class__.__name__)
            
            self.Devices[devcfg[2].get('name')] = dev
            
     


    
IF_CLASSES = {
    'Drivable': SECoPMoveableDevice,
    'Writable': SECoPWritableDevice,
    'Readable': SECoPReadableDevice,
    'Module': SECoPDevice,
}

JSON_DATATYPE = {
#JSON-Transport Datatype : SECoP Datatype  
    'double' : 'number',
    'int'    : 'number',
    'scaled' : 'number',  # TODO convert to double before handing off to bluesky??
    'bool'   : 'boolean',
    'enum'   : 'number',
    'string' : 'string',
    'blob'   : 'string',
    'array'  : 'array',
    'tuple'  : 'tuple',
    'struct' : 'object'
     
}

ALL_IF_CLASSES = set(IF_CLASSES.values())

# TODO
#FEATURES = {
#    'HasLimits': SecopHasLimits,
#    'HasOffset': SecopHasOffset,
#}