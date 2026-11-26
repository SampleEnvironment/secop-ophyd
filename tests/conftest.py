# mypy: disable-error-code="attr-defined"
import logging
from contextlib import contextmanager
from pathlib import Path

import pytest
from bluesky import RunEngine
from frappy.datatypes import (
    ArrayOf,
    FloatRange,
    IntRange,
    StringType,
    StructOf,
    TupleOf,
)
from ophyd_async.core import init_devices

from secop_ophyd.AsyncFrappyClient import AsyncSecopClient
from secop_ophyd.SECoPDevices import SECoPDevice, SECoPNodeDevice
from tests.util import SecNode


@pytest.fixture(autouse=True)
async def cleanup_secop_clients():
    """Clear SECoP clients between tests to ensure fresh connections."""
    yield
    # After each test, shut down and clear the cached clients, so they do not
    # keep connections to the session scoped SEC nodes open.
    for client in list(SECoPDevice._clients.values()):
        try:
            await client.disconnect(True)
        except Exception:
            pass
    SECoPDevice._clients.clear()


@pytest.fixture
def mass_spectrometer_description():
    mass_spectrometer_description = (
        Path(__file__).parent / "static_test_data" / "SHALL_mass_spec_describe.txt"
    )

    with mass_spectrometer_description.open() as f:
        description = f.read()

    return description


@pytest.fixture
def mass_spectrometer_description_no_impl():
    mass_spectrometer_description = (
        Path(__file__).parent
        / "static_test_data"
        / "SHALL_mass_spec_describe_no_impl_prop.txt"
    )

    with mass_spectrometer_description.open() as f:
        description = f.read()

    return description


@pytest.fixture
def clean_generated_file(tmp_path):
    """Provide an empty, per-test output directory for code generation tests.

    pytest keeps the directories of the last few runs, so the generated files
    can still be inspected after a test completes.
    """
    testgen_dir = tmp_path / "testgen"
    testgen_dir.mkdir()
    return testgen_dir


@pytest.fixture(scope="session")
def sec_node_workdir(tmp_path_factory) -> Path:
    return tmp_path_factory.mktemp("sec_nodes")


@contextmanager
def _running_node(cfg: str, name: str, workdir: Path, attempts: int = 3):
    # a free port can be taken by another process before the node binds it,
    # so retry on a new port
    for attempt in range(attempts):
        try:
            node = SecNode(cfg, name, workdir).start()
            break
        except RuntimeError:
            if attempt == attempts - 1:
                raise
    try:
        yield node
    finally:
        node.stop()


# Session scoped SEC nodes: started once per test session (per xdist worker).
# Tests using these must not leave lasting changes on the node, tests that
# need to kill/restart a node use the function scoped variants below.


@pytest.fixture(scope="session")
def cryo_sim(sec_node_workdir):
    with _running_node("cryo_cfg.py", "cryo", sec_node_workdir) as node:
        yield node


@pytest.fixture(scope="session")
def nested_struct_sim(sec_node_workdir):
    with _running_node("ophyd_secop_test_cfg.py", "nested", sec_node_workdir) as node:
        yield node


@pytest.fixture(scope="session")
def motion_sim(sec_node_workdir):
    with _running_node("motion_cfg.py", "motion", sec_node_workdir) as node:
        yield node


@pytest.fixture(scope="session")
def acquisition_sim(sec_node_workdir):
    with _running_node("acquisition_cfg.py", "acquisition", sec_node_workdir) as node:
        yield node


@pytest.fixture
def fresh_cryo_sim(sec_node_workdir):
    """A private cryo node, for tests that restart it or change its state."""
    with _running_node("cryo_cfg.py", "cryo", sec_node_workdir) as node:
        yield node


@pytest.fixture
def fresh_nested_struct_sim(sec_node_workdir):
    """A private nested node, for tests that change its state."""
    with _running_node("ophyd_secop_test_cfg.py", "nested", sec_node_workdir) as node:
        yield node


