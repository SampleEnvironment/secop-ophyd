# mypy: disable-error-code="attr-defined"
import asyncio

import pytest
from frappy.errors import ImpossibleError
from ophyd_async.core import Command, TriggerableCommand

from secop_ophyd.SECoPDevices import (
    SECoPMoveableDevice,
    SECoPNodeDevice,
)


async def test_struct_inp_cmd(nested_struct_sim, nested_node_no_re: SECoPNodeDevice):
    test_cmd: Command = nested_node_no_re.ophy_struct.test_cmd_CMD

    input_dict = {"name": "test_name", "id": 900, "sort": False}

    status = test_cmd.execute(input_dict)

    await status

    assert isinstance(status.result, int)


def test_command_is_not_triggerable(
    nested_struct_sim, nested_node_no_re: SECoPNodeDevice
):
    test_cmd: Command = nested_node_no_re.ophy_struct.test_cmd_CMD

    # Commands with an argument or a result are exposed via execute(), not the
    # bluesky Triggerable protocol
    assert not hasattr(test_cmd, "trigger")


async def test_secop_error_on_cmd(
    nested_struct_sim, nested_node_no_re: SECoPNodeDevice
):
    test_cmd: Command = nested_node_no_re.ophy_struct.test_cmd_CMD

    # Triggers SECoP Error
    input_dict = {"name": "bad_name", "id": 900, "sort": False}

    with pytest.raises(ImpossibleError):
        await test_cmd.execute(input_dict)


def test_secop_command_plan(nested_struct_sim, nested_node: SECoPNodeDevice, RE):
    input_dict = {"name": "test_name", "id": 900, "sort": False}

    result = None

    def plan():
        nonlocal result
        result = yield from nested_node.ophy_struct.test_cmd(input_dict)

    RE(plan())

    assert isinstance(result, int)


def test_stop_cmd_is_triggerable_command(cryo_sim, cryo_node_no_re: SECoPNodeDevice):
    cryo: SECoPMoveableDevice = cryo_node_no_re.cryo

    assert isinstance(cryo.stop_CMD, TriggerableCommand)


async def test_stop_cmd_success(cryo_sim, cryo_node_no_re: SECoPNodeDevice):
    cryo: SECoPMoveableDevice = cryo_node_no_re.cryo

    await cryo.window.set(5)
    await cryo.tolerance.set(1)
    await cryo.ramp.set(20)

    stat = cryo.set(15)

    await asyncio.sleep(3)

    # stop() always halts the SEC node module; success=True means the
    # resulting AsyncStatus should still report successful completion
    await cryo.stop(success=True)

    await stat
    assert stat.success


async def test_stop_cmd_failure(cryo_sim, cryo_node_no_re: SECoPNodeDevice):
    cryo: SECoPMoveableDevice = cryo_node_no_re.cryo

    await cryo.window.set(5)
    await cryo.tolerance.set(1)
    await cryo.ramp.set(20)

    stat = cryo.set(15)

    await asyncio.sleep(3)

    # stop() always halts the SEC node module; success=False means the
    # resulting AsyncStatus should report failure
    await cryo.stop(success=False)

    with pytest.raises(RuntimeError):
        await stat
    assert not stat.success
