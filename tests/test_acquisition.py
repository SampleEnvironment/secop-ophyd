# mypy: disable-error-code="attr-defined"
"""SECoP "Acquisition" modules (and the legacy "Triggerable"): one cycle per
trigger(), flying with prepare/kickoff/complete, and collecting the updates of
'value' (or the result of 'get_data') of a fly scan."""

import asyncio

import bluesky.plan_stubs as bps
import numpy as np
import pytest
from bluesky.plans import count
from bluesky.protocols import EventPageCollectable, Flyable, Pausable, Preparable
from ophyd_async.core import Device, TriggerableCommand, set_mock_value

from secop_ophyd.AsyncFrappyClient import AsyncSecopClient
from secop_ophyd.codegen import GenNodeCode
from secop_ophyd.devices import (
    AcquisitionGoal,
    SECoPAcquisitionChannelDevice,
    SECoPAcquisitionControllerDevice,
    SECoPAcquisitionDevice,
    SECoPNodeDevice,
    SECoPTriggerableDevice,
)
from secop_ophyd.devices.acquisition.channel import _ReadingBuffer
from secop_ophyd.devices.connector import channel_attr_name
from secop_ophyd.devices.readable import IDLE, _status_code
from tests.util import import_generated

# defaults of the test node's FastAcquisition modules
LIMIT = 5


def _counts(values: list, last: int | None = None) -> bool:
    """Whether ``values`` are the counts of one cycle: 0 (the reset at 'go',
    only sent if the previous cycle did not end at 0 too), 1, 2, ..."""
    first = 0 if values[:1] == [0] else 1
    expected = list(range(first, (values[-1] if last is None else last) + 1))
    return values == expected


async def _status_code_of(dev) -> int:
    return _status_code(dev.status_0, await dev.status_0.get_value(cached=False))


async def _reset(dev) -> None:
    await dev.stop()
    await dev.fail.set(False)
    await dev.goal_enable.set(False)
    await dev.limit.set(LIMIT)
    await dev.tick.set(0.01)


@pytest.fixture
async def counter(acquisition_node_no_re: SECoPNodeDevice):
    counter: SECoPAcquisitionDevice = acquisition_node_no_re.counter
    await _reset(counter)
    yield counter
    # leave the shared node at rest for the next test
    await _reset(counter)


@pytest.fixture
async def spectrum(acquisition_node_no_re: SECoPNodeDevice):
    spectrum: SECoPAcquisitionDevice = acquisition_node_no_re.spectrum
    await _reset(spectrum)
    yield spectrum
    await _reset(spectrum)


async def _fly(dev, goal: AcquisitionGoal | None = None) -> list[dict]:
    """prepare -> kickoff -> complete -> collect, outside a RunEngine."""
    await dev.stage()
    try:
        await dev.prepare(goal)
        await dev.kickoff()
        await asyncio.wait_for(dev.complete(), 5)
        return [page async for page in dev.collect_pages()]
    finally:
        await dev.unstage()


async def test_acquisition_interface_class(acquisition_node_no_re, motion_node_no_re):
    counter = acquisition_node_no_re.counter
    assert type(counter) is SECoPAcquisitionDevice
    assert type(acquisition_node_no_re.spectrum) is SECoPAcquisitionDevice

    # the legacy interface class "Triggerable" is an Acquisition
    assert SECoPTriggerableDevice is SECoPAcquisitionDevice
    assert type(motion_node_no_re.detector) is SECoPAcquisitionDevice

    for protocol in (Flyable, Preparable, EventPageCollectable, Pausable):
        assert isinstance(counter, protocol)

    assert isinstance(counter.go, TriggerableCommand)
    # SECoP 'prepare' must not shadow bluesky's prepare(): it gets an underscore
    assert isinstance(counter.prepare_, TriggerableCommand)
    assert not isinstance(counter.prepare, TriggerableCommand)
    # 'stop' is the device's Stoppable.stop()
    assert not isinstance(counter.stop, TriggerableCommand)


