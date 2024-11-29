# mypy: disable-error-code="attr-defined"
import asyncio

import numpy as np
import xprocess
from ophyd_async.core import AsyncStatus, SignalR, observe_value
from xprocess import ProcessStarter, XProcessInfo

from secop_ophyd.SECoPDevices import SECoPMoveableDevice, SECoPNodeDevice


async def test_node_structure(cryo_sim, cryo_node_internal_loop: SECoPNodeDevice):
    assert isinstance(cryo_node_internal_loop, SECoPNodeDevice)
    await cryo_node_internal_loop.disconnect_async()


async def test_node_read(cryo_sim, cryo_node_internal_loop: SECoPNodeDevice):
    # Node device should return the readbacks of the read signals of the child devices
    val_read = await cryo_node_internal_loop.read()
    assert val_read != {}
    await cryo_node_internal_loop.disconnect_async()


async def test_node_describe(cryo_sim, cryo_node_internal_loop: SECoPNodeDevice):
    # Node device should return the descriptions of the read signals of the child
    # devices
    val_desc = await cryo_node_internal_loop.describe()
    assert val_desc != {}
    await cryo_node_internal_loop.disconnect_async()


async def test_node_module_describe(cryo_sim, cryo_node_internal_loop: SECoPNodeDevice):

    val_desc = await cryo_node_internal_loop.cryo.describe_configuration()
    conf = await cryo_node_internal_loop.cryo.read_configuration()

    assert val_desc != {}
    assert conf != {}
    await cryo_node_internal_loop.disconnect_async()


async def test_dev_read(cryo_sim, cryo_node_internal_loop: SECoPNodeDevice):

    cryo_dev: SECoPMoveableDevice = cryo_node_internal_loop.cryo
    cryo_val = await cryo_dev.read()
    val_name = cryo_dev.value.name

    read_val = cryo_val[val_name].get("value")

    assert read_val is not None
    assert read_val > 5
    await cryo_node_internal_loop.disconnect_async()


async def test_signal_read(cryo_sim, cryo_node_internal_loop: SECoPNodeDevice):

    cryo_dev: SECoPMoveableDevice = cryo_node_internal_loop.cryo

    p = await cryo_dev.p.get_value(cached=False)
    assert p == 40.0

    await cryo_node_internal_loop.disconnect_async()


async def test_signal_read_cached(cryo_sim, cryo_node_internal_loop: SECoPNodeDevice):

    cryo_dev: SECoPMoveableDevice = cryo_node_internal_loop.cryo

    p = await cryo_dev.p.get_value(cached=True)

    assert p == 40.0

    await cryo_node_internal_loop.disconnect_async()


async def test_signal_stage_unstage_read_cached(
    cryo_sim, cryo_node_internal_loop: SECoPNodeDevice
):

    cryo_dev: SECoPMoveableDevice = cryo_node_internal_loop.cryo

    await cryo_dev.value.stage()

    await asyncio.sleep(1)

    await cryo_dev.value.unstage()

    await asyncio.sleep(1)

    p = await cryo_dev.p.get_value(cached=True)

    assert p == 40.0

    await cryo_node_internal_loop.disconnect_async()


async def test_status(cryo_sim, cryo_node_internal_loop: SECoPNodeDevice):

    cryo_dev: SECoPMoveableDevice = cryo_node_internal_loop.cryo
    status: SignalR = cryo_dev.status

    async for current_stat in observe_value(status):
        assert current_stat["f0"] == 100

        if current_stat["f0"] == 100:
            break


async def test_trigger(cryo_sim, cryo_node_internal_loop: SECoPNodeDevice):
    cryo_dev: SECoPMoveableDevice = cryo_node_internal_loop.cryo

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

    await cryo_node_internal_loop.disconnect_async()


# async def test_node_read_config(cryo_sim,cryo_node:SECoP_Node_Device):
#    # Node device has no read value, it has to return an empty dict
#    val_desc = await cryo_node.read_configuration()
#    assert  val_desc == {}


async def test_node_drive(cryo_sim, cryo_node_internal_loop: SECoPNodeDevice):
    cryo_dev: SECoPMoveableDevice = cryo_node_internal_loop.cryo

    target_old = await cryo_dev.target.read()

    new_target = 11.0

    old_target = target_old[cryo_dev.target.name].get("value")

    assert old_target != new_target

    stat = cryo_dev.set(new_target=new_target)

    # assert new_target == conf_new.get('target').get('value')

    await stat

    reading = await cryo_dev.value.get_value()

    assert np.isclose(reading, new_target, atol=0.2)

    await cryo_node_internal_loop.disconnect_async()


async def test_node_reconn(
    cryo_sim: xprocess, cryo_node_internal_loop: SECoPNodeDevice, env_vars
):
    frappy_dir, env_dict = env_vars

    class Starter(ProcessStarter):
        # startup pattern
        pattern = ".*: startup done, handling transport messages"
        timeout = 5
        # command to start process
        env = env_dict
        args = [
            "python3",
            frappy_dir + "/bin/frappy-server",
            "-c",
            frappy_dir + "/cfg/cryo_cfg.py",
            "cryo",
        ]

    # cryo_dev: SECoPMoveableDevice = cryo_node_internal_loop.cryo

    old_conf = await cryo_node_internal_loop.read_configuration()

    # disconnect sec-node
    # clean up whole process tree afterwards

    cryo_info: XProcessInfo = cryo_sim.getinfo("cryo_sim")

    cryo_info.terminate(timeout=3)

    await asyncio.sleep(1)

    cryo_sim.ensure("cryo_sim", Starter)

    await asyncio.sleep(2)

    new_conf = await cryo_node_internal_loop.read_configuration()

    fw_old = old_conf[cryo_node_internal_loop.firmware.name]
    fw_new = new_conf[cryo_node_internal_loop.firmware.name]

    assert fw_new["timestamp"] > fw_old["timestamp"]

    await cryo_node_internal_loop.disconnect_async()