@pytest.fixture
def logger():
    class NoRXFilter(logging.Filter):
        def filter(self, record):
            return not record.getMessage().startswith("RX")

    class NoTXFilter(logging.Filter):
        def filter(self, record):
            return not record.getMessage().startswith("TX")

    logger = logging.getLogger("async_client")
    logger.setLevel(logging.DEBUG)

    logger.addFilter(NoRXFilter())
    logger.addFilter(NoTXFilter())

    return logger


@pytest.fixture()
async def async_frappy_client(cryo_sim, logger):
    client = AsyncSecopClient(host="localhost", port=str(cryo_sim.port), log=logger)
    await client.connect(3)
    yield client
    await client.disconnect(True)


@pytest.fixture()
async def async_secop_client(cryo_sim, logger):
    client = AsyncSecopClient(host="localhost", port=str(cryo_sim.port), log=logger)
    await client.connect(3)
    yield client
    await client.disconnect(True)


@pytest.fixture()
async def nested_client(nested_struct_sim, logger):
    client = AsyncSecopClient(
        host="localhost", port=str(nested_struct_sim.port), log=logger
    )
    await client.connect(3)
    yield client
    await client.disconnect(True)


@pytest.fixture()
async def RE():  # noqa: N802
    re = RunEngine({})
    return re


@pytest.fixture()
async def nested_node_no_re(nested_struct_sim):
    async with init_devices():
        nested = SECoPNodeDevice(sec_node_uri=nested_struct_sim.uri)

    return nested


@pytest.fixture()
def nested_node(RE, nested_struct_sim):  # noqa: N803
    with init_devices():
        nested = SECoPNodeDevice(sec_node_uri=nested_struct_sim.uri)

    return nested


@pytest.fixture()
async def cryo_node_no_re(cryo_sim):
    async with init_devices():
        cryo = SECoPNodeDevice(sec_node_uri=cryo_sim.uri)
    return cryo


@pytest.fixture()
def cryo_node(RE, cryo_sim):  # noqa: N803
    with init_devices():
        cryo = SECoPNodeDevice(sec_node_uri=cryo_sim.uri)

    return cryo


@pytest.fixture()
async def motion_node_no_re(motion_sim):
    async with init_devices():
        motion = SECoPNodeDevice(sec_node_uri=motion_sim.uri)
    return motion


@pytest.fixture()
async def acquisition_node_no_re(acquisition_sim):
    async with init_devices():
        acquisition = SECoPNodeDevice(sec_node_uri=acquisition_sim.uri)
    return acquisition


@pytest.fixture()
def acquisition_node(RE, acquisition_sim):  # noqa: N803
    with init_devices():
        acquisition = SECoPNodeDevice(sec_node_uri=acquisition_sim.uri)
    # short names: tiled limits data keys to 63 characters
    acquisition.set_name("acq")
    return acquisition


@pytest.fixture()
def motion_node(RE, motion_sim):  # noqa: N803
    with init_devices():
        motion = SECoPNodeDevice(sec_node_uri=motion_sim.uri)
    return motion


@pytest.fixture
def struct_val():
    return {
        "number": 5.0,
        "string": "blablalbla",
        "tupl": [1.0, 1.0, 1.0],
        "pos_struct": {"x": 5.0, "y": 10.0, "z": 15.0, "col": "green", "enum": 1},
    }


@pytest.fixture
def nested_param_description():
    return {
        "_nested_struct": {
            "datainfo": {
                "members": {
                    "number": {"max": 100.0, "min": 0.0, "type": "double", "unit": "s"},
                    "pos_struct": {
                        "members": {
                            "col": {"type": "string"},
                            "enum": {
                                "members": {
                                    "mode_max": 2,
                                    "mode_one": 1,
                                    "mode_zero": 0,
                                },
                                "type": "enum",
                            },
                            "x": {
                                "max": 100.0,
                                "min": 0.0,
                                "type": "double",
                                "unit": "m",
                            },
                            "y": {
                                "max": 100.0,
                                "min": 0.0,
                                "type": "double",
                                "unit": "m",
                            },
                            "z": {
                                "max": 100.0,
                                "min": 0.0,
                                "type": "double",
                                "unit": "m",
                            },
                        },
                        "type": "struct",
                    },
                    "string": {"type": "string"},
                    "tupl": {
                        "members": [
                            {"max": 100.0, "min": 0.0, "type": "double"},
                            {"max": 100.0, "min": 0.0, "type": "double"},
                            {"max": 100.0, "min": 0.0, "type": "double"},
                        ],
                        "type": "tuple",
                    },
                },
                "type": "struct",
            },
            "description": "nestedstruct dict containing other structs and tuples ",
            "readonly": True,
        }
    }


