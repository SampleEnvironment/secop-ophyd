# mypy: disable-error-code="attr-defined"
import numpy as np
import pytest
from ophyd_async.core import SignalR, SignalW

from secop_ophyd.SECoPDevices import SECoPNodeDevice, SECoPReadableDevice
from secop_ophyd.util import IncompatibleSECoPDatatype


async def test_nested_connect(nested_struct_sim, nested_node_no_re: SECoPNodeDevice):
    assert isinstance(nested_node_no_re, SECoPNodeDevice)


async def test_tuple_dev(nested_struct_sim):

    ophy_struct = SECoPReadableDevice("localhost:10771:ophy_struct")

    await ophy_struct.connect()

    # status is StatusType == TupleOf(EnumType, StringType), always split
    # into a status_0 (code, raw int) / status_1 (message, str) pair
    status_code_sig: SignalR = ophy_struct.status_0
    status_text_sig: SignalR = ophy_struct.status_1

    code_reading = await status_code_sig.read()
    text_reading = await status_text_sig.read()

    stat_code = code_reading[status_code_sig.name]["value"]
    stat_text = text_reading[status_text_sig.name]["value"]

    await status_code_sig.describe()

    assert isinstance(stat_code, int)
    assert isinstance(stat_text, str)


async def test_struct_dev(nested_struct_sim):
    ophy_struct = SECoPReadableDevice("localhost:10771:ophy_struct")
    await ophy_struct.connect()

    # value is StructOf(x, y, z, color), depth 1 -> split into one
    # read-only Signal per member, no monolithic 'value' Signal
    assert not hasattr(ophy_struct, "value")

    value_x_sig: SignalR = ophy_struct.value_x
    value_color_sig: SignalR = ophy_struct.value_color

    x_reading = await value_x_sig.read()
    color_reading = await value_color_sig.read()

    assert isinstance(x_reading[value_x_sig.name]["value"], float)
    assert isinstance(color_reading[value_color_sig.name]["value"], str)


async def test_unsupported_nested_struct_warns(nested_struct_sim):
    # nested_struct contains a StructOf member (pos_struct) and a TupleOf
    # member (tupl) -- nesting depth 2, unsupported. It isn't mandatory for
    # the Readable interface class, so it should only warn and be skipped,
    # not raise.
    with pytest.warns(UserWarning, match="nested_struct"):
        ophy_struct = SECoPReadableDevice("localhost:10771:ophy_struct")
        await ophy_struct.connect()

    assert not hasattr(ophy_struct, "nested_struct")


async def test_nested_dtype_str_signal_generation(
    nested_struct_sim, nested_node_no_re: SECoPNodeDevice
):
    struct_mod = nested_node_no_re.ophy_struct

    # target is writable and composite (StructOf) -> a write-only SignalW
    # at the base name, no read/describe capability there
    target = struct_mod.target
    assert isinstance(target, SignalW)
    assert not isinstance(target, SignalR)

    target_x: SignalR = struct_mod.target_x
    target_color: SignalR = struct_mod.target_color

    reading = await target_x.read()
    descr_reading = await target_x.describe()

    descr = descr_reading.get(target_x.name)
    val = reading.get(target_x.name)["value"]

    assert isinstance(val, float)
    assert descr["dtype"] == "number"

    color_reading = await target_color.read()
    assert isinstance(color_reading.get(target_color.name)["value"], str)


async def test_nested_dtype_set_str_struct(
    nested_struct_sim, nested_node_no_re: SECoPNodeDevice
):
    struct_mod = nested_node_no_re.ophy_struct

    target: SignalW = struct_mod.target

    # SignalW.put() accepts a plain dict for a StructOf parameter (frappy's
    # StructOf.validate() coerces it), no need to build a numpy struct array
    await target.set({"x": 20, "y": 30, "z": 40, "color": "yellow"})

    assert (await struct_mod.target_x.get_value()) == 20
    assert (await struct_mod.target_y.get_value()) == 30
    assert (await struct_mod.target_z.get_value()) == 40
    assert (await struct_mod.target_color.get_value()) == "yellow"


