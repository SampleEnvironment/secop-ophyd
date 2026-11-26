# mypy: disable-error-code="attr-defined"
"""End-to-end: SEC node -> secop-ophyd -> bluesky RunEngine -> TiledWriter -> tiled.

Each test runs a plan against a live frappy SEC node, streams the documents
into an in-process tiled server (SimpleTiledServer: temporary SQLite catalog
+ file storage) and reads the run back through the tiled client, comparing
what was stored against the event documents the RunEngine emitted.
"""

from collections import defaultdict

import bluesky.plan_stubs as bps
import numpy as np
import pytest
from bluesky.plans import count
from bluesky_tiled_plugins import TiledWriter
from tiled.client import from_uri
from tiled.server import SimpleTiledServer

from secop_ophyd.devices import AcquisitionGoal, SECoPNodeDevice

NUM_EVENTS = 3


@pytest.fixture
def tiled_client(tmp_path):
    with SimpleTiledServer(directory=tmp_path) as server:
        yield from_uri(server.uri)


@pytest.fixture
def run_to_tiled(RE, tiled_client):  # noqa: N803
    """Run a plan with TiledWriter subscribed; return (tiled run, event data
    emitted by the RunEngine keyed by data key)."""

    def _run(plan):
        emitted: dict[str, list] = defaultdict(list)

        def collect(name, doc):
            if name == "event":
                for key, val in doc["data"].items():
                    emitted[key].append(val)

        RE.subscribe(TiledWriter(tiled_client))
        RE.subscribe(collect)
        (uid,) = RE(plan)
        return tiled_client[uid], emitted

    return _run


def assert_stored_matches_emitted(primary, emitted: dict[str, list]) -> dict:
    """Read every data key back from tiled (each is its own array node) and
    compare it, event by event, with what the RunEngine emitted."""
    missing = set(emitted) - set(primary)
    assert not missing, f"data keys missing in tiled: {missing}"

    stored: dict[str, np.ndarray] = {}
    for key, values in emitted.items():
        stored[key] = primary[key].read()
        assert len(stored[key]) == len(values) == NUM_EVENTS, key
        for stored_val, emitted_val in zip(stored[key], values):
            np.testing.assert_array_equal(
                stored_val, np.asarray(emitted_val), err_msg=key
            )

    return stored


def test_tiled_composite_parameters(
    nested_node: SECoPNodeDevice, run_to_tiled  # noqa: N803
):
    """Struct/tuple parameters are split into atomic member signals; every
    member must land in tiled as its own column with a proper dtype."""
    # tiled stores every data key as an SQL column, which is limited to 63
    # bytes -- the default node name (equipment id) is too long for that
    nested_node.set_name("nest")

    modules = [
        nested_node.ophy_struct,  # StructOf value/target -> value_x, ...
        nested_node.struct_of_arrays,  # StructOf of ArrayOf members
        nested_node.nd_arr,  # 2D ArrayOf(ArrayOf(int))
        nested_node.str_test,
    ]

    run, emitted = run_to_tiled(count(modules, num=NUM_EVENTS))

    assert run.metadata["stop"]["exit_status"] == "success"
    primary = run["primary"]
    stored = assert_stored_matches_emitted(primary, emitted)

    # struct members are plain scalar columns
    for member in ("x", "y", "z"):
        assert stored[f"nest-ophy_struct-value_{member}"].dtype == np.float64
    assert stored["nest-ophy_struct-value_color"].dtype.kind == "U"

    # array members are stored as typed (time, 5) arrays. Note: read them per
    # key -- run["primary"].read() (the xarray view) renders these small
    # inline arrays as strings, although the stored data is numeric
    for key, kind in (
        ("nest-struct_of_arrays-value_ints", "i"),
        ("nest-struct_of_arrays-value_floats", "f"),
        ("nest-struct_of_arrays-value_strings", "U"),
    ):
        assert stored[key].dtype.kind == kind, key
        assert stored[key].shape == (NUM_EVENTS, 5), key

    assert stored["nest-nd_arr-value"].dtype.kind == "i"
    assert stored["nest-nd_arr-value"].shape == (NUM_EVENTS, 5, 5)

    # the composite parent itself is not a data key -- only its members
    assert "nest-ophy_struct-value" not in primary


def test_tiled_drivable_with_config(cryo_node: SECoPNodeDevice, run_to_tiled):
    """A Drivable: readback in the data table, configuration (including the
    split status_0/status_1 signals) in the stream's configuration metadata."""
    cryo_node.set_name("cryo")
    cryo = cryo_node.cryo

    run, emitted = run_to_tiled(count([cryo], num=NUM_EVENTS))

    assert run.metadata["stop"]["exit_status"] == "success"
    primary = run["primary"]
    stored = assert_stored_matches_emitted(primary, emitted)
    assert stored[cryo.name].dtype == np.float64

    config = primary.metadata["configuration"][cryo.name]
    cfg_data = config["data"]
    assert isinstance(cfg_data[cryo.status_0.name], str)
    assert isinstance(cfg_data[cryo.status_1.name], str)
    # configuration values stored in tiled match the device
    for key, desc in config["data_keys"].items():
        assert key in cfg_data
        assert desc["dtype"] in ("number", "integer", "string", "boolean", "array")


# --- acquisitions, end to end ----------------------------------------------

GOAL = 3

