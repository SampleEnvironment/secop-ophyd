from __future__ import annotations

import copy
import time
from abc import ABC, abstractmethod
from functools import reduce
from itertools import chain
from typing import Any, List, Union

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
                        [k] + self._dev_path[i : i + n]
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


class DtypeNP(ABC):
    secop_dtype: DataType
    name: str | None
    array_element: bool = False

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
        return (self.name, bool)

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
        return (self.name, int)

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
        return (self.name, float)

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
        return (self.name, int)

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
        return (self.name, int)

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
            Warning(
                "maxchars was not set, default max char lenght is set to: "
                + str(STR_LEN_DEFAULT)
            )
            self.strlen = STR_LEN_DEFAULT

        else:
            self.strlen = string_dt.maxchars

    def make_numpy_dtype(self) -> tuple:
        return (self.name, "U" + str(self.strlen))

    def make_concrete_numpy_dtype(self, value) -> tuple:
        return (self.name, "U" + str(self.strlen))

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
            dt_factory(member, array_element=self.array_element)
            for member in tuple_dt.members
        ]

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

        if isinstance(self.members, ArrayNP):
            self.shape.extend(self.members.shape)
            self.root_type = self.members.root_type
            self.members.shape = []
            if self.members.ragged:
                raise NestedRaggedArray(
                    "ragged arrays with more than a single dimension are not supported"
                )

        else:
            self.root_type = self.members

        if self.array_element and self.ragged:
            raise NestedRaggedArray(
                "ragged arrays inside of arrays of copmposite datatypes (struct/tuple) "
                "are not supported"
            )

    def make_numpy_dtype(self) -> tuple:
        if self.shape == []:
            return self.members.make_numpy_dtype()
        else:
            return (self.name, list(self.members.make_numpy_dtype()).pop(), self.shape)

    def make_concrete_numpy_dtype(self, value) -> tuple:

        if self.shape == []:
            return self.members.make_concrete_numpy_dtype(value)
        elif self.ragged is False:
            return (
                self.name,
                list(self.members.make_concrete_numpy_dtype(value)).pop(),
                self.shape,
            )
        else:
            return (
                self.name,
                list(self.members.make_concrete_numpy_dtype(value)).pop(),
                [len(value)],
            )

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

        # Describe Fields ------------------------

        self.numpy_dtype: np.dtype

        self.dtype_tree: DtypeNP

        self._is_composite: bool = False

        self.describe_dict: dict = {}

        self.dtype_tree = dt_factory(datatype)

        if isinstance(self.dtype_tree, ArrayNP):
            self.shape = self.dtype_tree.shape
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
            self.describe_dict["dtype_str"] = self.dtype_str

            self.dtype_descr = self.numpy_dtype.descr
            self.describe_dict["dtype_descr"] = self.dtype_descr

        # Scalar atomic Datatypes and arrays of atomic dataypes
        else:
            self._is_composite = False

            if isinstance(self.dtype_tree, ArrayNP):
                # root secop datatype that is contained in the array
                root_secop_dt = self.dtype_tree.root_type.secop_dtype
                self.dtype = SECOP2DTYPE[root_secop_dt.__class__]
            else:
                self.dtype = SECOP2DTYPE[datatype.__class__]

        self.describe_dict["dtype"] = self.dtype
        self.describe_dict["shape"] = self.shape
        self.describe_dict["SECOP_datainfo"] = self.secop_dtype_str

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
        if not self._is_composite:
            return

        # Composite Datatypes & Arrays of Composite Datatypes

        dt = self.dtype_tree.make_concrete_numpy_dtype(input_val)

        # Top level elements are not named and shape is
        # already covered by the shape var
        dt = dt[1]

        self.numpy_dtype = np.dtype(dt)

        self.dtype_str = self.numpy_dtype.str
        self.describe_dict["dtype_str"] = self.dtype_str

        self.dtype_descr = str(self.numpy_dtype.descr)
        self.describe_dict["dtype_descr"] = self.dtype_descr

        self.describe_dict["dtype"] = self.dtype
        self.describe_dict["shape"] = self.shape
        self.describe_dict["SECOP_datainfo"] = self.secop_dtype_str


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
    FloatRange: "number",
    IntRange: "number",
    ScaledInteger: "number",
    BoolType: "boolean",
    EnumType: "number",
    StringType: "string",
    BLOBType: "string",
}
