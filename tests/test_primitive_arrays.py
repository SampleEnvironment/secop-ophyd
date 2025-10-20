# mypy: disable-error-code="attr-defined"

import pytest
from bluesky import RunEngine
from bluesky.plans import count
from ophyd_async.core import SignalR

from secop_ophyd.SECoPDevices import SECoPNodeDevice, SECoPReadableDevice


def test_primitive_arrays(
    nested_struct_sim, RE: RunEngine, nested_node: SECoPNodeDevice  # noqa: N803
):

    prim_arr: SECoPReadableDevice = nested_node.primitive_arrays

    prim_arr.add_readables(
        [
            prim_arr.arr_int,
            prim_arr.arr_float,
            prim_arr.arr_arr_float,
            prim_arr.arr_bool,
            prim_arr.arr_String,
            prim_arr.arr_String_nomax,
        ]
    )

    RE(count([prim_arr], 1, 3))


@pytest.mark.parametrize(
    "sig,shape_expected,dtype_numpy_expected",
    [
        pytest.param("arr_int", [6], "<i8", id="integer array"),
        pytest.param("arr_float", [2], "<f8", id="float array"),
        pytest.param("arr_arr_float", [2, 2], "<f8", id="2d float array"),
        pytest.param("arr_bool", [3], "<b1", id="boolean array"),
        pytest.param("arr_String", [3], "<U20", id="string array"),
        pytest.param("arr_String_nomax", [3], "<U100", id="string array no maxlen"),
    ],
)
async def test_primitive_float_array(
    nested_struct_sim,
    RE: RunEngine,  # noqa: N803
    nested_node_no_re: SECoPNodeDevice,
    shape_expected,
    sig,
    dtype_numpy_expected,
):

    prim_arr: SECoPReadableDevice = nested_node_no_re.primitive_arrays

    arr_signal: SignalR = getattr(prim_arr, sig)

    descr = await arr_signal.describe()

    decr_dict = descr[arr_signal.name]

    dtype_numpy = decr_dict["dtype_numpy"]
    shape = decr_dict["shape"]

    assert shape == shape_expected
    assert dtype_numpy == dtype_numpy_expected
