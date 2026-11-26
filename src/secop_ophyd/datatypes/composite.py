"""Classification of struct/tuple datatypes."""

from __future__ import annotations

from enum import Enum

from frappy.datatypes import DataType, StructOf, TupleOf

from secop_ophyd.datatypes.secop_dtype import SECoPdtype


class IncompatibleSECoPDatatype(Exception):
    """The datatype cannot be represented as ophyd-async Signal(s): it is a
    struct/tuple nested inside another struct/tuple/array, which is
    unsupported. Raised when this happens for a parameter that is mandatory
    for its module's interface class."""


# max depth of struct/tuple nesting supported by tiled & databroker (and,
# for parameters, by the struct/tuple-member decomposition in SECoPDeviceConnector)
MAX_DEPTH = 1


class CompositeKind(Enum):
    """Classification of a SECoP datatype for parameter-signal construction.

    ATOMIC: scalar or array-of-scalar (incl. array-of-enum/blob/...) -- gets
        a single Signal, unchanged from today's behaviour.
    DECOMPOSABLE: a top-level StructOf/TupleOf whose members are all
        themselves ATOMIC (nesting depth <= MAX_DEPTH) -- gets split into
        one read-only Signal per member (see get_composite_members()).
    UNSUPPORTED: an ArrayOf wrapping a StructOf/TupleOf, or a StructOf/TupleOf
        containing a nested StructOf/TupleOf member -- cannot be represented
        as flat Signals; no Signal is constructed for it.
    """

    ATOMIC = "atomic"
    DECOMPOSABLE = "decomposable"
    UNSUPPORTED = "unsupported"


def classify_datatype(datatype: DataType) -> CompositeKind:
    """Classify a raw SECoP datatype for parameter-signal construction."""
    secop_dt = SECoPdtype(datatype)

    if not secop_dt._is_composite:
        return CompositeKind.ATOMIC

    if isinstance(datatype, (StructOf, TupleOf)) and secop_dt.max_depth <= MAX_DEPTH:
        return CompositeKind.DECOMPOSABLE

    return CompositeKind.UNSUPPORTED


def get_composite_members(datatype: StructOf | TupleOf) -> list[tuple[str, DataType]]:
    """Return (member_key, member_datatype) pairs for a StructOf/TupleOf, in
    declaration order.

    Struct members keep their SECoP member name as key. Tuple members are
    keyed by their stringified 0-based index ("0", "1", ...), used as the
    `_<idx>` naming postfix for split signals.
    """
    if isinstance(datatype, StructOf):
        return list(datatype.members.items())

    if isinstance(datatype, TupleOf):
        return [(str(idx), member) for idx, member in enumerate(datatype.members)]

    raise TypeError(f"{datatype!r} is neither StructOf nor TupleOf")


def optional_struct_members(datainfo: dict) -> list[str]:
    """Names of the optional members of a struct, from its SECoP datainfo:
    without an 'optional' entry every member is mandatory. (frappy's StructOf
    cannot tell: built from such a datainfo it treats every member as
    optional.)"""
    return list(datainfo.get("optional", []))
