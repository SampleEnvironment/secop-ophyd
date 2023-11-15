from __future__ import annotations
from functools import reduce
import copy
from itertools import chain

from frappy.datatypes import (
    StructOf,
    ArrayOf,
    DataType,
    IntRange,
    ScaledInteger,
    EnumType,
    StringType,
    FloatRange,
    BoolType,
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
        self._last_named_param = None

        self._dev_path = []

    # Path is extended
    def append(self, elem: str or int) -> Path:
        new_path = copy.deepcopy(self)

        if isinstance(elem, str):
            new_path._last_named_param = len(new_path._dev_path)

        new_path._dev_path.append(elem)

        return new_path

    def get_param_path(self):
        return {"module": self._module_name, "parameter": self._accessible_name}

    def get_path_tuple(self):
        return (self._module_name, self._accessible_name)

    def get_memberinfo_path(self) -> list:
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
                    "path is incorrect " + key + " is not in dict: " + str(dic)
                )
        # insert new value
        if self._dev_path[-1] in d:
            d[self._dev_path[-1]] = new_val
        else:
            # wrong path
            raise Exception("path is incorrect " + key + " is not in dict: " + str(dic))
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
