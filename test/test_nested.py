from secop_ophyd.SECoPDevices import (
    SECoP_Node_Device,
    SECoP_Struct_Device,
    SECoP_Tuple_Device,
)


from secop_ophyd.util import Path

from secop_ophyd.AsyncSecopClient import AsyncFrappyClient
from ophyd.v2.core import SignalRW


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
