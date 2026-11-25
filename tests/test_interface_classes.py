# mypy: disable-error-code="attr-defined"
"""Device classes for each SECoP interface class, run against the fast modules
of the motion test node (see frappy_modules/motion.py)."""

import asyncio
import time
from typing import Annotated as A

import bluesky.plan_stubs as bps
import numpy as np
import pytest
from bluesky.protocols import Checkable, Locatable, Movable, Stoppable
from bluesky.utils import ProgressBarManager
from ophyd_async.core import Command, SignalR, SignalW, TriggerableCommand

from secop_ophyd.SECoPDevices import (
    BUSY,
    ERROR,
    IDLE,
    ParameterType,
    SECoPCommunicatorDevice,
    SECoPMoveableDevice,
    SECoPNodeDevice,
    SECoPTriggerableDevice,
    SECoPWritableDevice,
    _status_code,
)


async def _status(dev) -> tuple[int, str]:
    # status is split into status_0 (code, as enum member name) / status_1 (text)
    code_name = await dev.status_0.get_value(cached=False)
    text = await dev.status_1.get_value(cached=False)
    return _status_code(dev.status_0, code_name), str(text)


@pytest.fixture
async def detector(motion_node_no_re: SECoPNodeDevice):
    detector: SECoPTriggerableDevice = motion_node_no_re.detector
    await detector.duration.set(0.1)
    await detector.fail.set(False)
    yield detector
    # leave the shared node at rest for the next test
    await detector.fail.set(False)
    await detector.stop()


async def test_interface_class_mapping(motion_node_no_re: SECoPNodeDevice):
    assert type(motion_node_no_re.mover) is SECoPMoveableDevice
    assert type(motion_node_no_re.detector) is SECoPTriggerableDevice
    assert type(motion_node_no_re.writer) is SECoPWritableDevice
    assert type(motion_node_no_re.comm) is SECoPCommunicatorDevice

    assert isinstance(motion_node_no_re.detector.go, TriggerableCommand)
    assert isinstance(motion_node_no_re.comm.communicate, Command)


async def test_triggerable_go_wait_for_idle(detector: SECoPTriggerableDevice):
    count = await detector.value.get_value(cached=False)

    # wait_for_idle is added to the signature at connect time
    await detector.go.execute(wait_for_idle=True)  # type: ignore[call-arg]

    assert await detector.value.get_value(cached=False) == count + 1
    assert await _status(detector) == (IDLE, "measurement done")


async def test_triggerable_go_trigger(detector: SECoPTriggerableDevice):
    count = await detector.value.get_value(cached=False)

    # TriggerableCommand.trigger() only starts the measurement ...
    await detector.go.trigger()
    assert (await _status(detector))[0] == BUSY

    # ... and the module's wait_for_idle() waits for it to finish
    await asyncio.wait_for(detector.wait_for_idle(), timeout=5)

    assert await detector.value.get_value(cached=False) == count + 1


async def test_triggerable_failed_measurement(detector: SECoPTriggerableDevice):
    await detector.fail.set(True)

    await asyncio.wait_for(
        detector.go.execute(wait_for_idle=True),  # type: ignore[call-arg]
        timeout=5,
    )

    assert (await _status(detector))[0] >= ERROR
    assert detector._success is False


async def test_triggerable_stop(detector: SECoPTriggerableDevice):
    await detector.duration.set(5)
    count = await detector.value.get_value(cached=False)

    await detector.go.trigger()
    assert (await _status(detector))[0] == BUSY

    await detector.stop()

    assert await _status(detector) == (IDLE, "stopped")
    assert await detector.value.get_value(cached=False) == count


async def test_writable_signal_level_set(motion_node_no_re: SECoPNodeDevice):
    writer: SECoPWritableDevice = motion_node_no_re.writer
    new_value = await writer.value.get_value(cached=False) + 1.5

    # an atomic target is also a writable signal
    await writer.target.set(new_value)

    assert await writer.value.get_value(cached=False) == new_value
    reading = await writer.read()
    assert reading[writer.value.name]["value"] == new_value


async def test_writable_is_settable_but_not_stoppable(
    motion_node_no_re: SECoPNodeDevice,
):
    for writable in (motion_node_no_re.writer, motion_node_no_re.switch):
        assert type(writable) is SECoPWritableDevice
        for protocol in (Movable, Locatable, Checkable):
            assert isinstance(writable, protocol)
        # a Writable has no 'stop' command
        assert not isinstance(writable, Stoppable)
        assert not hasattr(writable, "stop")


