# Import bluesky and ophyd
import matplotlib.pyplot as plt
from bluesky import RunEngine
from bluesky.callbacks.best_effort import BestEffortCallback
from bluesky.plan_stubs import mov, movr, rd  # noqa
from bluesky.plans import grid_scan  # noqa
from bluesky.utils import ProgressBarManager, register_transform

from ophyd import Component, Device, EpicsSignal, EpicsSignalRO

from ophyd.v2.core import DeviceCollector


from AsyncSecopClient import AsyncSecopClient

from SECoPDevices import SECoP_Node_Device
from frappy.client import SecopClient
from SECoPSignal import SECoPSignalR ,SECoPSignalRW

import asyncio




def test_always_true():
    secclient = AsyncSecopClient('localhost:10769')
    
    asyncio.run(secclient.connect(1))
    print('ah')


test_always_true()