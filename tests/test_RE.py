# mypy: disable-error-code="attr-defined"
import warnings

from bluesky.plans import count

from secop_ophyd.SECoPDevices import SECoPNodeDevice

warnings.filterwarnings(
    "ignore",
    message="""The method Broker.insert may be removed
    in a future release of databroker.""",
)


async def test_run_engine_count(cryo_sim, run_engine, cryo_node: SECoPNodeDevice, db):
    p = run_engine(count([cryo_node.cryo], num=5, delay=1))

    run = db[p]

    data = run.primary.read()

    cryo_dat = data[cryo_node.cryo.value.name]

    assert len(cryo_dat) == 5
    assert max(cryo_dat.data) < 11 and min(cryo_dat.data) > 8

    cryo_node.disconnect()


async def test_run_engine_string_value(
    nested_struct_sim, run_engine, nested_node_re: SECoPNodeDevice, db
):
    # p = RE(read(nested_node_RE.str_test))
    nested_node_re.disconnect()
