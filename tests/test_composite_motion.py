# mypy: disable-error-code="attr-defined"
"""Move/stop behaviour of SECoPCompositeMoveableDevice: Drivable modules whose
'value' and 'target' are a struct (``struct_mover``) or a tuple
(``tuple_mover``), see frappy_modules/motion.py."""

import asyncio
import time

import bluesky.plan_stubs as bps
import numpy as np
import pytest
from bluesky.protocols import Checkable, Locatable, Movable, Stoppable
from bluesky.utils import ProgressBarManager
from frappy.client import get_datatype
from frappy.datatypes import FloatRange, StructOf
from ophyd_async.core import SignalR, SignalW, StandardMovable

from secop_ophyd.SECoPDevices import (
    BUSY,
    ERROR,
    IDLE,
    SECoPCompositeMoveableDevice,
    SECoPDrivableBase,
    SECoPMoveableDevice,
    SECoPNodeDevice,
    _python_to_secop,
)
from secop_ophyd.SECoPDevices import _status_code as _code_of
from secop_ophyd.SECoPDevices import (
    class_from_interface,
)

SPEED = 20.0  # mm/s

FLOAT = {"datatype": FloatRange()}
STRUCT = {"datatype": StructOf(x=FloatRange(), y=FloatRange())}


@pytest.fixture
async def struct_mover(motion_node_no_re: SECoPNodeDevice):
    mover: SECoPCompositeMoveableDevice = motion_node_no_re.struct_mover
    await mover.speed.set(SPEED)
    await mover.error_limit.set(1000)
    yield mover
    # leave the shared node at rest for the next test
    await mover.error_limit.set(1000)
    await mover.stop()


@pytest.fixture
async def tuple_mover(motion_node_no_re: SECoPNodeDevice):
    mover: SECoPCompositeMoveableDevice = motion_node_no_re.tuple_mover
    await mover.speed.set(SPEED)
    yield mover
    await mover.stop()


async def _status_code(mover: SECoPCompositeMoveableDevice) -> int:
    code_name = await mover.status_0.get_value(cached=False)
    return _code_of(mover.status_0, code_name)


async def _xy(mover: SECoPCompositeMoveableDevice) -> tuple[float, float]:
    return (
        await mover.value_x.get_value(cached=False),
        await mover.value_y.get_value(cached=False),
    )


@pytest.mark.parametrize(
    "parameters, expected",
    [
        ({"value": FLOAT, "target": FLOAT}, SECoPMoveableDevice),
        ({"value": STRUCT, "target": STRUCT}, SECoPCompositeMoveableDevice),
        ({"value": STRUCT, "target": FLOAT}, SECoPCompositeMoveableDevice),
        ({"value": FLOAT, "target": STRUCT}, SECoPCompositeMoveableDevice),
    ],
)
def test_drivable_class_depends_on_datatypes(parameters, expected):
    properties = {"interface_classes": ["Drivable"]}

    assert class_from_interface(properties, parameters) is expected
    # without the parameters only the interface class is known
    assert class_from_interface(properties) is SECoPMoveableDevice


async def test_composite_mover_classes(motion_node_no_re: SECoPNodeDevice):
    node = motion_node_no_re

    assert type(node.mover) is SECoPMoveableDevice
    assert type(node.struct_mover) is SECoPCompositeMoveableDevice
    assert type(node.tuple_mover) is SECoPCompositeMoveableDevice

    for mover in (node.mover, node.struct_mover, node.tuple_mover):
        assert isinstance(mover, SECoPDrivableBase)
        for protocol in (Movable, Locatable, Checkable, Stoppable):
            assert isinstance(mover, protocol)

    assert not isinstance(node.struct_mover, StandardMovable)


async def test_target_is_only_changed_through_set(
    struct_mover: SECoPCompositeMoveableDevice,
    tuple_mover: SECoPCompositeMoveableDevice,
):
    for mover, members in (
        (struct_mover, ("x", "y", "mode")),
        (tuple_mover, ("0", "1")),
    ):
        # no whole-parameter signals, only read-only members
        assert not hasattr(mover, "target")
        assert not hasattr(mover, "value")
        for param in ("target", "value"):
            for member in members:
                signal = getattr(mover, f"{param}_{member}")
                assert isinstance(signal, SignalR)
                assert not isinstance(signal, SignalW)


