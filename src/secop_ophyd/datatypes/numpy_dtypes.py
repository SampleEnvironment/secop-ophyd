"""numpy dtypes for SECoP datatypes, one class per datatype."""

from __future__ import annotations

import warnings
from abc import ABC, abstractmethod
from typing import Any

import numpy as np
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
        return bool(value)


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
        return int(value)


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
        return float(value)


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
        return int(value)


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
        return int(value)


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
        return str(value)


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
            # ragged arrays with more than a single dimension are not supported
            self.shape.extend(self.members.shape)
            self.root_type = self.members.root_type
            self.members.shape = []
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
            if value is None:
                member_np = self.members.make_concrete_numpy_dtype(None)
                val_shape = []
            elif len(value) == 0:
                # frappy delivers arrays as tuples, so compare by length
                member_np = self.members.make_concrete_numpy_dtype(None)
                val_shape = [0]
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
