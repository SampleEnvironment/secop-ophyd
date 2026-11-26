# mypy: disable-error-code="attr-defined"
"""Move/stop behaviour of SECoPMoveableDevice, run against the fast
``FastMover`` test module so each move takes a fraction of a second."""

import asyncio
import time

import bluesky.plan_stubs as bps
import numpy as np
import pytest
from ophyd_async.core import Command, TriggerableCommand

from secop_ophyd.devices import SECoPMoveableDevice, SECoPNodeDevice
from secop_ophyd.devices.readable import BUSY, ERROR, IDLE
from secop_ophyd.devices.readable import _status_code as _code_of

SPEED = 20.0  # mm/s


@pytest.fixture
async def mover(motion_node_no_re: SECoPNodeDevice):
    mover: SECoPMoveableDevice = motion_node_no_re.mover
    await mover.speed.set(SPEED)
    await mover.error_limit.set(1000)
    yield mover
    # leave the shared node at rest for the next test
    await mover.error_limit.set(1000)
    await mover.stop()


async def _position(mover: SECoPMoveableDevice) -> float:
    return await mover.value.get_value(cached=False)


async def _status_code(mover: SECoPMoveableDevice) -> int:
    # status_0 is the status code, resolved to its enum member name
    code_name = await mover.status_0.get_value(cached=False)
    return _code_of(mover.status_0, code_name)


async def test_mover_is_moveable_device(mover: SECoPMoveableDevice):
    assert isinstance(mover, SECoPMoveableDevice)
    # "stop" is the native Stoppable.stop(), not a raw command device
    assert not isinstance(mover.stop, (Command, TriggerableCommand))


async def test_move_waits_until_idle(mover: SECoPMoveableDevice):
    start = await _position(mover)
    distance = 4.0
    new_target = start + distance

    before = time.monotonic()
    await mover.set(new_target)
    elapsed = time.monotonic() - before

    assert elapsed >= 0.8 * distance / SPEED
    assert np.isclose(await _position(mover), new_target)
    assert IDLE <= await _status_code(mover) < BUSY


async def test_locate(mover: SECoPMoveableDevice):
    target = await _position(mover) + 1
    await mover.set(target)

    location = await mover.locate()

    assert np.isclose(location["setpoint"], target)
    assert np.isclose(location["readback"], target)


async def test_move_timeout(mover: SECoPMoveableDevice):
    await mover.speed.set(1)
    start = await _position(mover)

    with pytest.raises(asyncio.TimeoutError):
        await mover.set(start + 100, timeout=0.2)


async def test_stop_success(mover: SECoPMoveableDevice):
    await mover.speed.set(1)
    start = await _position(mover)

    stat = mover.set(start + 100)
    await asyncio.sleep(0.2)

    # success=True: the halted move still counts as successfully completed
    await mover.stop(success=True)
    await stat

    assert stat.success
    assert await _position(mover) < start + 100
    assert IDLE <= await _status_code(mover) < BUSY


async def test_stop_failure(mover: SECoPMoveableDevice):
    await mover.speed.set(1)
    start = await _position(mover)

    stat = mover.set(start + 100)
    await asyncio.sleep(0.2)

    # success=False: the halted move is reported as failed
    await mover.stop(success=False)

    with pytest.raises(RuntimeError):
        await stat
    assert not stat.success


async def test_move_into_error_state_fails(mover: SECoPMoveableDevice):
    start = await _position(mover)
    await mover.error_limit.set(abs(start) + 0.5)

    with pytest.raises(RuntimeError, match="ERROR/DISABLED"):
        await mover.set(start + 5 if start >= 0 else start - 5)

    assert await _status_code(mover) >= ERROR


async def test_wait_for_idle(mover: SECoPMoveableDevice):
    start = await _position(mover)

    await mover.target.set(start + 2)
    assert BUSY <= await _status_code(mover) < ERROR

    await asyncio.wait_for(mover.wait_for_idle(), timeout=5)

    assert IDLE <= await _status_code(mover) < BUSY
    assert np.isclose(await _position(mover), start + 2)


def test_abs_set_wait_behaviour(RE, motion_node: SECoPNodeDevice):  # noqa: N803
    mover: SECoPMoveableDevice = motion_node.mover
    distance = 4.0

    def plan():
        yield from bps.abs_set(mover.speed, SPEED, wait=True)
        yield from bps.abs_set(mover.error_limit, 1000, wait=True)
        start = yield from bps.rd(mover)

        before = time.monotonic()
        yield from bps.abs_set(mover, start + distance, wait=True)
        assert time.monotonic() - before >= 0.8 * distance / SPEED

        before = time.monotonic()
        yield from bps.abs_set(mover, start, wait=False, group="move")
        assert time.monotonic() - before < distance / SPEED
        yield from bps.wait(group="move")

        assert np.isclose((yield from bps.rd(mover)), start)

    RE(plan())


async def test_subscribe_reading(mover: SECoPMoveableDevice):
    readings: list[float] = []

    def callback(reading):
        readings.append(reading[mover.value.name]["value"])

    mover.subscribe_reading(callback)
    try:
        start = await _position(mover)
        await mover.set(start + 2)
        await asyncio.sleep(0.05)
    finally:
        mover.clear_sub(callback)

    # the initial reading plus updates while moving
    assert len(readings) > 2
    assert np.isclose(readings[-1], start + 2)

    n_readings = len(readings)
    await mover.set(start)
    await asyncio.sleep(0.05)
    assert len(readings) == n_readings


def test_observe_status_change(RE, motion_node: SECoPNodeDevice):  # noqa: N803
    mover: SECoPMoveableDevice = motion_node.mover

    def plan():
        yield from bps.abs_set(mover.speed, SPEED, wait=True)
        start = yield from bps.rd(mover)

        yield from bps.abs_set(mover.target, start + 2, wait=True)
        status = yield from bps.rd(mover.status_0)
        assert _code_of(mover.status_0, status) == BUSY

        # blocks until the module leaves BUSY
        yield from mover.observe_status_change(BUSY)

        status = yield from bps.rd(mover.status_0)
        assert IDLE <= _code_of(mover.status_0, status) < BUSY
        assert np.isclose((yield from bps.rd(mover)), start + 2)

    RE(plan())
