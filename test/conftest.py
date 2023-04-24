import pytest
from xprocess import ProcessStarter

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