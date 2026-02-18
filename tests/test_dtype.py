# mypy: disable-error-code="attr-defined"
import numpy as np
import pytest
from frappy.client import CacheItem
from frappy.datatypes import (
    ArrayOf,
    EnumType,
    FloatRange,
    IntRange,
    ScaledInteger,
    StringType,
    StructOf,
    TupleOf,
)
from frappy.lib.enum import EnumMember

from secop_ophyd.util import SECoPdtype, SECoPReading

RAGGED = True
REGULAR = False


@pytest.mark.parametrize(
    "start_dtype,data,expected_shape,expected_update_shape",
    [
        pytest.param(
            ArrayOf(FloatRange(), minlen=0, maxlen=100),
            [0.1, 0.1],
            [100],
            [2],
            id="Ragged 1D",
        ),
        pytest.param(
            ArrayOf(ArrayOf(FloatRange()), minlen=0, maxlen=100),
            [[10, 10], [10, 10]],
            [100, 100],  # maxlen defaults to 100 if not given
            [2, 2],
            id="Ragged 2D",
        ),
        pytest.param(
            ArrayOf(ArrayOf(ArrayOf(FloatRange())), minlen=0, maxlen=99),
            [[[10, 10], [10, 10]], [[10, 10], [10, 10]]],
            [99, 100, 100],
            [2, 2, 2],
            id="Ragged 3D",
        ),
        pytest.param(
            ArrayOf(
                ArrayOf(ArrayOf(FloatRange(), minlen=2, maxlen=2), minlen=2, maxlen=2),
                minlen=2,
                maxlen=2,
            ),
            [[[10, 10], [10, 10]], [[10, 10], [10, 10]]],
            [2, 2, 2],
            [2, 2, 2],
            id="Regular 3D",
        ),
        pytest.param(
            ArrayOf(TupleOf(ArrayOf(FloatRange(), minlen=0, maxlen=50), StringType())),
            [([1, 2, 3], "blah")],
            [100],
            [1],
            id="Complex Ragged 1D",
        ),
        pytest.param(
            ArrayOf(
                ArrayOf(
                    TupleOf(ArrayOf(FloatRange(), minlen=0, maxlen=50), StringType())
                )
            ),
            [
                [([1, 2, 3], "blah"), ([1, 2, 3], "blah")],
                [([1, 2, 3], "blah"), ([1, 2, 3], "blah")],
            ],
            [100, 100],
            [2, 2],
            id="Complex Ragged 2D",
        ),
        pytest.param(
            TupleOf(ArrayOf(FloatRange(), minlen=0, maxlen=50), StringType()),
            ([1, 2, 3], "blah"),
            [],
            [],
            id="Complex",
        ),
        pytest.param(
            StructOf(
                mass=ArrayOf(FloatRange()),
                pressure=ArrayOf(FloatRange()),
                timestamp=ArrayOf(FloatRange()),
            ),
            {"mass": [], "pressure": [], "timestamp": []},
            [],
            [],
            id="struct of empty arrays",
        ),
        pytest.param(
            ArrayOf(ArrayOf(FloatRange())),
            [[], []],
            [100, 100],
            [2, 0],
            id="empty 2d array ",
        ),
        pytest.param(
            ArrayOf(ArrayOf(FloatRange())),
            [[]],
            [100, 100],
            [1, 0],
            id="empty 2d array [1,0]",
        ),
        pytest.param(
            ArrayOf(ArrayOf(FloatRange())),
            [],
            [100, 100],
            [0],
            id="empty empty 2d",
        ),
    ],
)
def test_arrayof_update_dtype(start_dtype, data, expected_shape, expected_update_shape):
    sdtype = SECoPdtype(start_dtype)
    assert sdtype.shape == expected_shape

    sdtype.update_dtype(data)
    assert sdtype.shape == expected_update_shape

    SECoPReading(sdtype, CacheItem(data, 234342.2, None, datatype=start_dtype))


@pytest.mark.parametrize(
    "start_dtype,expected_dtype_descr,expected_shape,max_depth",
    [
        pytest.param(
            TupleOf(ArrayOf(FloatRange(), maxlen=50), StringType()),
            [("f0", "<f8", (50,)), ("f1", "<U100")],
            [],
            1,
            id="Tuple of Array",
        ),
        pytest.param(
            ArrayOf(
                TupleOf(FloatRange(), StringType(), ArrayOf(ArrayOf(FloatRange()))),
                maxlen=50,
            ),
            [("f0", "<f8"), ("f1", "<U100"), ("f2", "<f8", (100, 100))],
            [50],
            1,
            id="Array of Tuple",
        ),
        pytest.param(
            ArrayOf(
                TupleOf(
                    FloatRange(), StringType(), TupleOf(FloatRange(), StringType())
                ),
                maxlen=50,
            ),
            [("f0", "<f8"), ("f1", "<U100"), ("f2", [("f0", "<f8"), ("f1", "<U100")])],
            [50],
            2,
            id="Array of (Tuple of Tuple)",
        ),
        pytest.param(
            ArrayOf(
                StructOf(a=FloatRange(), b=StringType(), c=StringType()),
                maxlen=50,
            ),
            [("a", "<f8"), ("b", "<U100"), ("c", "<U100")],
            [50],
            1,
            id="Array of Struct",
        ),
        pytest.param(
            ArrayOf(
                StructOf(
                    a=FloatRange(),
                    b=StringType(),
                    c=TupleOf(FloatRange(), FloatRange()),
                ),
                maxlen=50,
            ),
            [("a", "<f8"), ("b", "<U100"), ("c", [("f0", "<f8"), (("f1", "<f8"))])],
            [50],
            2,
            id="Array of (Struct of tuple)",
        ),
    ],
)
def test_describe_str(start_dtype, expected_dtype_descr, expected_shape, max_depth):
    sdtype = SECoPdtype(start_dtype)

    assert sdtype.shape == expected_shape
    assert sdtype.dtype_descr == expected_dtype_descr
    assert sdtype.max_depth == max_depth


