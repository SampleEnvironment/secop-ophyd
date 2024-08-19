import pytest
from frappy.datatypes import ArrayOf, FloatRange, StringType, StructOf, TupleOf

from secop_ophyd.util import NestedRaggedArray, SECoPdtype


def test_nested_ragged_inside_struct_arrays():
    ragged = ArrayOf(
        StructOf(arr2=ArrayOf(FloatRange(), minlen=0, maxlen=100), name=StringType()),
        minlen=0,
        maxlen=100,
    )

    with pytest.raises(NestedRaggedArray):
        SECoPdtype(ragged)


def test_nested_ragged_inside_tuple_arrays():
    ragged = ArrayOf(
        TupleOf(ArrayOf(FloatRange(), minlen=0, maxlen=100), StringType()),
        minlen=0,
        maxlen=100,
    )

    with pytest.raises(NestedRaggedArray):
        SECoPdtype(ragged)


def test_nested_ragged_arrays():
    ragged = ArrayOf(ArrayOf(FloatRange(), minlen=0, maxlen=100), minlen=0, maxlen=100)

    with pytest.raises(NestedRaggedArray):
        SECoPdtype(ragged)