async def test_struct_move_waits_until_idle(
    struct_mover: SECoPCompositeMoveableDevice,
):
    x, y = await _xy(struct_mover)
    distance = 4.0
    target = {"x": x + distance, "y": y + distance / 2, "mode": "strict"}

    before = time.monotonic()
    await struct_mover.set(target)
    elapsed = time.monotonic() - before

    assert elapsed >= 0.8 * distance / SPEED
    assert IDLE <= await _status_code(struct_mover) < BUSY
    assert await _xy(struct_mover) == pytest.approx((target["x"], target["y"]))
    assert await struct_mover.target_mode.get_value() == "strict"


async def test_struct_locate_matches_member_signals(
    struct_mover: SECoPCompositeMoveableDevice,
):
    x, y = await _xy(struct_mover)
    target = {"x": x + 1, "y": y - 1, "mode": "relaxed"}
    await struct_mover.set(target)

    location = await struct_mover.locate()

    assert location["setpoint"] == pytest.approx(target)
    assert location["readback"] == pytest.approx(target)

    # read() has the same values, one data key per member
    reading = await struct_mover.read()
    for param, position in (
        ("target", location["setpoint"]),
        ("value", location["readback"]),
    ):
        for member, member_value in position.items():
            signal = getattr(struct_mover, f"{param}_{member}")
            assert reading[signal.name]["value"] == member_value


async def test_tuple_move_and_locate(tuple_mover: SECoPCompositeMoveableDevice):
    start = (await tuple_mover.locate())["readback"]
    assert isinstance(start, tuple)
    target = (start[0] + 2, start[1] - 1)

    # a list is accepted as well
    await tuple_mover.set(list(target))

    location = await tuple_mover.locate()
    assert location["setpoint"] == pytest.approx(target)
    assert location["readback"] == pytest.approx(target)
    assert await tuple_mover.value_0.get_value() == pytest.approx(target[0])
    assert await tuple_mover.target_1.get_value() == pytest.approx(target[1])


@pytest.mark.parametrize(
    "bad_target, message",
    [
        ({"x": 1.0, "y": 2.0}, "missing struct members"),
        ({"x": 1.0, "y": 2.0, "mode": None}, "missing struct members"),
        ({"x": 1.0, "y": 2.0, "mode": "strict", "z": 3.0}, "superfluous"),
        ({"x": "left", "y": 2.0, "mode": "strict"}, "x"),
        ({"x": 1.0, "y": 2.0, "mode": "unknown"}, "mode"),
        (3.0, "expected a mapping"),
        ((1.0, 2.0, "strict"), "expected a mapping"),
    ],
)
async def test_invalid_struct_target_is_rejected(
    struct_mover: SECoPCompositeMoveableDevice, bad_target, message
):
    before = await struct_mover.locate()

    with pytest.raises(ValueError, match=message):
        await struct_mover.check_value(bad_target)
    with pytest.raises(ValueError, match=message):
        await struct_mover.set(bad_target)

    # nothing was sent to the node
    assert await struct_mover.locate() == before
    assert IDLE <= await _status_code(struct_mover) < BUSY


@pytest.mark.parametrize("bad_target", [(1.0,), (1.0, 2.0, 3.0), "ab", {"0": 1.0}])
async def test_invalid_tuple_target_is_rejected(
    tuple_mover: SECoPCompositeMoveableDevice, bad_target
):
    with pytest.raises(ValueError, match="not a valid target"):
        await tuple_mover.set(bad_target)


async def test_tuple_target_from_numpy_array(
    tuple_mover: SECoPCompositeMoveableDevice,
):
    await tuple_mover.set(np.array([1.5, 0.5]))
    assert (await tuple_mover.locate())["setpoint"] == pytest.approx((1.5, 0.5))
    await tuple_mover.set((0.0, 0.0))


