from secop_ophyd.SECoPDevices import SECoP_Node_Device


from bluesky.plans import count
from bluesky.plan_stubs import read
import time


def test_RE_count(cryo_sim, RE, cryo_node: SECoP_Node_Device, db):
    p = RE(count([cryo_node.cryo], num=5, delay=1))

    run = db[p]

    data = run.primary.read()

    cryo_dat = data[cryo_node.cryo.value.name]

    assert len(cryo_dat) == 5
    assert max(cryo_dat.data) < 11 and min(cryo_dat.data) > 8

    cryo_node.disconnect()


def test_RE_String_value(nested_struct_sim, RE, nested_node_RE: SECoP_Node_Device, db):
    p = RE(read(nested_node_RE.str_test))
    nested_node_RE.disconnect()
