from bssecop.SECoPDevices import SECoP_Node_Device,SECoPMoveableDevice,SECoP_Struct_Device, SECoP_Tuple_Device
import numpy as np
import asyncio
import pytest
from bssecop.util import Path
from frappy.lib.enum import EnumMember
from bssecop.AsyncSecopClient import AsyncFrappyClient

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
    assert isinstance(nested_node,SECoP_Node_Device)
    await nested_node.disconnect()