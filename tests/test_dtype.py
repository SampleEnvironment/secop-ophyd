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

from secop_ophyd.util import mkdt, mklist


def test_dtype_array(array_dtype, array_tuple_dtype):
    dty = array_dtype[0]
    elem = array_dtype[1]

    dty_tup = array_tuple_dtype[0]
    elem_tup = array_tuple_dtype[1]

    np_dt = mkdt(secop_dt=dty)

    arr = mklist(elem, dty)

    np_arr = np.asarray(a=arr, dtype=np_dt)
