# mypy: disable-error-code="attr-defined"
import warnings

from bluesky.plans import count

from secop_ophyd.SECoPDevices import SECoPNodeDevice

warnings.filterwarnings(
    "ignore",
    message="""The method Broker.insert may be removed
    in a future release of databroker.""",
)


async def test_run_engine_count(cryo_sim, run_engine, cryo_node: SECoPNodeDevice):
    run_engine(count([cryo_node.cryo], num=5, delay=1))

    cryo_node.disconnect()


async def test_run_engine_string_value(
    nested_struct_sim, run_engine, nested_node_re: SECoPNodeDevice
):
    # p = RE(read(nested_node_RE.str_test))
    nested_node_re.disconnect()
