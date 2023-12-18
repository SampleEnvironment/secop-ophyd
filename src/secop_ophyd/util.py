from __future__ import annotations

import copy
import time
from abc import abstractmethod
from functools import reduce
from itertools import chain
from typing import List, Union,Any

import numpy as np
from bluesky.protocols import Reading
from frappy.client import CacheItem
from frappy.datatypes import (
    ArrayOf,
    BLOBType,
    BoolType,
    DataType,
    EnumType,
    FloatRange,
    IntRange,
    ScaledInteger,
    StringType,
    StructOf,
    TupleOf,
    CommandType
)

SCALAR_DATATYPES = (
    IntRange,
    BoolType,
    FloatRange,
    ScaledInteger,
    StringType,
    EnumType,
)


def deep_get(dictionary, keys, default=None) -> dict:
    def get_val(obj, key, default):
        if isinstance(obj, dict):
            return obj.get(key, default)
        if isinstance(obj, list):
            return obj[key]
        if isinstance(obj, tuple):
            return obj[key]
        return default

    return reduce(lambda d, key: get_val(d, key, default), keys, dictionary)


class Path:
    def __init__(self, parameter_name: str, module_name: str) -> None:
        self._accessible_name = parameter_name
        self._module_name = module_name
        self._last_named_param: int | None = None

        self._dev_path: List[Union[str, int]] = []

    # Path is extended
    def append(self, elem: Union[str, int]) -> Path:
        new_path = copy.deepcopy(self)

        if isinstance(elem, str):
            new_path._last_named_param = len(new_path._dev_path)

        new_path._dev_path.append(elem)

        return new_path

    def get_param_path(self):
        return {"module": self._module_name, "parameter": self._accessible_name}

    def get_path_tuple(self):
        return (self._module_name, self._accessible_name)

    # TODO typing of return value
    def get_memberinfo_path(self):
        # inserting K after every Nth number
        # using itertool.chain()

        # insert element
        k = "members"

        # insert after every other element
        N = 1

        # using itertool.chain()
        # inserting K after every Nth number
        return list(
            chain(
                *[
                    [k] + self._dev_path[i : i + N]
                    if len(self._dev_path[i : i + N]) == N
                    else self._dev_path[i : i + N]
                    for i in range(0, len(self._dev_path), N)
                ]
            )
        )

    def get_signal_name(self):
        # top level: signal name == Parameter name

        if self._dev_path == []:
            return self._accessible_name

        sig_name_postfix = self._dev_path[self._last_named_param :]

        if self._last_named_param is None:
            sig_name_postfix = [self._accessible_name] + sig_name_postfix

        delim = "-"
        return delim.join(map(str, sig_name_postfix))

    def get_param_desc_path(self):
        return [self._module_name, "parameters", self._accessible_name]

    def get_cmd_desc_path(self):
        return [self._module_name, "commands", self._accessible_name]

    def get_leaf(self):
        if self._dev_path == []:
            return None

        return self._dev_path[-1]

    def insert_val(self, dic: dict, new_val):
        if self._dev_path == []:
            return dic

        d = dic
        for key in self._dev_path[:-1]:
            if key in d:
                d = d[key]
            else:
                # wrong path
                raise Exception(
                    "path is incorrect " + str(key) + " is not in dict: " + str(dic)
                )
        # insert new value
        if self._dev_path[-1] in d:
            d[self._dev_path[-1]] = new_val
        else:
            # wrong path
            raise Exception(
                "path is incorrect " + str(key) + " is not in dict: " + str(dic)
            )
        return dic


def parseStructOf(dtype: StructOf) -> list[bool]:
    return [is_Scalar_or_ArrayOf_scalar(val) for val in dtype.members.values()]


def is_Scalar_or_ArrayOf_scalar(type: DataType):
    if isinstance(type, SCALAR_DATATYPES):
        return True
    elif isinstance(type, ArrayOf):
        return isinstance(type.members, SCALAR_DATATYPES)
    else:
        return False


SECOP2PY = {
    IntRange: int,
    FloatRange: float,
    StringType: "U100",
    BoolType: bool,
    BLOBType: "U100",
    ScaledInteger: int,
    EnumType: int,
}


def mklist(secop_value: dict | tuple, secop_dt: DataType) -> list:
    if isinstance(secop_dt, StructOf):
        ret_val = tuple(
            [
                mklist(secop_value[key], member_dt)
                for key, member_dt in secop_dt.members.items()
            ]
        )

    elif isinstance(secop_dt, TupleOf):
        ret_val = tuple(
            [
                mklist(secop_value[idx], member_dt)
                for idx, member_dt in enumerate(secop_dt.members)
            ]
        )

    else:
        ret_val = secop_value

    return ret_val


def mk_np_arr(JSON_arr, secop_dt: DataType):
    pass