async def test_trigger_runs_one_cycle(counter: SECoPAcquisitionDevice):
    await counter.limit.set(3)

    await asyncio.wait_for(counter.trigger(), 5)

    assert await _status_code_of(counter) == IDLE
    reading = await counter.read()
    assert reading[counter.value.name]["value"] == 3


async def test_trigger_failed_cycle(counter: SECoPAcquisitionDevice):
    await counter.fail.set(True)

    with pytest.raises(RuntimeError, match="acquisition failed"):
        await asyncio.wait_for(counter.trigger(), 5)


async def test_legacy_triggerable_trigger(motion_node_no_re):
    detector = motion_node_no_re.detector
    await detector.duration.set(0.05)
    count_before = await detector.value.get_value(cached=False)

    await asyncio.wait_for(detector.trigger(), 5)

    assert await detector.value.get_value() == count_before + 1


def test_count_plan(RE, acquisition_node):  # noqa: N803
    events = []
    RE(
        count([acquisition_node.counter], num=2),
        {"event": lambda _, e: events.append(e)},
    )

    assert [event["data"]["acq-counter-value"] for event in events] == [LIMIT] * 2


async def test_fly_collects_every_update(counter: SECoPAcquisitionDevice):
    pages = await _fly(counter, AcquisitionGoal(goal=8, enable=True))

    assert await counter.goal.get_value() == 8
    (page,) = pages
    key = counter.value.name
    # every count of the cycle
    assert _counts(page["data"][key], last=8)
    timestamps = page["timestamps"][key]
    assert timestamps == sorted(timestamps)
    assert page["time"] == timestamps

    # collecting hands the data over: nothing is left
    assert [page async for page in counter.collect_pages()] == []


async def test_fly_describe_collect(counter: SECoPAcquisitionDevice):
    description = await counter.describe_collect()

    assert list(description) == [counter.name]
    assert description[counter.name] == await counter.value.describe()


async def test_fly_collect_while_running(counter: SECoPAcquisitionDevice):
    await counter.limit.set(30)
    await counter.stage()
    try:
        await counter.prepare(None)
        await counter.kickoff()
        await asyncio.sleep(0.1)
        early = [page async for page in counter.collect_pages()]
        await asyncio.wait_for(counter.complete(), 5)
        late = [page async for page in counter.collect_pages()]
    finally:
        await counter.unstage()

    key = counter.value.name
    values = [v for page in early + late for v in page["data"][key]]
    assert early and late
    assert _counts(values, last=30)


async def test_fly_stop(counter: SECoPAcquisitionDevice):
    await counter.limit.set(1000)
    await counter.stage()
    try:
        await counter.prepare(None)
        await counter.kickoff()
        await asyncio.sleep(0.05)
        await counter.stop()
        await asyncio.wait_for(counter.complete(), 5)
        (page,) = [page async for page in counter.collect_pages()]
    finally:
        await counter.unstage()

    values = page["data"][counter.value.name]
    assert 0 < values[-1] < 1000
    assert _counts(values)


async def test_fly_needs_prepare(counter: SECoPAcquisitionDevice):
    await counter.stage()
    try:
        with pytest.raises(RuntimeError, match="prepare"):
            await counter.kickoff()
    finally:
        await counter.unstage()


async def test_unstage_stops_running_cycle(counter: SECoPAcquisitionDevice):
    await counter.limit.set(1000)
    await counter.stage()
    await counter.prepare(None)
    await counter.kickoff()

    await counter.unstage()

    assert await _status_code_of(counter) == IDLE
    assert (await counter.status_1.get_value(cached=False)) == "stopped"


async def test_pause_stops_running_cycle(counter: SECoPAcquisitionDevice):
    await counter.limit.set(1000)
    status = counter.trigger()
    await asyncio.sleep(0.05)

    await counter.pause()
    await asyncio.wait_for(status, 5)
    await counter.resume()

    assert (await counter.status_1.get_value(cached=False)) == "stopped"


