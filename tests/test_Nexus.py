from secop_ophyd.SECoPDevices import SECoPNodeDevice


async def test_node_nexus_struct(cryo_sim, cryo_node_internal_loop: SECoPNodeDevice):
    generated_text = await cryo_node_internal_loop.generate_nexus_struct()

    print(generated_text)

    await cryo_node_internal_loop.disconnect_async()