def test_optional_struct_members_may_be_left_out():
    # as built by the client from a node description
    datatype = get_datatype(
        {
            "type": "struct",
            "members": {"x": {"type": "double"}, "y": {"type": "double"}},
        }
    )

    with pytest.raises(ValueError, match=r"missing struct members \['y'\]"):
        _python_to_secop(datatype, {"x": 1})

    assert _python_to_secop(datatype, {"x": 1}, optional=["y"]) == {"x": 1.0}


async def test_move_timeout(struct_mover: SECoPCompositeMoveableDevice):
    await struct_mover.speed.set(1)
    x, y = await _xy(struct_mover)

    # the message names the status signal and its last value
    with pytest.raises(asyncio.TimeoutError, match="status_0"):
        await struct_mover.set({"x": x + 100, "y": y, "mode": "strict"}, timeout=0.2)


@pytest.mark.parametrize("module", ["struct_mover", "mover"])
async def test_move_does_not_end_on_a_stale_status(
    motion_node_no_re: SECoPNodeDevice, monkeypatch, module
):
    """A node may send its BUSY update after replying to the target write. A
    move must then not end on the pre-move IDLE status still in the cache."""
    mover = getattr(motion_node_no_re, module)
    await mover.speed.set(SPEED)
    client = mover._client
    set_parameter = client.set_parameter

    async def set_parameter_then_stale_status(module, parameter, value):
        stale = client.cache[(module, "status")]
        result = await set_parameter(module, parameter, value)
        if parameter == "target":
            client.cache[(module, "status")] = stale
        return result

    monkeypatch.setattr(client, "set_parameter", set_parameter_then_stale_status)

    distance = 4.0
    if module == "struct_mover":
        x, y = await _xy(mover)
        target: object = {"x": x + distance, "y": y, "mode": "strict"}
    else:
        target = await mover.value.get_value(cached=False) + distance

    before = time.monotonic()
    await mover.set(target)
    assert time.monotonic() - before >= 0.8 * distance / SPEED
    assert IDLE <= await _status_code(mover) < BUSY


async def test_timeout_covers_writing_the_target(
    struct_mover: SECoPCompositeMoveableDevice, monkeypatch
):
    async def slow_set_parameter(*args, **kwargs):
        await asyncio.sleep(5)

    monkeypatch.setattr(struct_mover._client, "set_parameter", slow_set_parameter)
    x, y = await _xy(struct_mover)

    before = time.monotonic()
    with pytest.raises(asyncio.TimeoutError, match="writing the target"):
        await struct_mover.set({"x": x + 1, "y": y, "mode": "strict"}, timeout=0.2)
    assert time.monotonic() - before < 1


async def test_failed_stop_command_does_not_end_the_move(
    struct_mover: SECoPCompositeMoveableDevice, monkeypatch
):
    x, y = await _xy(struct_mover)
    target = {"x": x + 2, "y": y, "mode": "strict"}
    stat = struct_mover.set(target)
    await asyncio.sleep(0.02)

    async def failing_exec_command(*args, **kwargs):
        raise ConnectionError("node gone")

    with monkeypatch.context() as patch:
        patch.setattr(struct_mover._client, "exec_command", failing_exec_command)
        with pytest.raises(ConnectionError):
            await struct_mover.stop()

    # not stopped, so the move ends normally
    await stat
    assert stat.success
    assert await _xy(struct_mover) == pytest.approx((target["x"], y))


async def test_stop_success(struct_mover: SECoPCompositeMoveableDevice):
    await struct_mover.speed.set(1)
    x, y = await _xy(struct_mover)

    stat = struct_mover.set({"x": x + 100, "y": y, "mode": "strict"})
    await asyncio.sleep(0.2)

    # success=True: the halted move still counts as successfully completed
    await struct_mover.stop(success=True)
    await stat

    assert stat.success
    assert (await _xy(struct_mover))[0] < x + 100
    assert IDLE <= await _status_code(struct_mover) < BUSY


