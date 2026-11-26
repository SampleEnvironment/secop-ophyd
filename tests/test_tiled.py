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

from secop_ophyd.SECoPDevices import AcquisitionGoal, SECoPNodeDevice

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


def _fly(det, goal):
    yield from bps.open_run()
    yield from bps.stage(det)
    yield from bps.prepare(det, goal, wait=True)
    yield from bps.kickoff(det, wait=True)
    yield from bps.complete(det, wait=True)
    yield from bps.collect(det)
    yield from bps.unstage(det)
    yield from bps.close_run()


@pytest.mark.parametrize(
    "module, key, expected",
    [
        # without get_data: every update of 'value' of the cycle
        ("counter", "acq-counter-value", np.arange(1, 4)),
        # with get_data: its result
        ("spectrum", "acq-spectrum-get_data", [[3, 3, 3, 3]]),
    ],
)
def test_tiled_acquisition_fly(
    RE,
    acquisition_node: SECoPNodeDevice,
    run_to_tiled,
    module,
    key,
    expected,  # noqa: N803
):
    """The collected stream of an acquisition flown in a plan."""
    det = getattr(acquisition_node, module)
    try:
        run, _ = run_to_tiled(_fly(det, AcquisitionGoal(goal=3, enable=True)))
        assert run.metadata["stop"]["exit_status"] == "success"
        stored = run[det.name][key].read()
        if module == "counter" and stored[0] == 0:
            # the reset to 0 at 'go', sent if the last cycle did not end at 0
            stored = stored[1:]
        np.testing.assert_array_equal(stored, expected)
    finally:
        RE(bps.mv(det.goal_enable, False))
