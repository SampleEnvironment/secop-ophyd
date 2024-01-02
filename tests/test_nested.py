import numpy as np
from ophyd_async.core.signal import SignalR, SignalRW

from secop_ophyd.AsyncFrappyClient import AsyncFrappyClient
from secop_ophyd.SECoPDevices import SECoP_Node_Device, SECoPReadableDevice


async def test_nested_connect(nested_struct_sim, nested_node: SECoP_Node_Device):
    assert isinstance(nested_node, SECoP_Node_Device)
    await nested_node.disconnect()


async def test_tuple_dev(nested_client: AsyncFrappyClient):
    ophy_struct = SECoPReadableDevice(
        secclient=nested_client, module_name="ophy_struct"
    )

    status_sig: SignalR = ophy_struct.status

    reading = await status_sig.read()

    reading_val = reading[status_sig.name]["value"]

    await status_sig.describe()

    stat0 = reading_val["f0"]
    stat1 = reading_val["f1"]

    assert stat0.item() == 300  # isinstance(stat0, int)

    assert isinstance(stat1.item(), str)

    await nested_client.disconnect(True)


async def test_struct_dev(nested_client: AsyncFrappyClient):
    ophy_struct = SECoPReadableDevice(
        secclient=nested_client, module_name="ophy_struct"
    )

    nested_struct_sig: SignalR = ophy_struct.nested_struct
    await nested_struct_sig.read()

    await nested_client.disconnect(True)


async def test_nested_dtype_str_signal_generation(
    nested_struct_sim, nested_node: SECoP_Node_Device
):
    struct_mod = nested_node.ophy_struct

    target: SignalRW = struct_mod.target

    reading = await target.read()

    descr_reading = await target.describe()

    descr = descr_reading.get(target.name)
    val = reading.get(target.name)["value"]

    assert isinstance(val, np.ndarray)
    assert descr["dtype"] == "array"
    assert descr["SECoP_dtype"] == "struct"
    await nested_node.disconnect()


async def test_nested_dtype_set_str_struct(
    nested_struct_sim, nested_node: SECoP_Node_Device
):
    struct_mod = nested_node.ophy_struct

    target: SignalRW = struct_mod.target

    reading = await target.read()

    val = reading.get(target.name)["value"]

    val["x"] = 20
    val["y"] = 30
    val["z"] = 40
    val["color"] = "yellow"

    stat = target.set(val)

    await stat

    reading = await target.read()

    val = reading.get(target.name)["value"]

    assert val["x"] == 20
    assert val["y"] == 30
    assert val["z"] == 40
    assert val["color"] == "yellow"
    assert isinstance(val, np.ndarray)

    await nested_node.disconnect()


async def test_nested_dtype_set_str_tuple(
    nested_struct_sim, nested_node: SECoP_Node_Device
):
    struct_mod = nested_node.ophy_struct

    tuple_param: SignalRW = struct_mod.tuple_param

    reading = await tuple_param.read()

    val = reading.get(tuple_param.name)["value"]

    assert val["f0"] == 5
    assert val["f1"] == 5
    assert val["f2"] == 5
    assert val["f3"] == "green"

    tuple_val = (50, 20, 30, "blue")

    stat = tuple_param.set(tuple_val)

    await stat

    reading = await tuple_param.read()

    val = reading.get(tuple_param.name)["value"]

    assert val["f0"] == 50
    assert val["f1"] == 20
    assert val["f2"] == 30
    assert val["f3"] == "blue"
    assert isinstance(val, np.ndarray)

    await nested_node.disconnect()


async def test_nested_struct_of_arrays(
    nested_struct_sim, nested_node: SECoP_Node_Device
):
    str_of_arr_mod: SECoPReadableDevice = nested_node.struct_of_arrays

    reading = await str_of_arr_mod.read()

    val = reading[str_of_arr_mod.value.name]["value"]

    assert isinstance(val, np.ndarray)

    # Write testing
    RW_str_of_arr: SignalRW = str_of_arr_mod.writable_strct_of_arr

    RW_reading = await RW_str_of_arr.read()

    RW_val: np.ndarray = RW_reading[RW_str_of_arr.name]["value"]

    RW_old = RW_val.copy()

    RW_val["ints"] += 20
    RW_val["floats"] += 0.2

    await RW_str_of_arr.set(RW_val)

    RW_reading = await RW_str_of_arr.read()

    RW_val = RW_reading[RW_str_of_arr.name]["value"]

    assert np.equal(RW_val["ints"], RW_old["ints"] + 20).all()

    assert np.equal(RW_val["floats"], RW_old["floats"] + 0.2).all()

    await nested_node.disconnect()


# TODO Nested Arrays (2D) uniform and ragged
