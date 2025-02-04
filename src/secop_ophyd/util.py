from __future__ import annotations

import copy
import time
import warnings
from abc import ABC, abstractmethod
from enum import Enum
from functools import reduce
from itertools import chain
from typing import Any, List, Union

import numpy as np
from bluesky.protocols import Reading
from event_model import DataKey
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
from ophyd_async.core._utils import StrictEnum

SCALAR_DATATYPES = (
    IntRange,
    BoolType,
    FloatRange,
    ScaledInteger,
    StringType,
    EnumType,
)


class Role(Enum):
    USER = 0
    ADVANCED = 1
    EXPERT = 2


class Access(Enum):
    NO_ACCESS = 0
    READ = 1
    WRITE = 2


def get_access_level(role: Role, accessmode: str) -> Access:
    assert len(accessmode) == 3

    access_str = accessmode[role.value]

    match access_str:
        case "r":
            return Access.READ
        case "w":
            return Access.WRITE
        case "-":
            return Access.NO_ACCESS
        case _:
            raise Exception(f"unknown accces level: {accessmode[role.value]}")


class NestedRaggedArray(Exception):
    """The Datatype contains nested ragged arrays"""


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
        n = 1

        # using itertool.chain()
        # inserting K after every Nth number
        return list(
            chain(
                *[
                    (
                        [k] + self._dev_path[i : i + n]  # type: ignore
                        if len(self._dev_path[i : i + n]) == n
                        else self._dev_path[i : i + n]
                    )
                    for i in range(0, len(self._dev_path), n)
                ]
            )
        )

    def get_signal_name(self):
        # top level: signal name == Parameter name

        if self._dev_path == []:
            return self._accessible_name

        sig_postfix = self._dev_path[self._last_named_param :]

        if self._last_named_param is None:
            sig_postfix = [self._accessible_name] + sig_postfix  # type: ignore

        delim = "-"
        return delim.join(map(str, sig_postfix))

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


class DtypeNP(ABC):
    secop_dtype: DataType
    name: str | None
    array_element: bool = False
    max_depth: int = 0

    @abstractmethod
    def make_numpy_dtype(self) -> tuple:
        """Create Numpy Compatible structured Datatype"""

    @abstractmethod
    def make_concrete_numpy_dtype(self, value) -> tuple:
        """Create Numpy Compatible structured Datatype from a concrete data value"""

    @abstractmethod
    def make_numpy_compatible_list(self, value) -> Any:
        """make a make a list that is ready for numpy array import"""

    @abstractmethod
    def make_secop_compatible_object(self, value) -> Any:
        """make a make an SECoP Compatible Object"""


def dt_factory(
    secop_dt: DataType, name: str = "", array_element: bool = False
) -> DtypeNP:
    dt_class = secop_dt.__class__

    dt_converters = {
        StructOf: StructNP,
        TupleOf: TupleNP,
        ArrayOf: ArrayNP,
        IntRange: IntNP,
        FloatRange: FloatNP,
        ScaledInteger: ScaledIntNP,
        BLOBType: BLOBNP,
        BoolType: BoolNP,
        EnumType: EnumNP,
        StringType: StringNP,
    }

    return dt_converters[dt_class](secop_dt, name, array_element)  # type: ignore


STR_LEN_DEFAULT = 100


class BLOBNP(DtypeNP):
    def __init__(
        self, blob_dt: BLOBType, name: str = "", array_element: bool = False
    ) -> None:
        self.name: str = name
        self.secop_dtype: BLOBType = blob_dt
        self.array_element = array_element

    def make_numpy_dtype(self) -> tuple:
        return (self.name, "U" + str(self.secop_dtype.maxbytes))

    def make_concrete_numpy_dtype(self, value) -> tuple:
        return (self.name, "U" + str(self.secop_dtype.maxbytes))

    def make_numpy_compatible_list(self, value: str):
        return value

    def make_secop_compatible_object(self, value) -> Any:
        return value


class BoolNP(DtypeNP):
    def __init__(
        self, bool_dt: BoolType, name: str = "", array_element: bool = False
    ) -> None:
        self.name: str = name
        self.secop_dtype: BoolType = bool_dt
        self.array_element = array_element

    def make_numpy_dtype(self) -> tuple:
        return (self.name, "<b1")

    def make_concrete_numpy_dtype(self, value) -> tuple:
        return self.make_numpy_dtype()

    def make_numpy_compatible_list(self, value: bool):
        return value

    def make_secop_compatible_object(self, value) -> Any:
        return value