@pytest.fixture
def array_dtype():
    dty = StructOf(
        flt=FloatRange(), testarr=ArrayOf(members=IntRange(), minlen=5, maxlen=5)
    )

    elem = {"flt": 1.3, "testarr": [1, 2, 3, 4, 5]}
    dty.check_type(elem)

    return dty, elem


@pytest.fixture
def array_tuple_dtype():
    dty = TupleOf(FloatRange(), ArrayOf(members=IntRange(), minlen=5, maxlen=5))

    elem = {"flt": 1.3, "testarr": [1, 2, 3, 4, 5]}
    dty.check_type(elem)

    return dty, elem


@pytest.fixture
def array_ragged_dtype():
    dty = StructOf(
        flt=FloatRange(),
        testarr=ArrayOf(
            members=ArrayOf(members=IntRange(), minlen=1, maxlen=5), minlen=5, maxlen=5
        ),
    )

    elem = {"flt": 1.3, "testarr": [[1, 2, 3], [1, 2, 3, 4, 5], [1, 2], [1], [1, 2, 5]]}
    dty.check_type(elem)

    return dty, elem


@pytest.fixture
def array_2d_dtype():
    dty = StructOf(
        flt=FloatRange(),
        testarr=ArrayOf(
            members=ArrayOf(members=IntRange(), minlen=5, maxlen=5), minlen=5, maxlen=5
        ),
    )

    elem = {
        "flt": 1.3,
        "testarr": [
            [1, 2, 3, 4, 5],
            [1, 2, 3, 4, 5],
            [1, 2, 3, 4, 5],
            [1, 2, 3, 4, 5],
            [1, 2, 3, 4, 5],
        ],
    }
    dty.check_type(elem)

    return dty, elem


@pytest.fixture
def array_of_structs_dtype():
    dty = StructOf(
        flt=FloatRange(),
        testarr=ArrayOf(
            members=StructOf(
                red=FloatRange(),
                green=FloatRange(),
                blue=FloatRange(),
                name=StringType(),
            ),
            minlen=5,
            maxlen=5,
        ),
    )

    elem = {
        "flt": 1.3,
        "testarr": [
            {"red": 1.2, "green": 3.4, "blue": 24.5, "name": "dieter"},
            {"red": 1.2, "green": 3.4, "blue": 24.5, "name": "dieter"},
            {"red": 1.2, "green": 3.4, "blue": 24.5, "name": "dieter"},
            {"red": 1.2, "green": 3.4, "blue": 24.5, "name": "dieter"},
            {"red": 1.2, "green": 3.4, "blue": 24.5, "name": "dieter"},
        ],
    }
    dty.check_type(elem)

    return dty, elem


@pytest.fixture
def bare_array_of_structs_dtype():
    dty = ArrayOf(
        members=StructOf(
            red=FloatRange(), green=FloatRange(), blue=FloatRange(), name=StringType()
        ),
        minlen=5,
        maxlen=5,
    )

    elem = [
        {"red": 1.2, "green": 3.4, "blue": 24.5, "name": "dieter"},
        {"red": 1.2, "green": 3.4, "blue": 24.5, "name": "dieter"},
        {"red": 1.2, "green": 3.4, "blue": 24.5, "name": "dieter"},
        {"red": 1.2, "green": 3.4, "blue": 24.5, "name": "dieter"},
        {"red": 1.2, "green": 3.4, "blue": 24.5, "name": "dieter"},
    ]
    dty.check_type(elem)

    return dty, elem
