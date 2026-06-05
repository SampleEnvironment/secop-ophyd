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
        print("Setting window=10")
        yield from bps.abs_set(cryo_node.cryo.window, 10, wait=True)
        print("Setting ramp=20")
        yield from bps.abs_set(cryo_node.cryo.ramp, 20, wait=True)
        print("Setting tolerance=2")
        yield from bps.abs_set(cryo_node.cryo.tolerance, 2, wait=True)
        before = time.time()
        print("abs_set cryo=20 (wait=True) starting")
        yield from bps.abs_set(cryo_node.cryo, 20, wait=True)
        after = time.time()
        print(f"abs_set cryo=20 (wait=True) took {after - before:.2f}s")

        assert after - before >= 10

        before = time.time()
        print("abs_set cryo=10 (wait=False) starting")
        yield from bps.abs_set(cryo_node.cryo, 15, wait=False, group="cryo")

        after = time.time()
        print(f"abs_set cryo=10 (wait=False) returned in {after - before:.2f}s")

        assert after - before < 5

        print("Waiting on group='cryo'")
        yield from bps.wait(group="cryo")
        print("Group 'cryo' done")

    RE(wait_on_abs_set())
