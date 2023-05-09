from bssecop.SECoPDevices import SECoP_Node_Device,SECoPMoveableDevice
# Import bluesky and ophyd
import matplotlib.pyplot as plt
from bluesky import RunEngine
from bluesky.callbacks.best_effort import BestEffortCallback
from bluesky.plan_stubs import *
from bluesky.plans import *
from bluesky.utils import ProgressBarManager, register_transform

from ophyd import Component, Device, EpicsSignal, EpicsSignalRO
from ophyd.v2 import epicsdemo
from ophyd.v2.core import DeviceCollector

def test_RE_count(cryo_sim,cryo_node):
    # Create a run engine, with plotting, progressbar and transform
    RE = RunEngine({}, call_returns_result=True)
    bec = BestEffortCallback()
    RE.subscribe(bec)
    RE.waiting_hook = ProgressBarManager()
    plt.ion()

    
    cryo_dev:SECoPMoveableDevice = cryo_node.cryo
    
    
    
    RE(read(cryo_dev))
    
    assert True
    
