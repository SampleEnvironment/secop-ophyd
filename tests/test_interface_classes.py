# mypy: disable-error-code="attr-defined"
"""Device classes for each SECoP interface class, run against the fast modules
of the motion test node (see frappy_modules/motion.py)."""

import asyncio

import pytest
from ophyd_async.core import Command, TriggerableCommand

from secop_ophyd.SECoPDevices import (
    BUSY,
    ERROR,
    IDLE,
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


async def test_writable(motion_node_no_re: SECoPNodeDevice):
    writer: SECoPWritableDevice = motion_node_no_re.writer
    new_value = await writer.value.get_value(cached=False) + 1.5

    await writer.target.set(new_value)

    assert await writer.value.get_value(cached=False) == new_value
    reading = await writer.read()
    assert reading[writer.value.name]["value"] == new_value


async def test_communicator(motion_node_no_re: SECoPNodeDevice):
    comm: SECoPCommunicatorDevice = motion_node_no_re.comm

    assert await comm.communicate.execute("*IDN?") == "echo: *IDN?"


async def test_communicator_has_no_wait_for_idle(motion_node_no_re: SECoPNodeDevice):
    comm: SECoPCommunicatorDevice = motion_node_no_re.comm

    # a Communicator has no status parameter to wait on
    with pytest.raises(RuntimeError, match="wait_for_idle is not supported"):
        await comm.communicate.execute("ping", wait_for_idle=True)
