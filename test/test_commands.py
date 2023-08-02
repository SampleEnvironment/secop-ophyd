from secop_ophyd.SECoPDevices import SECoP_Node_Device,SECoPMoveableDevice,SECoP_Struct_Device, SECoP_Tuple_Device,SECoP_CMD_Device
import numpy as np
import asyncio
import pytest
from ophyd.v2.core import SignalRW, SignalX,SignalR
from secop_ophyd.util import Path
from frappy.lib.enum import EnumMember
from secop_ophyd.AsyncSecopClient import AsyncFrappyClient

async def test_stop_cmd(cryo_sim,cryo_node_internal_loop:SECoP_Node_Device):
    cryo:SECoPMoveableDevice = cryo_node_internal_loop.cryo

    stat = cryo.set(15)
    
    await asyncio.sleep(3)

    await cryo.stop(True)

    await stat

    assert cryo._stopped is True

async def test_stop_no_sucess_cmd(cryo_sim,cryo_node_internal_loop:SECoP_Node_Device):
    cryo:SECoPMoveableDevice = cryo_node_internal_loop.cryo

    stat = cryo.set(15)
    
    rt_error = False

    await asyncio.sleep(3)

    try:
        await cryo.stop(False)
        await stat
        
    except RuntimeError:
        rt_error = True
        
        
 
    assert cryo._stopped is True
    assert rt_error is True


async def test_nested_connect(nested_struct_sim,nested_node:SECoP_Node_Device):
    test_cmd:SECoP_CMD_Device = nested_node.ophy_struct.test_cmd_dev

    
    await test_cmd.name_arg.set("test_name")
    await test_cmd.id_arg.set(1233)
    await test_cmd.sort_arg.set(False)

    res:SignalR = test_cmd.test_cmd_res



    run_obj:SignalX = test_cmd.test_cmd_x
    
    await run_obj.execute()

    reading_res = await res.read()
    assert reading_res.get(res.name).value is not None


    await nested_node.disconnect()