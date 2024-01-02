import numpy as np
from frappy.datatypes import DataType
from frappy.errors import RangeError
from ophyd_async.core.signal import SignalR, SignalRW

from secop_ophyd.AsyncFrappyClient import AsyncFrappyClient
from secop_ophyd.SECoPDevices import SECoP_Node_Device, SECoPReadableDevice
from secop_ophyd.util import SECoPdtype


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

    assert isinstance(stat0, int)

    assert isinstance(stat1, str)

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
    assert descr["SECoPtype"] == "struct"
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

    target_dtype: DataType = tuple_param._backend.SECoPdtype_obj

    reading = await tuple_param.read()

    val = reading.get(tuple_param.name)["value"]

    tuple_val = target_dtype.from_string(val)

    assert tuple_val[0] == 5
    assert tuple_val[1] == 5
    assert tuple_val[2] == 5
    assert tuple_val[3] == "green"

    tuple_val = (50, 20, 30, "blue")

    stat = tuple_param.set(str(tuple_val))

    await stat

    reading = await tuple_param.read()

    val = reading.get(tuple_param.name)["value"]

    tuple_val_read = target_dtype.from_string(val)

    assert tuple_val_read[0] == 50
    assert tuple_val_read[1] == 20
    assert tuple_val_read[2] == 30
    assert tuple_val_read[3] == "blue"
    assert isinstance(val, str)

    await nested_node.disconnect()


async def test_nested_struct_of_arrays(
    nested_struct_sim, nested_node: SECoP_Node_Device
):
    str_of_arr_mod: SECoPReadableDevice = nested_node.struct_of_arrays

    floats: SignalR = str_of_arr_mod.value_floats

    reading = await floats.read()

    val = reading[floats.name]["value"]

    assert isinstance(val, list)

    reading2 = await floats.read()

    val2 = reading2[floats.name]["value"]

    # check if lists are not equal
    assert all(x != y for x, y in zip(val, val2))

    dev_reading = await str_of_arr_mod.read()

    # one for each struct member and one where the whole struct is converted to a string
    assert len(dev_reading) == 4

    # check if struct was converted to string
    assert isinstance(dev_reading[str_of_arr_mod.value.name]["value"], str)

    # Write testing
    wfloats: SignalRW = str_of_arr_mod.writable_strct_of_arr_floats
    wints: SignalRW = str_of_arr_mod.writable_strct_of_arr_ints

    floats_reading = await wfloats.read()
    ints_reading = await wints.read()

    floats_val = floats_reading[wfloats.name]["value"]
    ints_val = ints_reading[wints.name]["value"]

    floats_new_val = [x + 20 for x in floats_val]

    await wfloats.set(floats_new_val)

    floats_reading = await wfloats.read()
    ints_reading = await wints.read()

    floats_new_val_remote = floats_reading[wfloats.name]["value"]
    ints_new_val = ints_reading[wints.name]["value"]

    assert all(x == y for x, y in zip(floats_new_val, floats_new_val_remote))
    assert all(x == y for x, y in zip(ints_val, ints_new_val))

    await nested_node.disconnect()


async def test_nested_readerr(nested_struct_sim, nested_node: SECoP_Node_Device):
    str_of_arr_mod: SECoPReadableDevice = nested_node.struct_of_arrays_readerr

    floats: SignalR = str_of_arr_mod.value_floats

    try:
        await floats.read()
    except RangeError:
        pass

    await nested_node.disconnect()


# TODO Nested Arrays (2D) uniform and ragged
