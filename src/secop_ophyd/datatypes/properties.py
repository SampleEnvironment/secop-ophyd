"""Datatypes and values of SECoP module and node properties."""

import json
from typing import Any

from frappy.datatypes import (
    ArrayOf,
    BoolType,
    DataType,
    FloatRange,
    IntRange,
    StringType,
    StructOf,
    TupleOf,
)

from secop_ophyd.datatypes.secop_dtype import get_type_param
from secop_ophyd.propertykeys import MEANING


def _is_composite_dtype(datatype: DataType) -> bool:
    if isinstance(datatype, (StructOf, TupleOf)):
        return True
    if isinstance(datatype, ArrayOf):
        return _is_composite_dtype(datatype.members)
    return False


def secop_dtype_obj_from_json(prop_val):
    if isinstance(prop_val, str):
        return StringType()

    # bool first: bool is a subclass of int
    if isinstance(prop_val, bool):
        return BoolType()

    if isinstance(prop_val, (int, float)):
        return FloatRange()

    if isinstance(prop_val, dict):
        members = {}
        for key, elem in prop_val.items():
            members[key] = secop_dtype_obj_from_json(elem)

        return StructOf(**members)

    if isinstance(prop_val, list):
        # empty list, cannot infer proper type
        if not prop_val:
            return ArrayOf(FloatRange())
        # numbers: one float array, whether the node sends 10 or 10.0
        if all(
            isinstance(elem, (int, float)) and not isinstance(elem, bool)
            for elem in prop_val
        ):
            return ArrayOf(FloatRange())
        # check if all elements have same Type:
        if all(isinstance(elem, type(prop_val[0])) for elem in prop_val):
            members = secop_dtype_obj_from_json(prop_val[0])
            return ArrayOf(members)
        else:
            members = []  # type: ignore
            for elem in prop_val:
                members.append(secop_dtype_obj_from_json(elem))  # type: ignore
            return TupleOf(*members)

    raise Exception(
        f"""unsupported datatype in Property:  {str(prop_val.__class__.__name__)}\n
        propval: {prop_val}"""
    )


def property_dtype_and_value(prop_val: Any) -> tuple[DataType, Any]:
    """Datatype and value with which a SECoP property is exposed as a Signal.

    Struct/tuple-valued properties (and arrays of them) are exposed as a JSON
    string: bluesky/TiledWriter cannot store numpy structured arrays."""
    if isinstance(prop_val, dict):
        return StringType(), json.dumps(prop_val)
    try:
        datatype = secop_dtype_obj_from_json(prop_val)
    except Exception:
        # e.g. null members: keep the value, as JSON
        return StringType(), json.dumps(prop_val)
    if _is_composite_dtype(datatype):
        return StringType(), json.dumps(prop_val)
    return datatype, prop_val


# members of the predefined module property 'meaning' (SECoP 2.0: a struct
# whose members are all optional)
MEANING_MEMBERS: dict[str, DataType] = {
    "function": StringType(),
    "importance": IntRange(0, 50),
    "belongs_to": StringType(),
    "link": StringType(),
    "key": StringType(),
}


def _meaning_members(value: Any) -> dict[str, Any] | None:
    """Members of a 'meaning' module property, or None if it is malformed.

    SECoP 1.x sends a ``(function, importance)`` tuple, SECoP 2.0 a struct."""
    if isinstance(value, (list, tuple)) and len(value) == 2:
        value = {"function": value[0], "importance": value[1]}
    if (
        not isinstance(value, dict)
        or not value
        or not set(value) <= MEANING_MEMBERS.keys()
    ):
        return None
    try:
        return {key: MEANING_MEMBERS[key](member) for key, member in value.items()}
    except Exception:
        return None


def module_property_signals(
    properties: dict[str, Any],
) -> dict[str, tuple[DataType, Any]]:
    """Signal name -> (datatype, value) for the properties of a module.

    A well-formed 'meaning' becomes one typed signal per member it has
    (``meaning_function``, ``meaning_importance``, ...). Every other property,
    and a malformed 'meaning', is one signal as given by
    `property_dtype_and_value`."""
    signals: dict[str, tuple[DataType, Any]] = {}
    for name, value in properties.items():
        members = _meaning_members(value) if name == MEANING else None
        if members is None:
            signals[name] = property_dtype_and_value(value)
            continue
        for key, member in members.items():
            signals[f"{name}_{key}"] = (MEANING_MEMBERS[key], member)
    return signals


def get_type_prop(prop_value) -> str | None:
    secop_dtype, _ = property_dtype_and_value(prop_value)
    return get_type_param(secop_dtype)