@pytest.mark.parametrize(
    "start_dtype,np_input,expected_output,type_checks,ophy_val",
    [
        pytest.param(
            StructOf(
                float_val=FloatRange(),
                string_val=StringType(),
                int_val=IntRange(),
                scaled_val=ScaledInteger(scale=0.01),
                enum_val=EnumType(ON=1, OFF=0),
            ),
            np.array(
                (3.14, "test", 42, 123, 1),
                dtype=[
                    ("float_val", "<f8"),
                    ("string_val", "<U100"),
                    ("int_val", "<i4"),
                    ("scaled_val", "<f8"),
                    ("enum_val", "<i4"),
                ],
            ),
            {
                "float_val": 3.14,
                "string_val": "test",
                "int_val": 42,
                "scaled_val": 123,
                "enum_val": 1,
            },
            lambda val: (
                isinstance(val["float_val"], float)
                and isinstance(val["string_val"], str)
                and isinstance(val["int_val"], int)
                and isinstance(val["scaled_val"], int)
                and isinstance(val["enum_val"], int)
            ),
            np.array(
                (3.14, "test", 42, 123, 1),
                dtype=[
                    ("float_val", "<f8"),
                    ("string_val", "<U100"),
                    ("int_val", "<i4"),
                    ("scaled_val", "<f8"),
                    ("enum_val", "<i4"),
                ],
            ),
            id="Struct of all atomic types",
        ),
        pytest.param(
            TupleOf(
                FloatRange(),
                StringType(),
                IntRange(),
                ScaledInteger(scale=0.1),
                EnumType(ON=1, OFF=0),
            ),
            np.array(
                (2.718, "hello", 99, 25, 0),
                dtype=[
                    ("f0", "<f8"),
                    ("f1", "<U100"),
                    ("f2", "<i8"),
                    ("f3", "<f8"),
                    ("f4", "<i8"),
                ],
            ),
            (2.718, "hello", 99, 25, 0),
            lambda val: (
                isinstance(val[0], float)
                and isinstance(val[1], str)
                and isinstance(val[2], int)
                and isinstance(val[3], int)
                and isinstance(val[4], int)
            ),
            np.array(
                (2.718, "hello", 99, 25, 0),
                dtype=[
                    ("f0", "<f8"),
                    ("f1", "<U100"),
                    ("f2", "<i8"),
                    ("f3", "<f8"),
                    ("f4", "<i8"),
                ],
            ),
            id="Tuple of all atomic types",
        ),
        pytest.param(
            StructOf(value=FloatRange(), state=EnumType(IDLE=0, BUSY=1, ERROR=2)),
            np.array((42.5, 2), dtype=[("value", "<f8"), ("state", "<i4")]),
            {"value": 42.5, "state": 2},
            lambda val: (
                isinstance(val["value"], float) and isinstance(val["state"], int)
            ),
            np.array((42.5, 2), dtype=[("value", "<f8"), ("state", "<i4")]),
            id="Struct of Float and Enum",
        ),
        pytest.param(
            ArrayOf(EnumType(ON=1, OFF=0)),
            [1, 0, 1],
            ArrayOf(EnumType(ON=1, OFF=0)).import_value([1, 0, 1]),
            lambda val: isinstance(val, tuple)
            and all(isinstance(v, EnumMember) for v in val),
            ["ON", "OFF", "ON"],
            id="Array of Enums",
        ),
        pytest.param(
            ArrayOf(ArrayOf(EnumType(ON=1, OFF=0))),
            [[1, 0, 1], [0], [1]],
            ArrayOf(ArrayOf(EnumType(ON=1, OFF=0))).import_value([[1, 0, 1], [0], [1]]),
            lambda val: isinstance(val, tuple)
            and all(isinstance(v, EnumMember) or isinstance(v, tuple) for v in val),
            [["ON", "OFF", "ON"], ["OFF"], ["ON"]],
            id="Ragged array of Enums",
        ),
    ],
)
def test_val2secop(start_dtype, np_input, expected_output, type_checks, ophy_val):
    sdtype = SECoPdtype(start_dtype)

    secop_val = sdtype.val2secop(np_input)

    # Use numpy.testing for proper comparison with numpy arrays
    np.testing.assert_equal(secop_val, expected_output)

    # Verify types are correct (should be Python types, not numpy arrays)

    assert type_checks(secop_val), f"Type check failed for {secop_val}"

    back_to_ophyd = sdtype.secop2val(secop_val)

    print(f"Original ophyd value: {ophy_val}")
    print(f"Back to ophyd value: {back_to_ophyd}")

    assert (
        back_to_ophyd == ophy_val
    ), f"Back to ophyd conversion failed for {back_to_ophyd}"
