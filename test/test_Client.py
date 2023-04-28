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
from bssecop.AsyncSecopClient import AsyncSecopClient, CacheReading

from bssecop.SECoPDevices import SECoP_Node_Device
from frappy.client import SecopClient, CacheItem
from bssecop.SECoPSignal import SECoPSignalR ,SECoPSignalRW



async def test_asycnc_secopclient_conn(cryo_sim,cryo_client):
       
    
  assert cryo_client.online == True



async def test_asycnc_secopclient_get_Param(cryo_sim,cryo_client):
  reading = await cryo_client.getParameter('cryo','value',False)

  assert isinstance( reading,CacheReading) 





