# mypy: disable-error-code="attr-defined"
import asyncio

from bluesky.protocols import Triggerable
from frappy.errors import ImpossibleError
from ophyd_async.core import SignalR, SignalX

from secop_ophyd.SECoPDevices import (
    SECoPCMDDevice,
    SECoPMoveableDevice,
    SECoPNodeDevice,
)


async def test_stop_cmd(cryo_sim, cryo_node_internal_loop: SECoPNodeDevice):
    cryo: SECoPMoveableDevice = cryo_node_internal_loop.cryo

    stat = cryo.set(15)

    await asyncio.sleep(3)

    await cryo.stop(success=True)

    await stat

    assert cryo._stopped is True

    await cryo_node_internal_loop.disconnect_async()


async def test_stop_no_sucess_cmd(cryo_sim, cryo_node_internal_loop: SECoPNodeDevice):
    cryo: SECoPMoveableDevice = cryo_node_internal_loop.cryo

    stat = cryo.set(15)

    rt_error = False

    await asyncio.sleep(3)

    try:
        await cryo.stop(False)
        await stat

    except RuntimeError:
        rt_error = True

    assert cryo._stopped is True
    assert rt_error is True

    await cryo_node_internal_loop.disconnect_async()


async def test_struct_inp_cmd(nested_struct_sim, nested_node: SECoPNodeDevice):
    test_cmd: SECoPCMDDevice = nested_node.ophy_struct.test_cmd_CMD

    input_dict = {"name": "test_name", "id": 900, "sort": False}

    await test_cmd.argument.set(input_dict)  # type: ignore

    res: SignalR = test_cmd.result  # type: ignore

    run_obj: SignalX = test_cmd.commandx

    stat = run_obj.trigger()

    await stat

    reading_res = await res.read()
    print(reading_res)
    assert isinstance(reading_res.get(res.name)["value"], int)

    await nested_node.disconnect_async()


def test_triggerable(nested_struct_sim, nested_node: SECoPNodeDevice):
    test_cmd: SECoPCMDDevice = nested_node.ophy_struct.test_cmd_CMD

    assert isinstance(test_cmd, Triggerable)


async def test_secop_error_on_cmd(nested_struct_sim, nested_node: SECoPNodeDevice):
    test_cmd: SECoPCMDDevice = nested_node.ophy_struct.test_cmd_CMD

    error_triggered = False
    # Triggers SECoP Error
    input_dict = {"name": "bad_name", "id": 900, "sort": False}

    await test_cmd.argument.set(input_dict)  # type: ignore

    res: SignalR = test_cmd.result  # type: ignore

    run_obj: SignalX = test_cmd.commandx

    try:
        stat = run_obj.trigger()
        await stat

    except ImpossibleError:
        error_triggered = True

    assert error_triggered is True

    reading_res = await res.read()
    assert reading_res.get(res.name)["value"] is None

    await nested_node.disconnect_async()


async def test_secop_triggering_cmd_dev(
    nested_struct_sim, nested_node: SECoPNodeDevice
):
    test_cmd: SECoPCMDDevice = nested_node.ophy_struct.test_cmd_CMD

    input_dict = {"name": "test_name", "id": 900, "sort": False}

    await test_cmd.argument.set(input_dict)  # type: ignore

    res: SignalR = test_cmd.result  # type: ignore

    stat = test_cmd.trigger()

    await stat

    reading_res = await res.read()
    assert isinstance(reading_res.get(res.name)["value"], int)

    await nested_node.disconnect_async()
