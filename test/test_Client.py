# Import bluesky and ophyd
import matplotlib.pyplot as plt
import pytest
from bluesky import RunEngine
from bluesky.callbacks.best_effort import BestEffortCallback
from bluesky.plan_stubs import mov, movr, rd  # noqa
from bluesky.plans import grid_scan  # noqa
from bluesky.utils import ProgressBarManager, register_transform

from ophyd import Component, Device, EpicsSignal, EpicsSignalRO

from ophyd.v2.core import DeviceCollector

#import xprocess
from bssecop.AsyncSecopClient import AsyncSecopClient, SECoPReading

from bssecop.SECoPDevices import SECoP_Node_Device
from frappy.client import SecopClient, CacheItem


import asyncio


async def test_asycnc_secopclient_conn(cryo_sim,cryo_client:AsyncSecopClient):
       
    
  assert cryo_client.online == True
  await cryo_client.disconnect()
  


async def test_asycnc_secopclient_get_Param(cryo_sim,cryo_client:AsyncSecopClient):

  reading = await cryo_client.getParameter('cryo','value',False)
        
        
  assert isinstance( reading,SECoPReading) 
  
  await cryo_client.disconnect()


async def test_async_secopclient_disconnect(cryo_sim,cryo_client:AsyncSecopClient):
  reading = await cryo_client.getParameter('cryo','value',False)
  
  await cryo_client.disconnect(True)
  
  assert cryo_client.state == 'shutdown'




async def test_async_secopclient_reconn(cryo_sim,cryo_client:AsyncSecopClient):
  
  reading1 = await cryo_client.getParameter('cryo','value',False)
  reading2 = await cryo_client.getParameter('cryo','value',False)
  
  assert reading1.get_value() != reading2.get_value()
  
  await cryo_client.disconnect(False)
  
  assert cryo_client.state == 'disconnected'

  await asyncio.sleep(15)
  
  assert cryo_client.state == 'connected'
  
  #ensures we are connected and getting fresh data again
  reading1 = await cryo_client.getParameter('cryo','value',False)
  reading2 = await cryo_client.getParameter('cryo','value',False)
  
  assert reading1.get_value() != reading2.get_value()
  
  await cryo_client.disconnect(True)
  
async def test_async_secopclient_shutdown_and_reconn(cryo_sim,cryo_client:AsyncSecopClient):
  
    reading1 = await cryo_client.getParameter('cryo','value',False)
    reading2 = await cryo_client.getParameter('cryo','value',False)
    
    assert reading1.get_value() != reading2.get_value()
    
    # Shutdown
    await cryo_client.disconnect(True)
    
    assert cryo_client.state == 'shutdown'

    await asyncio.sleep(15)
    
    # ensure no auto reconnect
    assert cryo_client.state == 'shutdown'
    
    #manual reconn
    await cryo_client.connect(1)
    
    assert cryo_client.state == 'connected'
    
    #ensures we are connected and getting fresh data again
    reading1 = await cryo_client.getParameter('cryo','value',False)
    reading2 = await cryo_client.getParameter('cryo','value',False)
    
    assert reading1.get_value() != reading2.get_value()
    
    await cryo_client.disconnect(True)