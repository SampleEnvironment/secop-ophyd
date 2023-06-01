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
  


def test_asycnc_secopclient_get_Param(cryo_sim,cryo_client:AsyncSecopClient):

  fut = asyncio.run_coroutine_threadsafe(
        cryo_client.getParameter('cryo','value',False),
        loop=cryo_client.loop,
        )
  
  reading = fut.result(timeout = 2)
  assert isinstance( reading,SECoPReading) 





