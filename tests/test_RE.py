import time

# mypy: disable-error-code="attr-defined"
import warnings

import bluesky.plan_stubs as bps
from bluesky.plans import count

from secop_ophyd.SECoPDevices import SECoPNodeDevice

warnings.filterwarnings(
    "ignore",
    message="""The method Broker.insert may be removed
    in a future release of databroker.""",
)


async def test_run_engine_count(RE, cryo_sim, cryo_node: SECoPNodeDevice):  # noqa: N803
    RE(count([cryo_node.cryo], num=5, delay=1))


async def test_run_engine_string_value(
    nested_struct_sim, RE, nested_node_no_re: SECoPNodeDevice  # noqa: N803
):
    def read_str():
        val = yield from bps.rd(nested_node_no_re.str_test.value)
        print(val)
        assert val is not None

    RE(read_str())


async def test_abs_set_wait_behaviour(
    RE, cryo_sim, cryo_node: SECoPNodeDevice  # noqa: N803
):

    def wait_on_abs_set():
        yield from bps.abs_set(cryo_node.cryo.window, 10, wait=True)
        yield from bps.abs_set(cryo_node.cryo.ramp, 20, wait=True)
        yield from bps.abs_set(cryo_node.cryo.tolerance, 2, wait=True)
        before = time.time()
        yield from bps.abs_set(cryo_node.cryo, 20, wait=True)
        after = time.time()

        assert after - before >= 10

        before = time.time()
        yield from bps.abs_set(cryo_node.cryo, 10, wait=False)
        after = time.time()

        assert after - before < 5

    RE(wait_on_abs_set())