async def test_pause_leaves_other_cycles_alone(counter: SECoPAcquisitionDevice):
    # a cycle this device did not start is not stopped
    await counter.limit.set(1000)
    await counter.go.execute()

    await counter.pause()

    assert (await counter.status_1.get_value(cached=False)) == "acquiring"


async def test_prepare_errors(counter: SECoPAcquisitionDevice, motion_node_no_re):
    # the legacy Triggerable has neither goal nor goal_enable
    detector = motion_node_no_re.detector
    with pytest.raises(ValueError, match="no writable 'goal'"):
        await detector.prepare(AcquisitionGoal(goal=1))
    with pytest.raises(ValueError, match="no writable 'goal_enable'"):
        await detector.prepare(AcquisitionGoal(enable=False))

    # an always active goal needs no goal_enable
    await detector.prepare(AcquisitionGoal(enable=True))


async def test_fly_get_data(spectrum: SECoPAcquisitionDevice):
    pages = await _fly(spectrum, AcquisitionGoal(goal=3, enable=True))

    key = f"{spectrum.name}-get_data"
    (page,) = pages
    (data,) = page["data"][key]
    np.testing.assert_array_equal(data, [3, 3, 3, 3])

    # the command itself returns a numpy array, like a parameter
    result = await spectrum.get_data.execute()
    assert isinstance(result, np.ndarray)
    np.testing.assert_array_equal(result, [3, 3, 3, 3])
    # with get_data, nothing is buffered
    assert all(len(buffer) == 0 for buffer in spectrum._buffers.values())

    # the result of a cycle is handed over once
    assert [page async for page in spectrum.collect_pages()] == []

    description = await spectrum.describe_collect()
    datakey = description[spectrum.name][key]
    assert datakey["shape"] == [4]
    assert datakey["dtype"] == "array"
    assert datakey["source"].endswith(":spectrum:get_data")


def test_fly_plan(RE, acquisition_node):  # noqa: N803
    counter = acquisition_node.counter
    docs: dict[str, list] = {"descriptor": [], "event_page": []}

    def fly():
        yield from bps.open_run()
        yield from bps.stage(counter)
        yield from bps.prepare(counter, AcquisitionGoal(goal=4, enable=True), wait=True)
        yield from bps.kickoff(counter, wait=True)
        yield from bps.complete(counter, wait=True)
        yield from bps.collect(counter)
        yield from bps.unstage(counter)
        yield from bps.close_run()

    try:
        RE(fly(), lambda name, doc: docs.setdefault(name, []).append(doc))
    finally:
        RE(bps.mv(counter.goal_enable, False))

    (descriptor,) = docs["descriptor"]
    assert descriptor["name"] == "acq-counter"
    (page,) = docs["event_page"]
    assert _counts(page["data"]["acq-counter-value"], last=4)


@pytest.fixture
async def control(acquisition_node_no_re: SECoPNodeDevice):
    control: SECoPAcquisitionControllerDevice = acquisition_node_no_re.control
    await _reset_controller(control)
    yield control
    await _reset_controller(control)


async def _reset_controller(control) -> None:
    await control.stop()
    await control.fail.set(False)
    await control.limit.set(LIMIT)
    for channel in control.channels.values():
        await channel.goal_enable.set(False)


async def test_controller_and_channels(acquisition_node_no_re):
    node = acquisition_node_no_re
    control = node.control
    assert type(control) is SECoPAcquisitionControllerDevice
    for protocol in (Flyable, Preparable, EventPageCollectable, Pausable):
        assert isinstance(control, protocol)

    # the channels are children of their controller, named by role
    assert set(control.channels) == {"t", "monitor"}
    assert control.channels["t"] is control.t
    assert type(control.t) is SECoPAcquisitionChannelDevice
    assert control.t._module == "timer"
    assert control.t.name == f"{node.name}-control-t"
    assert control.channels["monitor"]._module == "monitor"
    assert node.spectrum_control.channels["spectrum"]._module == "spectrum_channel"

    # ... and no module devices of the node
    children = dict(node.children())
    for channel_module in ("timer", "monitor", "spectrum_channel"):
        assert channel_module not in children
    # a channel of no controller stays one
    assert type(node.lone_channel) is SECoPAcquisitionChannelDevice


