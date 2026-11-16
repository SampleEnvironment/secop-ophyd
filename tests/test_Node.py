# mypy: disable-error-code="attr-defined"
import asyncio

from ophyd_async.core import AsyncStatus, SignalR, init_devices, observe_value

from secop_ophyd.SECoPDevices import SECoPMoveableDevice, SECoPNodeDevice


async def test_node_structure(cryo_sim, cryo_node_no_re: SECoPNodeDevice):
    assert isinstance(cryo_node_no_re, SECoPNodeDevice)


async def test_node_read(cryo_sim, cryo_node_no_re: SECoPNodeDevice):
    # Node device should return the readbacks of the read signals of the child devices

    val_read = await cryo_node_no_re.read()
    print(val_read)
    assert val_read != {}


async def test_source(cryo_sim, cryo_node_no_re: SECoPNodeDevice):
    # Node device should return the readbacks of the read signals of the child devices

    # Prameter name and source
    val_name = cryo_node_no_re.cryo.value.name
    val_source = cryo_node_no_re.cryo.value.source

    # StandardMovable.set_name() renames the readback (value) signal to match
    # its parent device, so it's the same name as the device itself
    assert val_name == "cryo_7-frappy-demo-cryo"
    assert val_name == cryo_node_no_re.cryo.name
    assert val_source == f"{cryo_sim.uri}:cryo:value"

    # property name and source
    prop_name = cryo_node_no_re.equipment_id.name
    prop_source = cryo_node_no_re.equipment_id.source

    assert prop_name == "cryo_7-frappy-demo-equipment_id"
    assert prop_source == f"{cryo_sim.uri}:equipment_id"

    mod_prop_name = cryo_node_no_re.cryo.description.name
    mod_prop_source = cryo_node_no_re.cryo.description.source

    assert mod_prop_name == "cryo_7-frappy-demo-cryo-description"
    assert mod_prop_source == f"{cryo_sim.uri}:cryo:description"


async def test_node_describe(cryo_sim, cryo_node_no_re: SECoPNodeDevice):
    # Node device should return the descriptions of the read signals of the child
    # devices
    val_desc = await cryo_node_no_re.describe()
    assert val_desc != {}


async def test_node_module_describe(cryo_sim, cryo_node_no_re: SECoPNodeDevice):

    val_desc = await cryo_node_no_re.cryo.describe_configuration()
    conf = await cryo_node_no_re.cryo.read_configuration()

    assert val_desc != {}
    assert conf != {}


async def test_dev_read(cryo_sim, cryo_node_no_re: SECoPNodeDevice):

    cryo_dev: SECoPMoveableDevice = cryo_node_no_re.cryo
    cryo_val = await cryo_dev.read()
    val_name = cryo_dev.value.name

    read_val = cryo_val[val_name].get("value")

    assert read_val is not None
    assert read_val > 5


async def test_signal_read(cryo_sim, cryo_node_no_re: SECoPNodeDevice):

    cryo_dev: SECoPMoveableDevice = cryo_node_no_re.cryo

    p = await cryo_dev.p.get_value(cached=False)
    assert p == 40.0


async def test_signal_read_cached(cryo_sim, cryo_node_no_re: SECoPNodeDevice):

    cryo_dev: SECoPMoveableDevice = cryo_node_no_re.cryo

    cryo_dev.p.subscribe_reading(lambda val: None)

    p = await cryo_dev.p.get_value(cached=True)

    assert p == 40.0


async def test_signal_stage_unstage_read_cached(
    cryo_sim, cryo_node_no_re: SECoPNodeDevice
):

    cryo_dev: SECoPMoveableDevice = cryo_node_no_re.cryo

    await cryo_dev.p.stage()

    p = await cryo_dev.p.get_value(cached=True)

    await cryo_dev.p.unstage()

    assert p == 40.0


async def test_status(cryo_sim, cryo_node_no_re: SECoPNodeDevice):

    cryo_dev: SECoPMoveableDevice = cryo_node_no_re.cryo
    status: SignalR = cryo_dev.status

    # the cryo simulation drifts in and out of tolerance on its own, so only the
    # structure is checked here (status values are covered in test_motion.py)
    async for current_stat in observe_value(status):
        break

    assert 0 <= int(current_stat["f0"]) < 600
    assert isinstance(current_stat["f1"].item(), str)


async def test_trigger(cryo_sim, cryo_node_no_re: SECoPNodeDevice):
    cryo_dev: SECoPMoveableDevice = cryo_node_no_re.cryo

    val_old = await cryo_dev.read()

    val_cached = await cryo_dev.read()

    assert (
        val_old[cryo_dev.value.name]["value"]
        == val_cached[cryo_dev.value.name]["value"]
    )

    stat: AsyncStatus = cryo_dev.trigger()

    await stat

    val_new = await cryo_dev.read()

    assert (
        val_old[cryo_dev.value.name]["value"] != val_new[cryo_dev.value.name]["value"]
    )


# async def test_node_read_config(cryo_sim,cryo_node:SECoP_Node_Device):
#    # Node device has no read value, it has to return an empty dict
#    val_desc = await cryo_node.read_configuration()
#    assert  val_desc == {}


async def test_node_reconn(fresh_cryo_sim):
    async with init_devices():
        cryo_node = SECoPNodeDevice(sec_node_uri=fresh_cryo_sim.uri)

    old_conf = await cryo_node.read_configuration()

    # restart the SEC node on the same port, the client reconnects on its own
    fresh_cryo_sim.restart()

    async def wait_for_new_firmware_reading():
        while True:
            try:
                new_conf = await cryo_node.read_configuration()
            except Exception:
                new_conf = None
            if new_conf is not None:
                fw_new = new_conf[cryo_node.firmware.name]
                if fw_new["timestamp"] > old_conf[cryo_node.firmware.name]["timestamp"]:
                    return
            await asyncio.sleep(0.05)

    await asyncio.wait_for(wait_for_new_firmware_reading(), timeout=10)
