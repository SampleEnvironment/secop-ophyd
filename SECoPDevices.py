from collections import OrderedDict, namedtuple

from ophyd.status import Status
from ophyd import Kind
from ophyd import BlueskyInterface

from ophyd.device import *


from frappy.client import SecopClient

from propertykeys import * 

import time

"""_summary_
    
    SECNode
    ||
    ||
    ||---dev1(readable)
    ||---dev2(drivable)
    ||---dev3(writable)
    ||---dev4(readable)
"""



class SECoPDevice(BlueskyInterface):
    pass

class SECoPReadableDevice(SECoPDevice):
    pass    
    
class SECoPWritableDevice(SECoPDevice):
    pass

class SECoPMoveableDevice(SECoPDevice):
    pass
    
class SECoP_Node_Device(SECoPDevice):
    
    def __init__(self,uri) -> None:
        
        self.parent = None
        
        self._secclient = conn = SecopClient(uri)
        conn.connect(5)
        
        self.equipment_Id = conn.properties[EQUIPMENT_ID]
         
        self.name = '%s (%s)' % (self.equipment_Id, conn.uri)
        
        
        
        self.modules = conn.modules
        
        self.properties = conn.properties
        self.protocolVersion = conn.secop_version
        
        self.Devices = {}
        
        
        self.Devices = self.init_Devices_from_Description()
        
    
    def read_configuration(self) -> OrderedDictType[str, Dict[str, Any]]:
        res = OrderedDict()
        
        # TODO timestamp on node properties
        for property, value in self.properties.items():
            res[property] = {"value":value,"timestamp":time.time()}    
        return res
    
    def describe_configuration(self) -> OrderedDictType[str, Dict[str, Any]]:
       res = OrderedDict()
       
       # TODO shape...
       for property, value in self.properties.items():
            res[property] = {"source":self.equipment_Id,"dtype":value.__class__.__name__,"shape":[]}    
       return res
   
    def init_Devices_from_Description(self):
        
        for module , module_description in self._secclient.modules.items():
            module_properties = module_description.get('properties', {})

        
            pass
    def class_from_interface(mod_properties)
        for interface_class in ()


    
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