async def test_controller_trigger(control: SECoPAcquisitionControllerDevice):
    await control.limit.set(3)

    await asyncio.wait_for(control.trigger(), 5)

    reading = await control.read()
    assert {key: r["value"] for key, r in reading.items()} == {
        control.t.value.name: 3,
        control.monitor.value.name: 3,
    }


async def test_controller_trigger_failed(control: SECoPAcquisitionControllerDevice):
    await control.fail.set(True)

    with pytest.raises(RuntimeError, match="acquisition failed"):
        await asyncio.wait_for(control.trigger(), 5)


async def test_controller_fly(control: SECoPAcquisitionControllerDevice):
    await control.stage()
    try:
        await control.prepare({"t": AcquisitionGoal(goal=4, enable=True)})
        assert await control.t.goal.get_value() == 4
        await control.kickoff()
        await asyncio.wait_for(control.complete(), 5)
        pages = [page async for page in control.collect_pages()]
    finally:
        await control.unstage()

    description = await control.describe_collect()
    assert set(description) == {control.t.name, control.monitor.name}
    by_key = {key: values for page in pages for key, values in page["data"].items()}
    assert set(by_key) == {control.t.value.name, control.monitor.value.name}
    for values in by_key.values():
        assert _counts(values, last=4)


async def test_controller_prepare_unknown_role(
    control: SECoPAcquisitionControllerDevice,
):
    with pytest.raises(ValueError, match=r"no channels with the roles \['x'\]"):
        await control.prepare({"x": AcquisitionGoal(goal=1)})


async def test_controller_unstage_stops_running_cycle(
    control: SECoPAcquisitionControllerDevice,
):
    await control.limit.set(1000)
    await control.stage()
    await control.prepare(None)
    await control.kickoff()

    await control.unstage()

    assert (await control.status_1.get_value(cached=False)) == "stopped"


def test_channel_attr_name():
    class Controller(Device):
        def __init__(self):
            self.status = 1
            super().__init__(name="ctrl")

    controller = Controller()
    assert channel_attr_name("t", controller, set()) == "t"
    assert channel_attr_name("1st channel", controller, set()) == "_1st_channel"
    # an annotated channel keeps its name
    assert channel_attr_name("status", controller, {"status"}) == "status"
    # clashes with an attribute or a bluesky protocol method get underscores
    with pytest.warns(UserWarning, match="'status_'"):
        assert channel_attr_name("status", controller, set()) == "status_"
    with pytest.warns(UserWarning, match="'read_'"):
        assert channel_attr_name("read", controller, set()) == "read_"


