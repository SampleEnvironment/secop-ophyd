"""Python types and call signatures of SECoP commands."""

from __future__ import annotations

import inspect
import re
from typing import Any, cast

import numpy as np
from frappy.datatypes import (
    ArrayOf,
    BLOBType,
    BoolType,
    CommandType,
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


def secop_enum_name_to_python(member_name: str) -> str:
    """Convert SECoP enum member name to Python identifier.

    Examples:
        'Low Energy' -> 'LOW_ENERGY'
        'high-power' -> 'HIGH_POWER'
        'Mode 1' -> 'MODE_1'

    :param member_name: Original SECoP enum member name
    :return: Python-compatible identifier in UPPER_CASE
    """
    # Replace spaces and hyphens with underscores, remove other special chars
    cleaned = re.sub(r"[\s-]+", "_", member_name)
    cleaned = re.sub(r"[^a-zA-Z0-9_]", "", cleaned)
    # Convert to uppercase
    cleaned = cleaned.upper()
    # Ensure it doesn't start with a digit
    if cleaned and cleaned[0].isdigit():
        cleaned = "_" + cleaned
    return cleaned


# Maps a SECoP command argument/result datatype to the plain python type used
# for both the runtime bluesky-plan-method signature and the generated
# Command[[ArgT], ResT] annotation (as opposed to SECoPdtype.np_datatype, which
# is the numpy-facing type used for Parameter/Property SignalR annotations).
COMMAND_DTYPE_MAPPING: dict[type[DataType], type] = {
    StructOf: dict[str, Any],
    ArrayOf: list[Any],
    TupleOf: tuple[Any],
    BLOBType: str,
    BoolType: bool,
    EnumType: StrictEnum,
    FloatRange: float,
    IntRange: int,
    ScaledInteger: int,
    StringType: str,
}


def command_dtype_to_python_type(datatype: DataType) -> type:
    """Map a SECoP command argument/result datatype to a plain python type."""
    return COMMAND_DTYPE_MAPPING[datatype.__class__]


def is_numeric_array(datatype: DataType | None) -> bool:
    """Whether ``datatype`` is a (possibly nested) array of numbers or bools."""
    if not isinstance(datatype, ArrayOf):
        return False
    while isinstance(datatype, ArrayOf):
        datatype = datatype.members
    return isinstance(datatype, (FloatRange, IntRange, ScaledInteger, BoolType))


def command_result_python_type(datatype: DataType) -> type:
    """Map a SECoP command result datatype to the python type the command
    returns: a numpy array for an array of numbers, as for a parameter."""
    if is_numeric_array(datatype):
        return np.ndarray
    return command_dtype_to_python_type(datatype)


def _dynamic_enum_class(enum_dt: EnumType, context_name: str) -> type[StrictEnum]:
    """Dynamically build a StrictEnum subclass with the real SECoP member
    names/values, for a command signature annotation built at pure-runtime
    introspection (no codegen involved, so no generated source file to name
    a concrete class in) -- gives `build_command_signature()` a real,
    member-carrying enum instead of the generic `StrictEnum` base class,
    using Python's functional Enum API rather than generated source text.
    """
    members = enum_dt.export_datatype().get("members", {})
    class_name = f"{context_name.capitalize()}Enum"
    # mypy doesn't recognize the functional Enum API on a custom StrEnum
    # subclass with its own metaclass; this is exercised at runtime in
    # tests/test_classgen.py.
    enum_cls = StrictEnum(  # type: ignore[call-arg]
        class_name, {secop_enum_name_to_python(name): name for name in members}
    )
    return cast("type[StrictEnum]", enum_cls)


def _reused_annotated_enum(candidate: Any) -> type[StrictEnum] | None:
    """If `candidate` (a parameter/return annotation captured from a real
    `Command[[ArgT], ResT]` class annotation, before init_command_from_introspection
    would otherwise discard it) is already a genuine, concrete StrictEnum
    subclass -- not the bare base class, not `inspect.Parameter.empty`/
    `inspect.Signature.empty` -- return it so it can be reused verbatim
    instead of building a fresh, differently-named class via
    `_dynamic_enum_class`."""
    if (
        isinstance(candidate, type)
        and issubclass(candidate, StrictEnum)
        and candidate is not StrictEnum
    ):
        return cast("type[StrictEnum]", candidate)
    return None


def python_type_to_str(python_type: Any) -> str:
    """Render a python type object as it should appear in generated source,
    e.g. int -> "int", dict[str, Any] -> "dict[str, Any]"."""
    if python_type in (None, type(None)):
        return "None"
    if isinstance(python_type, type):
        return python_type.__name__
    return str(python_type).replace("typing.", "")


def command_dtype_to_annotation_str(datatype: DataType) -> str:
    """Render a SECoP command argument/result datatype as a python type string,
    e.g. for use in a generated Command[[ArgT], ResT] annotation."""
    return python_type_to_str(command_dtype_to_python_type(datatype))


def build_command_signature(
    cmd_datatype: CommandType,
    annotated_signature: inspect.Signature | None = None,
) -> inspect.Signature:
    """Build the call signature for a SECoP command's argument(s)/result.

    A StructOf argument is unraveled into one KEYWORD_ONLY parameter per
    member (optional members default to None, matching frappy's own "None
    means missing" convention for structs); any other argument datatype
    becomes a single POSITIONAL_OR_KEYWORD 'arg' parameter; no argument means
    no argument parameters. A trailing 'wait_for_idle: bool = False' is
    always appended (KEYWORD_ONLY if the preceding param is, to avoid mixing
    keyword-only and positional-or-keyword params). Used by
    SECoPCommandBackend to build both its real call signature and to
    validate/convert calls via Signature.bind().

    `annotated_signature`, if given, is the signature captured from a real
    `Command[[ArgT], ResT]` class annotation (before this function's caller
    would otherwise discard it) -- when the bare (non-struct) argument or
    result is an Enum and `annotated_signature` already carries a concrete
    generated enum class there, that class is reused instead of building a
    fresh, differently-named one via `_dynamic_enum_class`. Struct members
    are unaffected: `annotated_signature` has a different shape there (a
    single flat argument, not a per-member decomposition), and struct
    members never get named enum classes at codegen time anyway.
    """
    params: list[inspect.Parameter] = []
    arg_dt = cmd_datatype.argument
    annotated_params = (
        list(annotated_signature.parameters.values()) if annotated_signature else []
    )

    if isinstance(arg_dt, StructOf):
        for member_name, member_dtype in arg_dt.members.items():
            annotation = (
                _dynamic_enum_class(member_dtype, member_name)
                if isinstance(member_dtype, EnumType)
                else command_dtype_to_python_type(member_dtype)
            )
            params.append(
                inspect.Parameter(
                    member_name,
                    inspect.Parameter.KEYWORD_ONLY,
                    annotation=annotation,
                    default=(
                        None
                        if member_name in arg_dt.optional
                        else inspect.Parameter.empty
                    ),
                )
            )
    elif arg_dt is not None:
        if isinstance(arg_dt, EnumType):
            reused = (
                _reused_annotated_enum(annotated_params[0].annotation)
                if annotated_params
                else None
            )
            annotation = reused or _dynamic_enum_class(arg_dt, "arg")
        else:
            annotation = command_dtype_to_python_type(arg_dt)
        params.append(
            inspect.Parameter(
                "arg",
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
                annotation=annotation,
            )
        )

    wait_for_idle_kind = (
        inspect.Parameter.KEYWORD_ONLY
        if params and params[-1].kind is inspect.Parameter.KEYWORD_ONLY
        else inspect.Parameter.POSITIONAL_OR_KEYWORD
    )
    params.append(
        inspect.Parameter(
            "wait_for_idle", wait_for_idle_kind, annotation=bool, default=False
        )
    )

    res_dt = cmd_datatype.result
    return_annotation: type | None
    if isinstance(res_dt, EnumType):
        reused = (
            _reused_annotated_enum(annotated_signature.return_annotation)
            if annotated_signature
            else None
        )
        return_annotation = reused or _dynamic_enum_class(res_dt, "result")
    else:
        return_annotation = (
            command_result_python_type(res_dt) if res_dt is not None else None
        )
    return inspect.Signature(params, return_annotation=return_annotation)
