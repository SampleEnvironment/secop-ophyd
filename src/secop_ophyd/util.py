"""Paths into SECoP parameter values.

The datatype helpers that were here moved to `secop_ophyd.datatypes`; they
are still importable from here."""

from __future__ import annotations

import copy
from functools import reduce
from itertools import chain
from typing import List, Union

# moved to secop_ophyd.datatypes, imported for compatibility
from secop_ophyd.datatypes.commands import (
    COMMAND_DTYPE_MAPPING,
    build_command_signature,
    command_dtype_to_annotation_str,
    command_dtype_to_python_type,
    command_result_python_type,
    is_numeric_array,
    python_type_to_str,
    secop_enum_name_to_python,
)
from secop_ophyd.datatypes.composite import (
    MAX_DEPTH,
    CompositeKind,
    IncompatibleSECoPDatatype,
    classify_datatype,
    get_composite_members,
    optional_struct_members,
)
from secop_ophyd.datatypes.numpy_dtypes import (
    BLOBNP,
    STR_LEN_DEFAULT,
    ArrayNP,
    BoolNP,
    DtypeNP,
    EnumNP,
    FloatNP,
    IntNP,
    ScaledIntNP,
    StringNP,
    StructNP,
    TupleNP,
    dt_factory,
)
from secop_ophyd.datatypes.secop_dtype import (
    SECOP2DTYPE,
    SECoPDataKey,
    SECoPdtype,
    SECoPReading,
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


__all__ = [
    "COMMAND_DTYPE_MAPPING",
    "ArrayNP",
    "BLOBNP",
    "BoolNP",
    "CompositeKind",
    "DtypeNP",
    "EnumNP",
    "FloatNP",
    "IncompatibleSECoPDatatype",
    "IntNP",
    "MAX_DEPTH",
    "Path",
    "SECOP2DTYPE",
    "SECoPDataKey",
    "SECoPReading",
    "SECoPdtype",
    "STR_LEN_DEFAULT",
    "ScaledIntNP",
    "StringNP",
    "StructNP",
    "TupleNP",
    "build_command_signature",
    "classify_datatype",
    "command_dtype_to_annotation_str",
    "command_dtype_to_python_type",
    "command_result_python_type",
    "deep_get",
    "dt_factory",
    "get_composite_members",
    "is_numeric_array",
    "optional_struct_members",
    "python_type_to_str",
    "secop_enum_name_to_python",
]
