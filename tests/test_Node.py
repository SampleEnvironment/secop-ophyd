import pytest
from secop_ophyd.SECoPDevices import SECoP_Node_Device, SECoPMoveableDevice
import numpy as np
from xprocess import ProcessStarter, XProcessInfo
import xprocess

import asyncio


async def test_node_structure(cryo_sim, cryo_node_internal_loop: SECoP_Node_Device):
    assert isinstance(cryo_node_internal_loop, SECoP_Node_Device)
    await cryo_node_internal_loop.disconnect()


async def test_node_read(cryo_sim, cryo_node_internal_loop: SECoP_Node_Device):
    # Node device has no read value, it has to return an empty dict
    val_read = await cryo_node_internal_loop.read()
    assert val_read == {}
    await cryo_node_internal_loop.disconnect()


async def test_node_describe(cryo_sim, cryo_node_internal_loop: SECoP_Node_Device):
    # Node device has no read value, it has to return an empty dict
    val_desc = await cryo_node_internal_loop.describe()
    assert val_desc == {}
    await cryo_node_internal_loop.disconnect()


async def test_dev_read(cryo_sim, cryo_node_internal_loop: SECoP_Node_Device):
    # Node device has no read value, it has to return an empty dict
    cryo_dev: SECoPMoveableDevice = cryo_node_internal_loop.cryo
    cryo_val = await cryo_dev.read()
    val_name = cryo_dev.value.name
    assert cryo_val[val_name].get("value") > 5
    await cryo_node_internal_loop.disconnect()


# async def test_node_read_config(cryo_sim,cryo_node:SECoP_Node_Device):
#    # Node device has no read value, it has to return an empty dict
#    val_desc = await cryo_node.read_configuration()
#    assert  val_desc == {}


async def test_node_drive(cryo_sim, cryo_node_internal_loop: SECoP_Node_Device):
    cryo_dev: SECoPMoveableDevice = cryo_node_internal_loop.cryo

    target_old = await cryo_dev.target.read()

    new_target = 11.0

    old_target = target_old[cryo_dev.target.name].get("value")

    assert old_target != new_target

    stat = cryo_dev.set(new_target=new_target)

    # assert new_target == conf_new.get('target').get('value')

    await stat

    reading = await cryo_dev.read()

    assert np.isclose(
        reading.get(cryo_dev.value.name).get("value"), new_target, atol=0.2
    )

    await cryo_node_internal_loop.disconnect()


async def test_node_reconn(
    cryo_sim: xprocess,
    cryo_node_internal_loop: SECoP_Node_Device,
):
    class Starter(ProcessStarter):
        # startup pattern
        pattern = ".*: startup done, handling transport messages"
        timeout = 10
        # command to start process
        args = [
            "python3",
            "../../../../frappy/bin/frappy-server",
            "-c",
            "../../../../frappy/cfg/cryo_cfg.py",
            "cryo",
        ]

    cryo_dev: SECoPMoveableDevice = cryo_node_internal_loop.cryo

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