async def test_nested_dtype_set_str_tuple(
    nested_struct_sim, nested_node_no_re: SECoPNodeDevice
):
    struct_mod = nested_node_no_re.ophy_struct

    tuple_param: SignalW = struct_mod.tuple_param

    assert (await struct_mod.tuple_param_0.get_value()) == 5
    assert (await struct_mod.tuple_param_1.get_value()) == 5
    assert (await struct_mod.tuple_param_2.get_value()) == 5
    assert (await struct_mod.tuple_param_3.get_value()) == "green"

    # SignalW.put() also accepts a plain tuple for a TupleOf parameter
    await tuple_param.set((50, 20, 30, "blue"))

    assert (await struct_mod.tuple_param_0.get_value()) == 50
    assert (await struct_mod.tuple_param_1.get_value()) == 20
    assert (await struct_mod.tuple_param_2.get_value()) == 30
    assert (await struct_mod.tuple_param_3.get_value()) == "blue"


async def test_nested_struct_of_arrays(
    nested_struct_sim, nested_node_no_re: SECoPNodeDevice
):
    str_of_arr_mod: SECoPReadableDevice = nested_node_no_re.struct_of_arrays

    # value is StructOf(ints=Array, strings=Array, floats=Array), readonly
    # -> split, no monolithic 'value' Signal
    assert not hasattr(str_of_arr_mod, "value")

    # a struct member that is itself an ArrayOf(atomic) behaves exactly like
    # any other standalone ArrayOf parameter (see test_primitive_arrays.py)
    # -- a plain tuple/list, not a numpy array (numpy wrapping only ever
    # applied to the *structured*-dtype whole-parameter value, not to a
    # plain array field extracted from it)
    ints_val = await str_of_arr_mod.value_ints.get_value()
    assert len(ints_val) == 5
    assert all(isinstance(v, int) for v in ints_val)

    # Write testing: writable_strct_of_arr is the same shape but writable
    # -> a SignalW at the base name, plus split read-only member Signals
    rw_str_of_arr: SignalW = str_of_arr_mod.writable_strct_of_arr
    assert not isinstance(rw_str_of_arr, SignalR)

    old_ints = np.array(await str_of_arr_mod.writable_strct_of_arr_ints.get_value())
    old_strings = await str_of_arr_mod.writable_strct_of_arr_strings.get_value()
    old_floats = np.array(await str_of_arr_mod.writable_strct_of_arr_floats.get_value())

    await rw_str_of_arr.set(
        {
            "ints": old_ints + 20,
            "strings": old_strings,
            "floats": old_floats + 0.2,
        }
    )

    new_ints = await str_of_arr_mod.writable_strct_of_arr_ints.get_value()
    new_floats = await str_of_arr_mod.writable_strct_of_arr_floats.get_value()

    assert np.equal(new_ints, old_ints + 20).all()
    assert np.allclose(new_floats, old_floats + 0.2)


# TODO Nested Arrays (2D) uniform and ragged


async def test_hinted_signal(nested_struct_sim, nested_node_no_re: SECoPNodeDevice):
    str_of_arr_mod: SECoPReadableDevice = nested_node_no_re.struct_of_arrays

    reading = await str_of_arr_mod.read()

    # value (3 members) + writable_strct_of_arr (3 members), all hinted;
    # status_0/status_1 are CONFIG_SIGNAL, not part of read()
    assert len(reading) == 6


async def test_mandatory_composite_incompatible_raises(nested_struct_sim):
    from secop_ophyd.SECoPDevices import SECoPMoveableDevice

    # ophy_struct is a Drivable whose 'value'/'target'/'status' are all
    # depth-1 (compatible); nested_struct (depth 2, unsupported) is not
    # mandatory there, so connecting normally must succeed.
    moveable = SECoPMoveableDevice("localhost:10771:ophy_struct")
    with pytest.warns(UserWarning, match="nested_struct"):
        await moveable.connect()

    assert isinstance(moveable.target, SignalW)


async def test_mandatory_incompatible_datatype_raises_exception(
    nested_struct_sim, monkeypatch
):
    # nested_struct (StructOf containing a nested StructOf/TupleOf member) is
    # genuinely unsupported (nesting depth > MAX_DEPTH). It isn't normally
    # mandatory for the Readable interface class -- see
    # test_unsupported_nested_struct_warns above, which only warns -- so
    # temporarily mark it mandatory (the same way 'value'/'status' already
    # are) to prove SECoPDeviceConnector.connect_real() raises
    # IncompatibleSECoPDatatype instead of warning in that case.
    monkeypatch.setattr(
        SECoPReadableDevice,
        "mandatory_parameters",
        ["value", "status", "nested_struct"],
    )

    ophy_struct = SECoPReadableDevice("localhost:10771:ophy_struct")
    with pytest.raises(IncompatibleSECoPDatatype, match="nested_struct"):
        await ophy_struct.connect()
