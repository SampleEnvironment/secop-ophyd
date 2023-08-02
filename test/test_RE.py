from secop_ophyd.SECoPDevices import SECoP_Node_Device


from bluesky.plans import count


def test_RE_count(cryo_sim, RE, cryo_node: SECoP_Node_Device):
    p = RE(count([cryo_node.cryo], num=5, delay=1))
    print(p)
    assert True

    cryo_node.disconnect_external()