async def test_stop_failure(struct_mover: SECoPCompositeMoveableDevice):
    await struct_mover.speed.set(1)
    x, y = await _xy(struct_mover)

    stat = struct_mover.set({"x": x + 100, "y": y, "mode": "strict"})
    await asyncio.sleep(0.2)

    # success=False: the halted move is reported as failed
    await struct_mover.stop(success=False)

    with pytest.raises(RuntimeError, match="was stopped"):
        await stat
    assert not stat.success

    # the next move is not affected by the earlier stop
    await struct_mover.speed.set(SPEED)
    await struct_mover.set({"x": x, "y": y, "mode": "strict"})


async def test_move_into_error_state_fails(
    struct_mover: SECoPCompositeMoveableDevice,
):
    x, y = await _xy(struct_mover)
    await struct_mover.error_limit.set(max(abs(x), abs(y)) + 0.5)

    with pytest.raises(RuntimeError, match="ERROR/DISABLED"):
        await struct_mover.set(
            {"x": x + 5 if x >= 0 else x - 5, "y": y, "mode": "strict"}
        )

    assert await _status_code(struct_mover) >= ERROR


async def test_subscribe_reading_names_the_member_signals(
    struct_mover: SECoPCompositeMoveableDevice,
):
    with pytest.raises(NotImplementedError, match="value_x"):
        struct_mover.subscribe_reading(lambda reading: None)
    with pytest.raises(NotImplementedError, match="value_x"):
        struct_mover.clear_sub(lambda reading: None)


def test_plan_with_progress_bar(RE, motion_node: SECoPNodeDevice):  # noqa: N803
    """bluesky's progress bar computes distances from the positions a status
    reports, which is not possible for a struct: the move must not report
    any."""
    struct_mover: SECoPCompositeMoveableDevice = motion_node.struct_mover
    tuple_mover: SECoPCompositeMoveableDevice = motion_node.tuple_mover
    RE.waiting_hook = ProgressBarManager()

    def plan():
        yield from bps.abs_set(struct_mover.speed, SPEED, wait=True)
        yield from bps.abs_set(struct_mover.error_limit, 1000, wait=True)
        yield from bps.abs_set(tuple_mover.speed, SPEED, wait=True)

        start = yield from bps.rd(struct_mover)
        target = {"x": start["x"] + 2, "y": start["y"] + 1, "mode": "strict"}

        yield from bps.mv(struct_mover, target, tuple_mover, (1.0, 2.0))

        # for a Locatable, rd() is the readback of locate()
        assert (yield from bps.rd(struct_mover)) == pytest.approx(target)
        location = yield from bps.locate(tuple_mover)
        assert location[tuple_mover.name]["setpoint"] == pytest.approx((1.0, 2.0))

        yield from bps.mv(struct_mover, start, tuple_mover, (0.0, 0.0))

    RE(plan())


async def test_wrong_drivable_class_is_rejected(motion_sim):
    scalar: SECoPMoveableDevice = SECoPMoveableDevice(f"{motion_sim.uri}:struct_mover")
    with pytest.raises(TypeError, match="use SECoPCompositeMoveableDevice"):
        await scalar.connect()

    composite: SECoPCompositeMoveableDevice = SECoPCompositeMoveableDevice(
        f"{motion_sim.uri}:mover"
    )
    with pytest.raises(TypeError, match="use SECoPMoveableDevice"):
        await composite.connect()


async def test_composite_mover_needs_a_stop_command(motion_sim):
    # 'switch' is a Writable with a struct target: it cannot be stopped
    mover: SECoPCompositeMoveableDevice = SECoPCompositeMoveableDevice(
        f"{motion_sim.uri}:switch"
    )
    with pytest.raises(TypeError, match="no 'stop' command"):
        await mover.connect()


async def test_directly_instantiated_composite_mover(motion_sim):
    mover: SECoPCompositeMoveableDevice = SECoPCompositeMoveableDevice(
        f"{motion_sim.uri}:tuple_mover"
    )
    await mover.connect()
    await mover.speed.set(SPEED)

    await mover.set((1.0, 1.0))
    assert (await mover.locate())["readback"] == pytest.approx((1.0, 1.0))
    await mover.set((0.0, 0.0))
