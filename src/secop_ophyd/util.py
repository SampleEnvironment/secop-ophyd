from __future__ import annotations

import copy
import time
from abc import abstractmethod
from functools import reduce
from itertools import chain
from typing import List, Union

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

    @abstractmethod
    def make_numpy_dtype(self):
        """Connect to underlying hardware"""

    @abstractmethod
    def make_numpy_compatible_list(self, value):
        """make a make a list that is ready for numpy array import"""


def dt_factory(secop_dt: TupleOf | StructOf | ArrayOf) -> dt_NP:
    dt_class = secop_dt.__class__

    dt_Converters = {StructOf: StructNP, TupleOf: TupleNP, ArrayOf: ArrayNP}

    return dt_Converters[dt_class](secop_dt)


class StructNP(dt_NP):
    def __init__(self, struct_dt: StructOf) -> None:
        self.secop_dtype = struct_dt
        self.members = {}

        for key, member in struct_dt.members:
            if isinstance(member, (TupleOf, StructOf, ArrayOf)):
                self.members[key] = dt_factory(member)
            else:
                self.members[key] = member

    def make_numpy_dtype(self):
        pass

    def make_numpy_compatible_list(self, value):
        pass


class TupleNP(dt_NP):
    def __init__(self, tuple_dt: TupleOf) -> None:
        self.secop_dtype = tuple_dt
        self.members = []

        for member in tuple_dt.members:
            if isinstance(member, (TupleOf, StructOf, ArrayOf)):
                self.members.append(dt_factory(member))
            else:
                self.members.append(member)

    def make_numpy_dtype(self):
        pass

    def make_numpy_compatible_list(self, value):
        pass


class ArrayNP(dt_NP):
    def __init__(self, array_dt: ArrayOf) -> None:
        self.secop_dtype = array_dt

        self.shape = []
        self.root_dt = array_dt
        while isinstance(self.root_dt, ArrayOf):
            self.shape.append(self.root_dt.maxlen)
            self.root_dt = self.root_dt.members

    def make_numpy_dtype(self):
        pass

    def make_numpy_compatible_list(self, value):
        pass


class SECoPdtype:
    def __init__(self, datatype: DataType) -> None:
        self.dtype: DataType = datatype

        self.root_dtype = datatype
        self.shape = None

        if isinstance(datatype, ArrayOf):
            self.shape = []
            while isinstance(self.root_dtype, ArrayOf):
                self.shape.append(self.root_dtype.maxlen)
                root_dt = self.root_dtype.members

        self._is_composite = (
            (True,) if isinstance(root_dt, (StructOf, TupleOf)) else False
        )

        if self._is_composite:
            self.members = root_dt.members

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


class SECoPReading:
    def __init__(
        self,
        SECoPdtype: DataType = None,
        entry: CacheItem = None,
    ) -> None:
        if entry is None:
            self.timestamp: float = time.time()
            self.value = None
            self.readerror = None
            return

        self.secop_dt: DataType = SECoPdtype

        self.value = entry.value

        self.timestamp = entry.timestamp

        self.readerror = entry.readerror

    def get_reading(self) -> Reading:
        return {"value": self.value, "timestamp": self.timestamp}

    def get_value(self):
        return self.value

    def set_reading(self, value) -> None:
        self.value = value
        self.timestamp = time.time()
