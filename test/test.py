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


from bssecop.AsyncSecopClient import AsyncSecopClient

from bssecop.SECoPDevices import SECoP_Node_Device
from frappy.client import SecopClient
from bssecop.SECoPSignal import SECoPSignalR ,SECoPSignalRW


@pytest.mark.asyncio
async def test_always_true():
    secclient = AsyncSecopClient('localhost:10769')
    
    await secclient.connect(1)
    
    
    assert secclient.online == True
    print(secclient.modules)


test_always_true()