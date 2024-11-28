# mypy: disable-error-code="attr-defined"
import pytest
from frappy.client import CacheItem
from frappy.datatypes import ArrayOf, FloatRange, StringType, StructOf, TupleOf

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
