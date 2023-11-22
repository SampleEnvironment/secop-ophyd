import asyncio

from bluesky.protocols import Triggerable
from ophyd_async.core.signal import SignalR, SignalX

from frappy.errors import ImpossibleError
from secop_ophyd.SECoPDevices import (
    SECoP_CMD_Device,
    SECoP_Node_Device,
    SECoPMoveableDevice,
)


async def test_stop_cmd(cryo_sim, cryo_node_internal_loop: SECoP_Node_Device):
    cryo: SECoPMoveableDevice = cryo_node_internal_loop.cryo

    stat = cryo.set(15)

    await asyncio.sleep(3)

    await cryo.stop(True)

    await stat

    assert cryo._stopped is True

    await cryo_node_internal_loop.disconnect()


async def test_stop_no_sucess_cmd(cryo_sim, cryo_node_internal_loop: SECoP_Node_Device):
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

    await cryo_node_internal_loop.disconnect()


async def test_struct_inp_cmd(nested_struct_sim, nested_node: SECoP_Node_Device):
    test_cmd: SECoP_CMD_Device = nested_node.ophy_struct.test_cmd_dev

    await test_cmd.name_arg.set("test_name")
    await test_cmd.id_arg.set(1233)
    await test_cmd.sort_arg.set(False)

    res: SignalR = test_cmd.test_cmd_res

    run_obj: SignalX = test_cmd.test_cmd_x

    stat = run_obj.trigger()

    await stat

    reading_res = await res.read()
    assert isinstance(reading_res.get(res.name)["value"], int)

    await nested_node.disconnect()


def test_triggerable(nested_struct_sim, nested_node: SECoP_Node_Device):
    test_cmd: SECoP_CMD_Device = nested_node.ophy_struct.test_cmd_dev

    assert isinstance(test_cmd, Triggerable)


async def test_SECoP_Error_on_CMD(nested_struct_sim, nested_node: SECoP_Node_Device):
    test_cmd: SECoP_CMD_Device = nested_node.ophy_struct.test_cmd_dev

    error_triggered = False
    # Triggers SECoP Error
    await test_cmd.name_arg.set("bad_name")

    await test_cmd.id_arg.set(1233)
    await test_cmd.sort_arg.set(False)

    res: SignalR = test_cmd.test_cmd_res

    run_obj: SignalX = test_cmd.test_cmd_x

    try:
        stat = run_obj.trigger()
        await stat

    except ImpossibleError:
        error_triggered = True

    assert error_triggered is True

    reading_res = await res.read()
    assert reading_res.get(res.name)["value"] is None

    await nested_node.disconnect()

    async def test_SECoP_triggering_DMD_Dev(
        nested_struct_sim, nested_node: SECoP_Node_Device
    ):
        test_cmd: SECoP_CMD_Device = nested_node.ophy_struct.test_cmd_dev

        error_triggered = False
        # Triggers SECoP Error
        await test_cmd.name_arg.set("bad_name")

        await test_cmd.id_arg.set(1233)
        await test_cmd.sort_arg.set(False)

        res: SignalR = test_cmd.test_cmd_res

        try:
            stat = test_cmd.trigger()
            await stat

        except ImpossibleError:
            error_triggered = True

        assert error_triggered is True

        reading_res = await res.read()
        assert reading_res.get(res.name)["value"] is None

        await nested_node.disconnect()
