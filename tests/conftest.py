# mypy: disable-error-code="attr-defined"
import logging
import os
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
from xprocess import ProcessStarter

from secop_ophyd.AsyncFrappyClient import AsyncFrappyClient
from secop_ophyd.SECoPDevices import SECoPNodeDevice


@pytest.fixture(autouse=True)
def cleanup_secop_clients():
    """Clear SECoP clients between tests to ensure fresh connections."""
    yield
    # After each test, clear the cached clients
    from secop_ophyd.SECoPDevices import SECoPDevice

    SECoPDevice.clients.clear()


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
def clean_generated_file():
    """Clean up generated genNodeClass.py file before test runs.

    This fixture ensures a fresh start for code generation tests while
    allowing inspection of results after the test completes.

    Returns:
        Path to the testgen directory where files should be generated
    """
    testgen_dir = Path(__file__).parent / "testgen"
    testgen_dir.mkdir(exist_ok=True)

    gen_file = testgen_dir / "genNodeClass.py"
    if gen_file.exists():
        gen_file.unlink()

    return testgen_dir


@pytest.fixture
def frappy_env():
    """Create and return environment variables and paths for frappy server."""
    # Create absolute paths for frappy requirements
    base_dir = os.path.abspath(os.path.dirname(os.path.dirname(__file__)))
    conf_dir = os.path.join(base_dir, "cfg")
    log_dir = os.path.join(base_dir, "logs")
    pid_dir = os.path.join(base_dir, "pid")

    # Add path to frappy_modules
    frappy_modules_dir = os.path.join(base_dir, "frappy_modules")

    # Create environment variables
    env = os.environ.copy()  # Start with current environment

    env.update(
        {
            "FRAPPY_CONFDIR": conf_dir,
            "FRAPPY_LOGDIR": log_dir,
            "FRAPPY_PIDDIR": pid_dir,
            "FRAPPY_PROJECT_ROOT": base_dir,  # Add the project root
        }
    )

    return {
        "env": env,
        "conf_dir": conf_dir,
        "log_dir": log_dir,
        "pid_dir": pid_dir,
        "modules_dir": frappy_modules_dir,
    }


@pytest.fixture
def cryo_sim(xprocess, frappy_env):

    class Starter(ProcessStarter):
        # startup pattern
        pattern = ".*: startup done with interface(s)*"
        timeout = 5
        # command to start process
        env = frappy_env["env"]  # Use the environment variables from the fixture

        args = [
            "frappy-server",
            "-c",
            "cryo_cfg.py",
            "cryo",
        ]

    # ensure process is running and return its logfile
    xprocess.ensure("cryo_sim", Starter)

    yield xprocess

    # clean up whole process tree afterwards
    xprocess.getinfo("cryo_sim").terminate()


@pytest.fixture
def nested_struct_sim(xprocess, frappy_env):

    class Starter(ProcessStarter):
        # startup pattern
        pattern = ".*: startup done with interface(s)*"
        timeout = 5

        env = frappy_env["env"]  # Use the environment variables from the fixture
        args = [
            "frappy-server",
            "-c",
            "ophyd_secop_test_cfg.py",
            "nested",
        ]

    pname = "nested"
    # ensure process is running and return its logfile
    xprocess.ensure(pname, Starter)

    yield

    # clean up whole process tree afterwards
    xprocess.getinfo(pname).terminate()


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

    logging.basicConfig(
        datefmt="%d/%m/%Y %I:%M:%S",
        format="%(asctime)s.%(msecs)03d  %(name)-12s %(levelname)-8s %(message)s",
        filename="asyncclient.log",
        encoding="utf-8",
        level=logging.DEBUG,
        filemode="w",
    )

    return logger


@pytest.fixture()
async def async_frappy_client(cryo_sim, logger, port="10769"):
    client = AsyncFrappyClient(host="localhost", port=port, log=logger)

    await client.connect(3)

    return client


@pytest.fixture()
async def nested_client(nested_struct_sim, logger, port="10771"):
    client = AsyncFrappyClient(host="localhost", port=port, log=logger)

    await client.connect(3)

    return client


@pytest.fixture()
async def RE():  # noqa: N802
    re = RunEngine({})
    return re


@pytest.fixture()
async def nested_node_no_re():
    async with init_devices():
        nested = SECoPNodeDevice(
            sec_node_uri="localhost:10771",
        )

    return nested


@pytest.fixture()
def nested_node(RE):  # noqa: N803
    with init_devices():
        nested = SECoPNodeDevice(
            sec_node_uri="localhost:10771",
        )

    return nested


@pytest.fixture()
async def cryo_node_no_re():
    async with init_devices():
        cryo = SECoPNodeDevice(
            sec_node_uri="localhost:10769",
        )
    return cryo


@pytest.fixture()
def cryo_node(RE):  # noqa: N803
    with init_devices():
        cryo = SECoPNodeDevice(
            sec_node_uri="localhost:10769",
        )

    return cryo


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
