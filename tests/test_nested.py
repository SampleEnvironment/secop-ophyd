from frappy.datatypes import DataType
from frappy.errors import RangeError
from ophyd_async.core.signal import SignalR, SignalRW

from secop_ophyd.AsyncFrappyClient import AsyncFrappyClient
from secop_ophyd.SECoPDevices import (
    SECoP_Node_Device,
    SECoP_Struct_Device,
    SECoP_Tuple_Device,
    SECoPReadableDevice,
)
from secop_ophyd.util import Path


async def test_nested_connect(nested_struct_sim, nested_node: SECoP_Node_Device):
    assert isinstance(nested_node, SECoP_Node_Device)
    await nested_node.disconnect()


async def test_tuple_dev(nested_client: AsyncFrappyClient):
    path = Path(module_name="ophy_struct", parameter_name="status")

    status_dev = SECoP_Tuple_Device(path=path, secclient=nested_client)

    reading = await status_dev.read()

    await status_dev.describe()

    stat0 = status_dev._read_signals[0]
    stat1 = status_dev._read_signals[1]

    reading0 = reading[stat0.name]
    reading1 = reading[stat1.name]

    assert isinstance(reading0["value"], int)

    assert isinstance(reading1["value"], str)

    await nested_client.disconnect(True)


async def test_struct_dev(nested_client: AsyncFrappyClient):
    path = Path(module_name="ophy_struct", parameter_name="nested_struct")
    nested_dev = SECoP_Struct_Device(secclient=nested_client, path=path)

    await nested_dev.read()

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

    assert isinstance(val, str)
    assert descr["dtype"] == "string"
    assert descr["SECoPtype"] == "struct"
    await nested_node.disconnect()


async def test_nested_dtype_set_str_struct(
    nested_struct_sim, nested_node: SECoP_Node_Device
):
    struct_mod = nested_node.ophy_struct

    target: SignalRW = struct_mod.target

    target_dtype: DataType = target._backend.SECoPdtype_obj

    reading = await target.read()

    val = reading.get(target.name)["value"]

    struct_val = target_dtype.from_string(val)

    struct_val = dict(struct_val)
    struct_val["x"] = 20
    struct_val["y"] = 30
    struct_val["z"] = 40
    struct_val["color"] = "yellow"

    stat = target.set(str(struct_val))

    await stat

    reading = await target.read()

    val = reading.get(target.name)["value"]

    struct_val_read = target_dtype.from_string(val)

    assert struct_val_read["x"] == 20
    assert struct_val_read["y"] == 30
    assert struct_val_read["z"] == 40
    assert struct_val_read["color"] == "yellow"
    assert isinstance(val, str)

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