class EnumNP(DtypeNP):
    def __init__(
        self, enum_dt: EnumType, name: str = "", array_element: bool = False
    ) -> None:
        self.name: str = name
        self.secop_dtype: EnumType = enum_dt
        self.array_element = array_element

    def make_numpy_dtype(self) -> tuple:
        return (self.name, "<i8")

    def make_concrete_numpy_dtype(self, value) -> tuple:
        return self.make_numpy_dtype()

    def make_numpy_compatible_list(self, value: int):
        return value

    def make_secop_compatible_object(self, value) -> Any:
        return value


class FloatNP(DtypeNP):
    def __init__(
        self, float_dt: FloatRange, name: str = "", array_element: bool = False
    ) -> None:
        self.name: str = name
        self.secop_dtype: FloatRange = float_dt
        self.array_element = array_element

    def make_numpy_dtype(self) -> tuple:
        return (self.name, "<f8")

    def make_concrete_numpy_dtype(self, value) -> tuple:
        return self.make_numpy_dtype()

    def make_numpy_compatible_list(self, value: float):
        return value

    def make_secop_compatible_object(self, value) -> Any:
        return value


class IntNP(DtypeNP):
    def __init__(
        self, int_dt: IntRange, name: str = "", array_element: bool = False
    ) -> None:
        self.name: str = name
        self.secop_dtype: IntRange = int_dt
        self.array_element = array_element

    def make_numpy_dtype(self) -> tuple:
        return (self.name, "<i8")

    def make_concrete_numpy_dtype(self, value) -> tuple:
        return self.make_numpy_dtype()

    def make_numpy_compatible_list(self, value: int):
        return value

    def make_secop_compatible_object(self, value) -> Any:
        return value


class ScaledIntNP(DtypeNP):
    def __init__(
        self, scaled_int_dt: ScaledInteger, name: str = "", array_element: bool = False
    ) -> None:
        self.name: str = name
        self.secop_dtype: ScaledInteger = scaled_int_dt
        self.array_element = array_element

    def make_numpy_dtype(self) -> tuple:
        return (self.name, "<i8")

    def make_concrete_numpy_dtype(self, value) -> tuple:
        return self.make_numpy_dtype()

    def make_numpy_compatible_list(self, value: int):
        return value

    def make_secop_compatible_object(self, value) -> Any:
        return value


class StringNP(DtypeNP):
    def __init__(
        self, string_dt: StringType, name: str = "", array_element: bool = False
    ) -> None:
        self.name: str = name
        self.secop_dtype: StringType = string_dt
        self.array_element = array_element

        if string_dt.maxchars == 1 << 64:
            # warnings.warn(
            #    "maxchars was not set, default max char lenght is set to: "
            #    + str(STR_LEN_DEFAULT)
            # )
            self.strlen = STR_LEN_DEFAULT

        else:
            self.strlen = string_dt.maxchars

    def make_numpy_dtype(self) -> tuple:
        return (self.name, "<U" + str(self.strlen))

    def make_concrete_numpy_dtype(self, value) -> tuple:
        return (self.name, "<U" + str(self.strlen))

    def make_numpy_compatible_list(self, value: str):
        return value

    def make_secop_compatible_object(self, value) -> Any:
        return value


class StructNP(DtypeNP):
    def __init__(
        self, struct_dt: StructOf, name: str = "", array_element: bool = False
    ) -> None:
        self.name: str = name
        self.secop_dtype: StructOf = struct_dt
        self.array_element = array_element
        self.members: dict[str, DtypeNP] = {
            name: dt_factory(member, name, self.array_element)
            for (name, member) in struct_dt.members.items()
        }

        max_depth = [member.max_depth for member in self.members.values()]
        self.max_depth = 1 + max(max_depth)

    def make_numpy_dtype(self) -> tuple:
        dt_list = []
        for member in self.members.values():
            dt_list.append(member.make_numpy_dtype())

        return (self.name, dt_list)

    def make_concrete_numpy_dtype(self, value) -> tuple:
        dt_list = []
        for key, member in self.members.items():

            member_val = value[key]
            dt_list.append(member.make_concrete_numpy_dtype(member_val))

        return (self.name, dt_list)

    def make_numpy_compatible_list(self, value: dict):
        return tuple(
            [
                self.members[name].make_numpy_compatible_list(value[name])
                for name in self.members.keys()
            ]
        )

    def make_secop_compatible_object(self, value) -> Any:
        return {
            member: np_dtype.make_secop_compatible_object(value=value[member])
            for (member, np_dtype) in self.members.items()
        }