class dt_NP:
    secop_dtype: DataType
    name:[str|None]

    @abstractmethod
    def make_numpy_dtype(self)-> tuple:
        """Create Numpy Compatible structured Datatype"""

    @abstractmethod
    def make_numpy_compatible_list(self, value):
        """make a make a list that is ready for numpy array import"""


def dt_factory(name:[str|None],secop_dt: DataType) -> dt_NP:
    dt_class = secop_dt.__class__

    dt_Converters = {StructOf: StructNP, TupleOf: TupleNP, ArrayOf: ArrayNP}

    return dt_Converters[dt_class](name,secop_dt)





STR_LEN_DEFAULT  = 1000


class BLOBNP(dt_NP):
    def __init__(self,name:[str|None],blob_dt: BLOBType) -> None:
        self.name:str = name
        self.secop_dtype = blob_dt
        
    def make_numpy_dtype(self)-> tuple:       
        return (self.name,'U' + str(self.secop_dtype.maxbytes))


    def make_numpy_compatible_list(self, value:str):

        return value

class BoolNP(dt_NP):
    def __init__(self,name:[str|None],bool_dt: BoolType) -> None:
        self.name:str = name
        self.secop_dtype = bool_dt

    def make_numpy_dtype(self)-> tuple:
        return (bool) if self.name is None else (self.name,bool)

    def make_numpy_compatible_list(self, value:bool):
        return value
 
class EnumNP(dt_NP):
    def __init__(self,name:[str|None],enum_dt: EnumType) -> None:
        self.name:str = name
        self.secop_dtype = enum_dt

    def make_numpy_dtype(self)-> tuple:        
        return  (int)  if self.name is None else (self.name, int)

    def make_numpy_compatible_list(self, value:int):
        return value

class FloatNP(dt_NP):
    def __init__(self,name:[str|None],float_dt: FloatRange) -> None:
        self.name:str = name
        self.secop_dtype = float_dt

    def make_numpy_dtype(self)-> tuple:
        return (float) if self.name is None else (self.name,float)


    def make_numpy_compatible_list(self, value:float):
        return value

class IntNP(dt_NP):
    def __init__(self,name:[str|None],int_dt: IntRange) -> None:
        self.name:str = name
        self.secop_dtype = int_dt

    def make_numpy_dtype(self)-> tuple:
        return (int) if self.name is None else (self.name,int)


    def make_numpy_compatible_list(self, value:int):
        return value

class ScaledIntNP(dt_NP):
    def __init__(self,name:[str|None],scaled_int_dt: ScaledInteger) -> None:
        self.name:str = name
        self.secop_dtype = scaled_int_dt

    def make_numpy_dtype(self)-> tuple:
        return (int) if self.name is None else (self.name,int)


    def make_numpy_compatible_list(self, value:int):
        return value

class StringNP(dt_NP):
    def __init__(self,name:[str|None],string_dt: StringType) -> None:
        self.name:str = name
        self.secop_dtype:StringType = string_dt

    def make_numpy_dtype(self)-> tuple:
        strlen = self.secop_dtype.maxchars 
        
        if self.secop_dtype.maxchars == 1 << 64:
            Warning('maxchars was not set, default max char lenght is set to: '+ str(STR_LEN_DEFAULT))
            strlen = STR_LEN_DEFAULT
        return ('U'+str(strlen)) if self.name is None else (self.name,'U'+str(strlen))


    def make_numpy_compatible_list(self, value:str):
        return value

class StructNP(dt_NP):
    def __init__(self,name:[str|None], struct_dt: StructOf) -> None:
        self.name:str = name
        self.secop_dtype:StructOf = struct_dt
        self.members: dict[str,dt_NP]= {name:dt_factory(name,member) for (name,member) in struct_dt.members.items()}

    def make_numpy_dtype(self)-> tuple:
        dt_list = []
        for  member in self.members.values():
            dt_list.append(member.make_numpy_dtype())

        return (dt_list) if self.name is None else (self.name,dt_list)

    def make_numpy_compatible_list(self, value:dict):
        return tuple([self.members[name].make_numpy_compatible_list(value[name]) for name in self.members.keys()])


class TupleNP(dt_NP):
    def __init__(self,name:[str|None], tuple_dt: TupleOf) -> None:
        self.name:str = name
        self.secop_dtype = tuple_dt
        self.members: list[dt_NP] = [dt_factory(None,member) for member in tuple_dt.members]

    def make_numpy_dtype(self)-> tuple:
        dt_list = []
        for  member in self.members:
            dt_list.append(member.make_numpy_dtype())

        return (dt_list) if self.name is None else (self.name,dt_list)

    def make_numpy_compatible_list(self, value:tuple):
        return tuple([self.members[idx].make_numpy_compatible_list(value[idx]) for idx,member in enumerate(self.members)])


