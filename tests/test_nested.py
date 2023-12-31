from secop_ophyd.SECoPDevices import (
    SECoP_Node_Device,
    SECoP_Struct_Device,
    SECoP_Tuple_Device,
)


from secop_ophyd.util import Path

from secop_ophyd.AsyncSecopClient import AsyncFrappyClient
from ophyd_async.core.signal import SignalRW

from frappy.datatypes import DataType


async def test_nested_connect(nested_struct_sim, nested_node: SECoP_Node_Device):
    assert isinstance(nested_node, SECoP_Node_Device)
    await nested_node.disconnect()


async def test_tuple_dev(nested_struct_sim, nested_client: AsyncFrappyClient):
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


async def test_struct_dev(nested_struct_sim, nested_client: AsyncFrappyClient):
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