class TupleNP(DtypeNP):
    def __init__(
        self, tuple_dt: TupleOf, name: str = "", array_element: bool = False
    ) -> None:
        self.name: str = name
        self.secop_dtype = tuple_dt
        self.array_element = array_element
        self.members: list[DtypeNP] = [
            dt_factory(
                secop_dt=member, name="f" + str(idx), array_element=self.array_element
            )
            for idx, member in enumerate(tuple_dt.members)
        ]

        max_depth = [member.max_depth for member in self.members]
        self.max_depth = 1 + max(max_depth)

    def make_numpy_dtype(self) -> tuple:
        dt_list = []
        for member in self.members:
            dt_list.append(member.make_numpy_dtype())

        return (self.name, dt_list)

    def make_concrete_numpy_dtype(self, value) -> tuple:
        dt_list = []
        for index, member in enumerate(self.members):
            member_val = value[index]
            dt_list.append(member.make_concrete_numpy_dtype(member_val))

        return (self.name, dt_list)

    def make_numpy_compatible_list(self, value: tuple):
        return tuple(
            [
                self.members[idx].make_numpy_compatible_list(value[idx])
                for idx, member in enumerate(self.members)
            ]
        )

    def make_secop_compatible_object(self, value) -> Any:
        return tuple(
            [
                np_dtype.make_secop_compatible_object(value=value["f" + str(idx)])
                for idx, np_dtype in enumerate(self.members)
            ]
        )


class ArrayNP(DtypeNP):
    def __init__(
        self, array_dt: ArrayOf, name: str = "", array_element: bool = False
    ) -> None:
        self.name: str = name
        self.secop_dtype = array_dt
        self.maxlen = array_dt.maxlen
        self.minlen = array_dt.minlen
        self.array_element = array_element

        self.ragged: bool = self.minlen != self.maxlen

        self.shape = [self.maxlen]

        self.members: DtypeNP = dt_factory(array_dt.members, array_element=True)
        self.root_type: DtypeNP

        self.max_depth = self.members.max_depth

        if isinstance(self.members, ArrayNP):
            self.shape.extend(self.members.shape)
            self.root_type = self.members.root_type
            self.members.shape = []
            if self.members.ragged:
                pass
                # raise NestedRaggedArray(
                #    "ragged arrays with more than a single dimension are not supported"
                # )int

        else:
            self.root_type = self.members

        if self.array_element and self.ragged:
            warnings.warn(
                "ragged arrays inside of arrays of copmposite datatypes (struct/tuple)"
                "are not supported"
            )

    def get_root_np_str(self) -> str:
        dtype_list = self.root_type.make_numpy_dtype()
        return dtype_list[1]

    def make_numpy_dtype(self) -> tuple:
        if self.shape == []:
            return self.members.make_numpy_dtype()
        else:
            return (self.name, list(self.members.make_numpy_dtype()).pop(), self.shape)

    def make_concrete_numpy_dtype(self, value) -> tuple:

        if self.ragged is False:
            return (
                self.name,
                list(self.members.make_concrete_numpy_dtype(value)).pop(),
                self.shape,
            )
        else:
            if value == []:
                member_np = self.members.make_concrete_numpy_dtype(None)
                val_shape = [0]
            elif value is None:
                member_np = self.members.make_concrete_numpy_dtype(None)
                val_shape = []
            else:
                member_np = self.members.make_concrete_numpy_dtype(value[0])
                val_shape = [len(value)]

            if isinstance(self.members, ArrayNP):
                val_shape = val_shape + member_np[2]

            return (self.name, member_np[1], val_shape)

    def make_numpy_compatible_list(self, value: list):
        return [self.members.make_numpy_compatible_list(elem) for elem in value]

    def make_secop_compatible_object(self, value: np.ndarray) -> Any:
        return [
            self.members.make_secop_compatible_object(value[idx])
            for idx in range(0, self.maxlen)
        ]


