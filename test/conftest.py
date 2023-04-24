import pytest
from xprocess import ProcessStarter
from bssecop.AsyncSecopClient import AsyncSecopClient
from bssecop.SECoPDevices import SECoP_Node_Device

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
    