# (layout, data source) -> (module of the acquisition test node, role of the
# channel whose goal ends the cycle; None for a single-module acquisition)
ACQUISITIONS = {
    # one Acquisition module, collecting the updates of 'value'
    ("unified", "value"): ("counter", None),
    # one Acquisition module with a 'get_data' command
    ("unified", "get_data"): ("spectrum", None),
    # a controller with two channels, collecting the updates of their 'value'
    ("split", "value"): ("control", "t"),
    # a controller with a channel with a 'get_data' command
    ("split", "get_data"): ("spectrum_control", "spectrum"),
}


@pytest.fixture
def run_streams_to_tiled(RE, tiled_client):  # noqa: N803
    """Run a plan with TiledWriter subscribed; return (tiled run, data the
    RunEngine emitted as {stream: {data key: [values]}}), from events and event
    pages alike."""

    def _run(plan):
        streams: dict[str, str] = {}
        emitted: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))

        def collect(name, doc):
            if name == "descriptor":
                streams[doc["uid"]] = doc["name"]
            elif name == "event":
                for key, val in doc["data"].items():
                    emitted[streams[doc["descriptor"]]][key].append(val)
            elif name == "event_page":
                for key, vals in doc["data"].items():
                    emitted[streams[doc["descriptor"]]][key].extend(vals)

        RE.subscribe(TiledWriter(tiled_client))
        RE.subscribe(collect)
        (uid,) = RE(plan)
        return tiled_client[uid], emitted

    return _run


def _fly(det, goal):
    yield from bps.open_run()
    yield from bps.stage(det)
    yield from bps.prepare(det, goal, wait=True)
    yield from bps.kickoff(det, wait=True)
    yield from bps.complete(det, wait=True)
    yield from bps.collect(det)
    yield from bps.unstage(det)
    yield from bps.close_run()


def _step(det, goal):
    yield from bps.prepare(det, goal, wait=True)
    yield from count([det], num=NUM_EVENTS)


def _read_back(run, emitted: dict[str, dict[str, list]]) -> dict[str, dict]:
    """Read every emitted data key back from tiled and compare it, value by
    value, with what the RunEngine emitted."""
    stored: dict[str, dict] = {}
    for stream, keys in emitted.items():
        assert stream in run, f"stream {stream} missing in tiled"
        stored[stream] = {}
        for key, values in keys.items():
            data = stored[stream][key] = run[stream][key].read()
            assert len(data) == len(values), key
            for stored_val, emitted_val in zip(data, values):
                np.testing.assert_array_equal(
                    stored_val, np.asarray(emitted_val), err_msg=key
                )
    return stored


def _assert_counts(values) -> None:
    """The updates of 'value' of one cycle: 1, 2, ..., GOAL, after the reset
    to 0 at 'go' (only sent if the previous cycle did not end at 0 too)."""
    values = list(values)
    if values[:1] == [0]:
        values = values[1:]
    assert values == list(range(1, GOAL + 1))


@pytest.mark.parametrize("mode", ["step", "fly"])
@pytest.mark.parametrize("layout, source", list(ACQUISITIONS))
def test_tiled_acquisition(
    RE,  # noqa: N803
    acquisition_node: SECoPNodeDevice,
    run_streams_to_tiled,
    layout,
    source,
    mode,
):
    """An acquisition of GOAL counts -- a single module, or a controller with
    its channels -- run step by step (trigger/read) or as a flyer
    (kickoff/complete/collect): its data lands in tiled and reads back the
    same."""
    module, role = ACQUISITIONS[layout, source]
    det = getattr(acquisition_node, module)
    goal = AcquisitionGoal(goal=GOAL, enable=True)
    prepare_value: AcquisitionGoal | dict[str, AcquisitionGoal]
    if role is None:
        channels = [det]
        goal_channel = det
        prepare_value = goal
    else:
        channels = list(det.channels.values())
        goal_channel = det.channels[role]
        prepare_value = {role: goal}
    plan = _step(det, prepare_value) if mode == "step" else _fly(det, prepare_value)

    try:
        run, emitted = run_streams_to_tiled(plan)
    finally:
        RE(bps.mv(goal_channel.goal_enable, False))

    assert run.metadata["stop"]["exit_status"] == "success"
    stored = _read_back(run, emitted)
    spectrum = np.full(4, GOAL)

    if mode == "step":
        # one event per cycle: the final value (and spectrum) of each channel
        assert set(stored) == {"primary"}
        primary = stored["primary"]
        for channel in channels:
            get_data_key = f"{channel.name}-get_data"
            np.testing.assert_array_equal(
                primary[channel.value.name], [GOAL] * NUM_EVENTS
            )
            if source == "get_data":
                np.testing.assert_array_equal(
                    primary[get_data_key], [spectrum] * NUM_EVENTS
                )
            else:
                assert get_data_key not in primary
    else:
        # a stream per channel, named after the channel
        assert set(stored) == {channel.name for channel in channels}
        for channel in channels:
            collected = stored[channel.name]
            get_data_key = f"{channel.name}-get_data"
            if source == "get_data":
                # the result of 'get_data' at the end of the cycle
                assert set(collected) == {get_data_key}
                np.testing.assert_array_equal(collected[get_data_key], [spectrum])
            else:
                # every update of 'value' during the cycle
                assert set(collected) == {channel.value.name}
                _assert_counts(collected[channel.value.name])
