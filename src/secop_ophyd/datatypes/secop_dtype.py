"""SECoP datatypes as numpy dtypes and bluesky data keys."""

from __future__ import annotations

import time
from typing import Any

import numpy as np
from bluesky.protocols import DataKey, Reading
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
)
from ophyd_async.core import StrictEnum
from ophyd_async.core._utils import StrictEnum
from typing_extensions import NotRequired

from secop_ophyd.datatypes.numpy_dtypes import (
    ArrayNP,
    DtypeNP,
    EnumNP,
    StructNP,
    TupleNP,
    dt_factory,
)


class SECoPDataKey(DataKey):
    """
    A DataKey that is used to describe the SECoP Datatype.
    """

    dtype_str: NotRequired[str]  # type: ignore
    """
    The array-protocol typestring of the data-type object.
    """

    dtype_descr: NotRequired[list]  # type: ignore
    """
    String representation of the numpy structured array dtype.
    """

    SECOP_datainfo: str
    """
    String representation of the original secop datatype.
    """

    SECoP_dtype: NotRequired[str]  # type: ignore
    """
    The SECoP datatype of the data.
    """


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
        self.secop_dtype_str = str(datatype.export_datatype())

        # Shape of Data
        self.shape = []

        self.np_datatype: Any

        # Describe Fields ------------------------

        self.numpy_dtype: np.dtype

        self.dtype_tree: DtypeNP

        self._is_composite: bool = False
        self._is_array: bool = False
        self._is_enum: bool = False

        self.dtype_tree = dt_factory(datatype)

        self.max_depth: int = self.dtype_tree.max_depth

        if isinstance(self.dtype_tree, EnumNP):
            self._is_enum = True

        if isinstance(self.dtype_tree, ArrayNP):
            self.shape = self.dtype_tree.shape
            self._is_array = True
            self._is_composite = (
                True
                if isinstance(self.dtype_tree.root_type, (StructNP, TupleNP))
                else False
            )
            self._is_enum = self._is_enum or isinstance(
                self.dtype_tree.root_type, EnumNP
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

    def get_datakey(self) -> dict:

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

        elif self._is_enum:
            if isinstance(self.dtype_tree, EnumNP):
                exp_val = self.dtype_tree.secop_dtype(reading_val).name
                return exp_val

            elif isinstance(self.dtype_tree, ArrayNP) and isinstance(
                self.dtype_tree.root_type, EnumNP
            ):

                def map_enum(val):
                    return list(
                        map(
                            lambda x: map_enum(x) if isinstance(x, tuple) else x.name,
                            val,
                        )
                    )

                exp_val = map_enum(reading_val)

                return exp_val

            else:
                raise Exception("enum type is not correctly identified in dtype tree")

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

        # exported_val = secop_dt.raw_dtype.export_value(entry.value)

        self.secop_dt.update_dtype(entry.value)

        self.value = secop_dt.secop2val(entry.value)

        self.secop_val = entry.value

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


def describedict_to_datakey(describe_dict: dict) -> SECoPDataKey:
    """Convert a DataKey to a SECoPDataKey"""
    datakey = SECoPDataKey(
        dtype=describe_dict["dtype"],
        shape=describe_dict["shape"],
        source=describe_dict["source"],
        SECOP_datainfo=describe_dict["SECOP_datainfo"],
    )

    if "units" in describe_dict:
        datakey["units"] = describe_dict["units"]

    if "dtype_str" in describe_dict:
        datakey["dtype_str"] = describe_dict["dtype_str"]

    if "dtype_descr" in describe_dict:
        datakey["dtype_descr"] = describe_dict["dtype_descr"]

    if "dtype_numpy" in describe_dict:
        datakey["dtype_numpy"] = describe_dict["dtype_numpy"]

    return datakey


def get_type_param(secop_dtype: DataType) -> str | None:
    sig_type = SECoPdtype(secop_dtype).np_datatype

    # Get the module name
    module = sig_type.__module__

    # For builtins, just return the name without module prefix
    if module == "builtins":
        return sig_type.__name__

    return sig_type.__name__