async def test_writable_set_and_locate(motion_node_no_re: SECoPNodeDevice):
    writer: SECoPWritableDevice = motion_node_no_re.writer
    new_value = await writer.value.get_value(cached=False) + 2.5

    await writer.set(new_value)

    assert await writer.locate() == {"setpoint": new_value, "readback": new_value}
    assert await writer.value.get_value(cached=False) == new_value

    with pytest.raises(ValueError, match="not a valid target"):
        await writer.check_value("far")
    with pytest.raises(ValueError, match="not a valid target"):
        await writer.set("far")
    assert (await writer.locate())["setpoint"] == new_value


def test_writable_in_plan(RE, motion_node: SECoPNodeDevice):  # noqa: N803
    writer: SECoPWritableDevice = motion_node.writer
    switch: SECoPWritableDevice = motion_node.switch
    RE.waiting_hook = ProgressBarManager()

    def plan():
        yield from bps.abs_set(switch.duration, 0.1, wait=True)
        yield from bps.abs_set(switch.fail, False, wait=True)

        yield from bps.mv(writer, 7.0, switch, {"on": True, "level": 20.0})

        assert (yield from bps.rd(writer)) == 7.0
        assert (yield from bps.rd(switch)) == {"on": True, "level": 20.0}

    RE(plan())


@pytest.fixture
async def switch(motion_node_no_re: SECoPNodeDevice):
    switch: SECoPWritableDevice = motion_node_no_re.switch
    await switch.duration.set(0.1)
    await switch.fail.set(False)
    yield switch
    # leave the shared node at rest for the next test
    await switch.duration.set(0.1)
    await switch.fail.set(False)
    await switch.set({"on": False, "level": 0.0})


async def test_struct_writable_waits_while_busy(switch: SECoPWritableDevice):
    # a struct target is only changed through set(): read-only members only
    assert not hasattr(switch, "target")
    assert isinstance(switch.target_on, SignalR)
    assert not isinstance(switch.target_on, SignalW)

    await switch.duration.set(0.3)
    target = {"on": True, "level": 42.0}

    before = time.monotonic()
    await switch.set(target)
    elapsed = time.monotonic() - before

    # the module was BUSY for 'duration', although it cannot be stopped
    assert elapsed >= 0.25
    assert await _status(switch) == (IDLE, "at target")
    assert await switch.locate() == {"setpoint": target, "readback": target}
    assert await switch.value_level.get_value() == 42.0

    with pytest.raises(ValueError, match="missing struct members"):
        await switch.set({"on": True})


async def test_struct_writable_timeout(switch: SECoPWritableDevice):
    await switch.duration.set(1)

    with pytest.raises(asyncio.TimeoutError):
        await switch.set({"on": True, "level": 1.0}, timeout=0.2)


async def test_struct_writable_error_state_fails(switch: SECoPWritableDevice):
    await switch.fail.set(True)

    with pytest.raises(RuntimeError, match="ERROR/DISABLED"):
        await switch.set({"on": True, "level": 1.0})


async def test_struct_writable_with_declared_target_signal(motion_sim):
    """Device classes generated before struct/tuple targets became set()-only
    declare a write-only 'target' signal."""

    class OldSwitch(SECoPWritableDevice):
        target: A[SignalW[np.ndarray], ParameterType()]

    old_switch = OldSwitch(f"{motion_sim.uri}:switch")
    with pytest.raises(TypeError, match="regenerate the device classes"):
        await old_switch.connect()


async def test_communicator(motion_node_no_re: SECoPNodeDevice):
    comm: SECoPCommunicatorDevice = motion_node_no_re.comm

    assert await comm.communicate.execute("*IDN?") == "echo: *IDN?"


async def test_communicator_has_no_wait_for_idle(motion_node_no_re: SECoPNodeDevice):
    comm: SECoPCommunicatorDevice = motion_node_no_re.comm

    # a Communicator has no status parameter to wait on
    with pytest.raises(RuntimeError, match="wait_for_idle is not supported"):
        await comm.communicate.execute("ping", wait_for_idle=True)
