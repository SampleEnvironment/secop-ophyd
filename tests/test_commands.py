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


async def test_stop_cmd(cryo_sim, cryo_node_no_re: SECoPNodeDevice):
    cryo: SECoPMoveableDevice = cryo_node_no_re.cryo

    await cryo.window.set(5)

    await cryo.tolerance.set(1)

    await cryo.ramp.set(20)

    stat = cryo.set(15)

    await asyncio.sleep(3)

    # essentially a NOOP (stops are only passed through to SECoP on success=False)
    await cryo.stop(success=True)
    assert cryo._stopped is False, "Move should not be stopped when success=True"
    assert (
        cryo._success is True
    ), "Move should be marked as successful when success=True"
    assert (
        not stat.done
    ), "Move should still be in progress after stop with success=True"

    # move is still going on
    await cryo.stop(success=False)
    assert cryo._stopped is True, "Move should be stopped when success=False"
    assert (
        cryo._success is False
    ), "Move should be marked as unsuccessful when success=False"

    await stat


async def test_struct_inp_cmd(nested_struct_sim, nested_node_no_re: SECoPNodeDevice):
    test_cmd: SECoPCMDDevice = nested_node_no_re.ophy_struct.test_cmd_CMD

    input_dict = {"name": "test_name", "id": 900, "sort": False}

    await test_cmd.argument.set(input_dict)  # type: ignore

    res: SignalR = test_cmd.result  # type: ignore

    run_obj: SignalX = test_cmd.commandx

    stat = run_obj.trigger()

    await stat

    reading_res = await res.read()
    print(reading_res)
    assert isinstance(reading_res.get(res.name)["value"], int)


def test_triggerable(nested_struct_sim, nested_node_no_re: SECoPNodeDevice):
    test_cmd: SECoPCMDDevice = nested_node_no_re.ophy_struct.test_cmd_CMD

    assert isinstance(test_cmd, Triggerable)


async def test_secop_error_on_cmd(
    nested_struct_sim, nested_node_no_re: SECoPNodeDevice
):
    test_cmd: SECoPCMDDevice = nested_node_no_re.ophy_struct.test_cmd_CMD

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


async def test_secop_triggering_cmd_dev(
    nested_struct_sim, nested_node_no_re: SECoPNodeDevice
):
    test_cmd: SECoPCMDDevice = nested_node_no_re.ophy_struct.test_cmd_CMD

    input_dict = {"name": "test_name", "id": 900, "sort": False}

    await test_cmd.argument.set(input_dict)  # type: ignore

    res: SignalR = test_cmd.result  # type: ignore

    stat = test_cmd.trigger()

    await stat

    reading_res = await res.read()
    assert isinstance(reading_res.get(res.name)["value"], int)
