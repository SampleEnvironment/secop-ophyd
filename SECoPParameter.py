from collections import OrderedDict, namedtuple
from ophyd import Kind

def get_read_str(value,timestamp):
    return {"value":value,"timestamp":timestamp}

def get_shape(datainfo):
    
    SECoPdtype = datainfo.get('type',None)
    
    if   SECoPdtype.__eq__('array'):
        return [ 1, datainfo.get('maxlen',None)]
    elif SECoPdtype.__eq__('tuple'):
        memeberArr = datainfo.get('members',None)
        return [1, memeberArr.len()]
    else:
        return []
    

class SECoPParameter:
    def __init__(self,prefix,name,module_name,param_desc,secclient,kind) -> None:
        self._secclient = secclient
        self.name = name
        self.module_name = module_name
        self.kind = kind
        self.datainfo = param_desc.pop('datainfo')
        
        self.SECoPdtype = self.datainfo.pop('type') 
        self.dtype  = JSON_DATATYPE[self.SECoPdtype]
        self.source = prefix+name
        self.shape  = get_shape(self.datainfo)
        
    def describe(self):
        res  = {}
        
        res['source'] = self.source
        res['dtype']  = self.dtype
        res['shape']  = self.shape
        
        for key , value in self.datainfo.items():
            res[key] = value
        
        for property_name, prop_val in self.datainfo.items():
            res[property_name] = prop_val
            
        return res
    
    def read(self):
        val =  self._secclient.getParameter(self.module_name,self.name,trycache =True)        
        return get_read_str(value=val[0],timestamp=val[1])
    
    
    
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
    'tuple'  : 'array',
    'struct' : 'object'
     
}