class ArrayNP(dt_NP):
    def __init__(self,name:[str|None], array_dt: ArrayOf) -> None:
        self.name:str = name
        self.secop_dtype = array_dt

        self.shape = []
        self.root_dt:dt_NP
        self.root_secop_dt= array_dt
        self._is_composite:bool = False

        # get the root datatype that is stored in the array
        while isinstance(self.root_secop_dt, ArrayOf):
            self.shape.append(self.root_secop_dt.maxlen)
            self.root_secop_dt = self.root_secop_dt.members

        if isinstance(self.root_secop_dt,(TupleOf,StructOf)):            
            self._is_composite = True


        self.root_dt = dt_factory(name,self.root_secop_dt)

    def make_numpy_dtype(self)-> tuple:
        return list(self.root_dt.make_numpy_dtype()).append(self.shape)

    def make_numpy_compatible_list(self, value:list):
        pass


class SECoPdtype:
    def __init__(self, datatype: DataType) -> None:
        self.raw_dtype: DataType = datatype

        self.dtype:str

        # The array-protocol typestring of the data-type object.
        self.dtype_str:str
        # String representation of the numpy structured array dtype
        self.dtype_descr:str
        # string representation of the original secop datatype
        self.secop_dtype_str:str

        self.numpy_dtype:np.dtype
        
        self.shape = None

        self.dtype_tree:[dt_NP|None]= None

        self._is_composite:bool = False


        # Composite Datatypes & Arrays
        if isinstance(datatype, (TupleOf,StructOf,ArrayOf)):
            self.dtype_tree = dt_factory(datatype)
            

            if isinstance(self.dtype_tree,ArrayNP):
                self._is_composite = True if self.dtype_tree._is_composite else False
                self.shape = self.dtype_tree.shape

            self.numpy_dtype = np.dtype(self.dtype_tree.make_numpy_dtype())

            # all composite Dtypes are transported as numpy arrays 
            self.dtype = 'array'

            self.dtype_str = self.numpy_dtype.str
            self.dtype_descr = self.numpy_dtype.descr
                
        # Scalar atomic Datatypes
        else:
            self._is_composite = False
            self.shape = []
            self.dtype = SECOP2DTYPE.get(datatype)

    def _SECoP2NumpyArr(self, value) -> np.ndarray :
        np_list = self.dtype_tree.make_numpy_compatible_list(value)

        return np.array(np_list,dtype=self.numpy_dtype)
    

    def SECoP2Val(self,reading_val) -> Any:
        if self._is_composite:
            return reading_val
        
        else:
            return self._SECoP2NumpyArr(reading_val)
        


        


class SECoPReading:
    def __init__(
        self,
        secop_dt:SECoPdtype = None,
        entry: CacheItem = None,
    ) -> None:
        if entry is None:
            self.timestamp: float = time.time()
            self.value = None
            self.readerror = None
            return

        self.secop_dt: DataType = secop_dt

        self.value = secop_dt.SECoP2Val(entry.value)

        self.timestamp = entry.timestamp

        self.readerror = entry.readerror

    def get_reading(self) -> Reading:
        return {"value": self.value, "timestamp": self.timestamp}

    def get_value(self):
        return self.value

    def set_reading(self, value) -> None:
        self.value = value
        self.timestamp = time.time()



SECOP2DTYPE = {
    FloatRange: "number",
    IntRange: "number",
    ScaledInteger: "number",
    BoolType: "boolean",
    EnumType: "number",
    StringType: "string",
    BLOBType: "string",
}


def mkdt(self):
    dt_list = []
    if isinstance(secop_dt, StructOf):
        for key, secop_dtype in secop_dt.members.items():
            # Member is an array
            shape = []
            if isinstance(secop_dtype, ArrayOf):
                while isinstance(secop_dtype, ArrayOf):
                    shape.append(secop_dtype.maxlen)
                    secop_dtype = secop_dtype.members

            # Member is a Tuple or Struct --> recursively call mkdt
            if isinstance(secop_dtype, (StructOf, TupleOf)):
                dt_list.append((key, mkdt(secop_dtype), shape))
                continue

            if shape == []:
                dt_list.append((key, SECOP2PY[secop_dtype.__class__]))
            else:
                dt_list.append((key, SECOP2PY[secop_dtype.__class__], shape))

    if isinstance(secop_dt, TupleOf):
        for index, secop_dtype in enumerate(secop_dt.members):
            if isinstance(secop_dtype, (StructOf, TupleOf)):
                dt_list.append(("idx" + str(index), mkdt(secop_dtype)))
                continue

            dt_list.append(("idx" + str(index), SECOP2PY[secop_dtype.__class__]))

    return dt_list