class SECoPdtype:

    def __init__(self, datatype: DataType) -> None:
        self.raw_dtype: DataType = datatype

        # Describe Fields ------------------------

        self.dtype: str

        # The array-protocol typestring of the data-type object.
        self.dtype_str: str

        # String representation of the numpy structured array dtype
        self.dtype_descr: list

        # string representation of the original secop datatype
        self.secop_dtype_str = datatype.export_datatype()

        # Shape of Data
        self.shape = []

        self.np_datatype: Any

        # Describe Fields ------------------------

        self.numpy_dtype: np.dtype

        self.dtype_tree: DtypeNP

        self._is_composite: bool = False
        self._is_array: bool = False

        self.dtype_tree = dt_factory(datatype)

        self.max_depth: int = self.dtype_tree.max_depth

        if isinstance(self.dtype_tree, ArrayNP):
            self.shape = self.dtype_tree.shape
            self._is_array = True
            self._is_composite = (
                True
                if isinstance(self.dtype_tree.root_type, (StructNP, TupleNP))
                else False
            )

        if isinstance(self.dtype_tree, (TupleNP, StructNP)):
            self._is_composite = True

        # Composite Datatypes & Arrays of COmposite Datatypes
        if self._is_composite:
            dt = self.dtype_tree.make_numpy_dtype()

            # Top level elements are not named and shape is
            # already covered by the shape var
            dt = dt[1]

            self.numpy_dtype = np.dtype(dt)

            # all composite Dtypes are transported as numpy arrays
            self.dtype = "array"
            self.dtype_str = self.numpy_dtype.str
            self.dtype_descr = self.numpy_dtype.descr
            self.np_datatype = np.ndarray

        # Scalar atomic Datatypes and arrays of atomic dataypes
        else:
            if self._is_array:
                # root secop datatype that is contained in the array
                self.dtype = "array"
                self.np_datatype = np.ndarray

            # Primitive datatypes
            else:
                self.np_datatype = SECOP2DTYPE[datatype.__class__][0]
                self.dtype = SECOP2DTYPE[datatype.__class__][1]

    def get_datakey(self) -> DataKey:
        describe_dict: dict = {}
        # Composite Datatypes & Arrays of COmposite Datatypes
        if self._is_composite:
            describe_dict["dtype_str"] = self.dtype_str
            # describe_dict["dtype_numpy"] = self.dtype_descr
            describe_dict["dtype_descr"] = self.dtype_descr

        if isinstance(self.dtype_tree, ArrayNP):
            describe_dict["dtype_numpy"] = self.dtype_tree.get_root_np_str()

        describe_dict["dtype"] = self.dtype
        describe_dict["shape"] = self.shape
        describe_dict["SECOP_datainfo"] = self.secop_dtype_str

        return describe_dict

    def _secop2numpy_array(self, value) -> np.ndarray:
        np_list = self.dtype_tree.make_numpy_compatible_list(value)

        return np.array(np_list, dtype=self.numpy_dtype)

    def secop2val(self, reading_val) -> Any:
        if self._is_composite:
            return self._secop2numpy_array(reading_val)

        else:
            return reading_val

    def val2secop(self, input_val) -> Any:
        # TODO check input_Val for conformity with datatype
        # TODO check if it is already in SECoP Format

        if self._is_composite and isinstance(input_val, np.ndarray):
            return self.dtype_tree.make_secop_compatible_object(input_val)
        else:
            return self.raw_dtype.validate(input_val)

    def update_dtype(self, input_val):
        if self._is_composite:
            # Composite Datatypes & Arrays of Composite Datatypes

            dt = self.dtype_tree.make_concrete_numpy_dtype(input_val)

            # Top level elements are not named and shape is
            # already covered by the shape var
            inner_dt = dt[1]

            if isinstance(self.raw_dtype, ArrayOf):
                self.shape = dt[2]

            self.numpy_dtype = np.dtype(inner_dt)

            self.dtype_str = self.numpy_dtype.str
            self.dtype_descr = self.numpy_dtype.descr

            return

        if isinstance(self.raw_dtype, ArrayOf):
            dt = self.dtype_tree.make_concrete_numpy_dtype(input_val)

            self.shape = dt[2]


class SECoPReading:
    def __init__(
        self,
        secop_dt: SECoPdtype,
        entry: CacheItem | None = None,
    ) -> None:
        self.secop_dt: SECoPdtype = secop_dt

        if entry is None:
            self.timestamp: float = time.time()
            self.value = None
            self.readerror = None

            return

        if entry.readerror is not None:
            raise entry.readerror

        exported_val = secop_dt.raw_dtype.export_value(entry.value)

        self.secop_dt.update_dtype(exported_val)

        self.value = secop_dt.secop2val(exported_val)

        self.secop_val = exported_val

        self.timestamp = entry.timestamp

    def get_reading(self) -> Reading:
        return {"value": self.value, "timestamp": self.timestamp}

    def get_value(self):
        return self.value

    def get_secop_value(self):
        return self.secop_val

    def set_reading(self, value) -> None:
        self.value = value
        self.secop_val = self.secop_dt.val2secop(value)
        self.timestamp = time.time()


SECOP2DTYPE = {
    FloatRange: (float, "number"),
    IntRange: (int, "integer"),
    ScaledInteger: (int, "integer"),
    BoolType: (bool, "boolean"),
    EnumType: (StrictEnum, "string"),
    StringType: (str, "string"),
    BLOBType: (str, "string"),
}