async def test_generated_acquisition(acquisition_sim, tmp_path, logger):
    client = AsyncSecopClient(
        host="localhost", port=str(acquisition_sim.port), log=logger
    )
    await client.connect(3)
    try:
        description = (await client.request("describe"))[2]
    finally:
        await client.disconnect(True)

    gen_code = GenNodeCode(path=str(tmp_path), log=logger)
    gen_code.from_json_describe(description)
    gen_code.write_gen_node_class_file()
    (node_cls,) = gen_code.node_classes
    source = (tmp_path / f"{node_cls.name}.py").read_text()

    assert "(SECoPAcquisitionDevice)" in source
    # SECoP 'prepare' must not shadow bluesky's prepare()
    assert "prepare_: TriggerableCommand" in source
    # numeric array results are numpy arrays
    assert "get_data: Command[[], ndarray]" in source
    # a class of several modules lists the description of each
    shared = source[source.index("class FastChannel(") :]
    shared = shared[: shared.index("# Module Properties")]
    assert "Class of the modules:" in shared
    assert "- timer: time channel of 'control'" in shared
    assert "- monitor: monitor channel of 'control'" in shared
    assert "- lone_channel: channel of no controller" in shared
    # CamelCase implementation names keep their case
    assert "class FastAcquisition(SECoPAcquisitionDevice):" in source
    # the channels of a controller are its children, named by role
    assert "(SECoPAcquisitionControllerDevice)" in source
    assert 't: A[FastChannel, ChannelT("t", "timer")]' in source
    assert 'spectrum: A[SpectrumChannel, ChannelT("spectrum", "spectrum_channel")]' in (
        source
    )
    node_source = source[source.index(f"class {node_cls.name}(") :]
    assert "    timer:" not in node_source
    assert "    lone_channel: FastChannel" in node_source

    generated = getattr(import_generated(tmp_path, node_cls.name), node_cls.name)

    # mock mode: mocked modules start IDLE, so a cycle ends at once
    node = generated("localhost:1", name="acq")
    await asyncio.wait_for(node.connect(mock=True), 10)
    counter = node.counter
    await asyncio.wait_for(counter.trigger(), 5)

    await counter.stage()
    await counter.prepare(None)
    await counter.kickoff()
    for value in (1, 2, 3):
        set_mock_value(counter.value, value)
    await asyncio.wait_for(counter.complete(), 5)
    (page,) = [page async for page in counter.collect_pages()]
    await counter.unstage()
    assert page["data"]["acq-counter-value"] == [1, 2, 3]

    # a mocked controller: its channels come from the annotations
    control = node.control
    assert set(control.channels) == {"t", "monitor"}
    assert control.channels["t"] is control.t
    assert control.t._module == "timer"
    await asyncio.wait_for(control.trigger(), 5)
    await control.stage()
    await control.prepare({"t": AcquisitionGoal(goal=2, enable=True)})
    await control.kickoff()
    set_mock_value(control.t.value, 1)
    set_mock_value(control.monitor.value, 7)
    await asyncio.wait_for(control.complete(), 5)
    pages = [page async for page in control.collect_pages()]
    await control.unstage()
    assert [page["data"] for page in pages] == [
        {"acq-control-t-value": [1]},
        {"acq-control-monitor-value": [7]},
    ]

    # a real connect with the generated classes
    real = generated(acquisition_sim.uri, name="acq")
    await asyncio.wait_for(real.connect(), 10)
    try:
        assert isinstance(real.counter.prepare_, TriggerableCommand)
        await asyncio.wait_for(real.counter.trigger(), 5)
        assert real.control.channels["t"] is real.control.t
        await asyncio.wait_for(real.control.trigger(), 5)
    finally:
        await real.counter.stop()
        await real.control.stop()


def test_reading_buffer_grows():
    buffer = _ReadingBuffer(capacity=2)
    for i in range(10):
        buffer.append(float(i), 100.0 + i)

    assert len(buffer) == 10
    assert buffer.last_timestamp == 109.0
    values, timestamps = buffer.take(4)
    assert values == [0.0, 1.0, 2.0, 3.0]
    assert timestamps == [100.0, 101.0, 102.0, 103.0]
    values, _ = buffer.take(100)
    assert values == [4.0, 5.0, 6.0, 7.0, 8.0, 9.0]
    assert len(buffer) == 0 and buffer.last_timestamp is None


def test_reading_buffer_arrays():
    buffer = _ReadingBuffer(capacity=1)
    for i in range(3):
        buffer.append(np.full(3, i), float(i))

    values, _ = buffer.take(3)
    assert [v.tolist() for v in values] == [[0, 0, 0], [1, 1, 1], [2, 2, 2]]
    assert all(isinstance(v, np.ndarray) for v in values)


def test_reading_buffer_falls_back_to_a_list():
    # a value that does not fit the array of the first: an array of another
    # length, or a float after ints
    buffer = _ReadingBuffer()
    buffer.append(np.zeros(2), 1.0)
    buffer.append(np.zeros(3), 2.0)
    values, _ = buffer.take(2)
    assert [len(v) for v in values] == [2, 3]

    buffer.append(1, 1.0)
    buffer.append(1.5, 2.0)
    assert buffer.take(2)[0] == [1, 1.5]

    buffer.append("IDLE", 1.0)
    buffer.append("BUSY", 2.0)
    assert buffer.take(2)[0] == ["IDLE", "BUSY"]
