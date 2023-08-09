from secop_ophyd.SECoPDevices import SECoP_Node_Device


from bluesky.plans import count
from bluesky.plan_stubs import read


def test_RE_count(cryo_sim, RE, cryo_node: SECoP_Node_Device):
    p = RE(count([cryo_node.cryo], num=5, delay=1))
    print(p)
    assert True

    cryo_node.disconnect_external()


def test_RE_String_value(nested_struct_sim, RE, nested_node_RE):
    p = RE(read(nested_node_RE.str_test))
