from bssecop.SECoPDevices import SECoP_Node_Device,SECoPMoveableDevice,SECoP_Struct_Device, SECoP_Tuple_Device
import numpy as np
import asyncio
import pytest

from bssecop.AsyncSecopClient import AsyncSecopClient

async def test_nested_connect(nested_struct_sim,nested_node:SECoP_Node_Device):
    assert isinstance(nested_node,SECoP_Node_Device)
    await nested_node.disconnect()
    


async def test_tuple_dev(nested_struct_sim,nested_client:AsyncSecopClient):
    status_dev = SECoP_Tuple_Device(
        secclient=nested_client,
        module_name='ophy_struct',
        parameter_name='status',
        depth=0,
        dev_path=[]
        )
    
    reading = await status_dev.read()
    print(reading)

    await nested_client.disconnect(True)
