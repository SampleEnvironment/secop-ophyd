# mypy: disable-error-code="attr-defined"
import asyncio
from typing import Any

import bluesky.plan_stubs as bps
import pytest
from frappy.errors import ImpossibleError
from ophyd_async.core import Command, TriggerableCommand

from secop_ophyd.SECoPDevices import (
    SECoPMoveableDevice,
    SECoPNodeDevice,
    SECoPReadableDevice,
)


class _AnnotatedStructDevice(SECoPReadableDevice):
    """SECoPReadableDevice subclass declaring a command annotation, mirroring
    how parameters/properties are declared via 'value: SignalR[float]'."""

    test_cmd: Command[[dict[str, Any]], int]


async def test_annotated_command_declaration(nested_struct_sim):
    dev = _AnnotatedStructDevice("localhost:10771:ophy_struct")

    await dev.connect()

    assert isinstance(dev.test_cmd, Command)

    status = dev.test_cmd.execute(name="test_name", id=900, sort=False)

    await status

    assert isinstance(status.result, int)


async def test_struct_inp_cmd(nested_struct_sim, nested_node_no_re: SECoPNodeDevice):
    test_cmd: Command = nested_node_no_re.ophy_struct.test_cmd

    status = test_cmd.execute(name="test_name", id=900, sort=False)

    await status

    assert isinstance(status.result, int)


def test_command_is_not_triggerable(
    nested_struct_sim, nested_node_no_re: SECoPNodeDevice
):
    test_cmd: Command = nested_node_no_re.ophy_struct.test_cmd

    # Commands with an argument or a result are exposed via execute(), not the
    # bluesky Triggerable protocol
    assert not hasattr(test_cmd, "trigger")


async def test_secop_error_on_cmd(
    nested_struct_sim, nested_node_no_re: SECoPNodeDevice
):
    test_cmd: Command = nested_node_no_re.ophy_struct.test_cmd

    # Triggers SECoP Error
    with pytest.raises(ImpossibleError):
        await test_cmd.execute(name="bad_name", id=900, sort=False)


async def test_command_wait_for_idle(
    nested_struct_sim, nested_node_no_re: SECoPNodeDevice
):
    test_cmd: Command = nested_node_no_re.ophy_struct.test_cmd

    status = test_cmd.execute(name="test_name", id=900, sort=False, wait_for_idle=True)

    await status

    assert isinstance(status.result, int)


def test_secop_command_plan(nested_struct_sim, nested_node: SECoPNodeDevice, RE):
    result = None

    def plan():
        nonlocal result
        status = nested_node.ophy_struct.test_cmd.execute(
            name="test_name", id=900, sort=False
        )
        yield from bps.wait_for([lambda: status])
        result = status.result

    RE(plan())

    assert isinstance(result, int)


def test_stop_is_not_shadowed_by_a_command(cryo_sim, cryo_node_no_re: SECoPNodeDevice):
    cryo: SECoPMoveableDevice = cryo_node_no_re.cryo

    # "stop" is skipped when creating raw command devices, so the native
    # Stoppable.stop() bound method (from StandardMovable) stays in place.
    assert not isinstance(cryo.stop, (Command, TriggerableCommand))
    assert callable(cryo.stop)


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
