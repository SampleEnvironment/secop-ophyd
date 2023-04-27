import pytest
from xprocess import ProcessStarter
from bssecop.AsyncSecopClient import AsyncSecopClient
from bssecop.SECoPDevices import SECoP_Node_Device

# Import bluesky and ophyd
import matplotlib.pyplot as plt
from bluesky import RunEngine
from bluesky.callbacks.best_effort import BestEffortCallback
from bluesky.plan_stubs import mov, movr, rd  # noqa
from bluesky.plans import grid_scan  # noqa
from bluesky.utils import ProgressBarManager, register_transform

@pytest.fixture
def cryo_sim(xprocess):
    class Starter(ProcessStarter):
        # startup pattern
        pattern = ".*: startup done, handling transport messages"
        timeout = 10
        # command to start process
        args = ['python3', '../../../../frappy/bin/frappy-server', '-c', '../../../../frappy/cfg/cryo_cfg.py','cryo']

    # ensure process is running and return its logfile
    logfile = xprocess.ensure("cryo_sim", Starter)


    yield 

    # clean up whole process tree afterwards
    xprocess.getinfo("cryo_sim").terminate()
    
@pytest.fixture
async def cryo_client(cryo_sim):
    secclient = AsyncSecopClient('localhost:10769')

    await secclient.connect(1)
    
    return secclient

@pytest.fixture
async def cryo_node(cryo_client,cryo_sim):
    return SECoP_Node_Device(secclient=cryo_client)
    
@pytest.fixture
def bluesky_runengine():
    # Create a run engine, with plotting, progressbar and transform
    RE = RunEngine({}, call_returns_result=True)
    bec = BestEffortCallback()
    RE.subscribe(bec)
    RE.waiting_hook = ProgressBarManager()
    plt.ion()
    register_transform("RE", prefix="<")