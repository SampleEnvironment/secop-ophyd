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
import logging


import asyncio

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
def nested_struct_sim(xprocess):
    class Starter(ProcessStarter):
        # startup pattern
        pattern = ".*: startup done, handling transport messages"
        timeout = 10
        # command to start process
        args = ['python3', '../../../../frappy/bin/frappy-server', '-c', '../../../../frappy/cfg/ophyd_secop_test_cfg.py','nested']

    pname = 'nested'
    # ensure process is running and return its logfile
    logfile = xprocess.ensure(pname, Starter)


    yield 

    # clean up whole process tree afterwards
    xprocess.getinfo(pname).terminate()

@pytest.fixture
def logger():
    class NoRXFilter(logging.Filter):
        def filter(self, record):
            return not record.getMessage().startswith('RX')
    class NoTXFilter(logging.Filter):
        def filter(self, record):
            return not record.getMessage().startswith('TX')




    logger = logging.getLogger('async_client')
    logger.setLevel(logging.DEBUG)

    logger.addFilter(NoRXFilter())
    logger.addFilter(NoTXFilter())

    logging.basicConfig(
        datefmt='%d/%m/%Y %I:%M:%S',
        format='%(asctime)s.%(msecs)03d  %(name)-12s %(levelname)-8s %(message)s',
        filename='asyncclient.log',
        encoding='utf-8',
        level=logging.DEBUG,
        filemode='w')
    
    return logger
    
    
@pytest.fixture
async def client(cryo_sim,logger,port = '10769'):
    
   
    loop =asyncio.get_running_loop()

    return await AsyncSecopClient.create(host='localhost',port=port,loop=loop,log=logger)

@pytest.fixture
async def nested_client(cryo_sim,logger,port = '10771'):
    
    loop =asyncio.get_running_loop()

    return await AsyncSecopClient.create(host='localhost',port=port,loop=loop,log=logger)
    
@pytest.fixture
def RE():
    RE = RunEngine({}, call_returns_result=True)
    bec = BestEffortCallback()
    RE.subscribe(bec)
    RE.waiting_hook = ProgressBarManager()
    plt.ion()
    return RE

@pytest.fixture
def cryo_node(RE):
    return SECoP_Node_Device.create_external_loop(host='localhost',port='10769',loop=RE.loop)
    
@pytest.fixture
async def cryo_node_internal_loop():
    return await SECoP_Node_Device.create(host='localhost',port='10769',loop=asyncio.get_running_loop())
    

@pytest.fixture
async def nested_node():
    return await SECoP_Node_Device.create(host='localhost',port='10771',loop=asyncio.get_running_loop())


@pytest.fixture
def struct_val():
    return {"number": 5.0, "string": "blablalbla", "tupl": [1.0, 1.0, 1.0], "pos_struct": {"x": 5.0, "y": 10.0, "z": 15.0, "col": "green", "enum": 1}}

@pytest.fixture
def nested_param_description():
    return {"_nested_struct":{
          "datainfo":{
            "members":{
              "number":{
                "max":100.0,
                "min":0.0,
                "type":"double",
                "unit":"s"
              },
              "pos_struct":{
                "members":{
                  "col":{
                    "type":"string"
                  },
                  "enum":{
                    "members":{
                      "mode_max":2,
                      "mode_one":1,
                      "mode_zero":0
                    },
                    "type":"enum"
                  },
                  "x":{
                    "max":100.0,
                    "min":0.0,
                    "type":"double",
                    "unit":"m"
                  },
                  "y":{
                    "max":100.0,
                    "min":0.0,
                    "type":"double",
                    "unit":"m"
                  },
                  "z":{
                    "max":100.0,
                    "min":0.0,
                    "type":"double",
                    "unit":"m"
                  }
                },
                "type":"struct"
              },
              "string":{
                "type":"string"
              },
              "tupl":{
                "members":[
                  {
                    "max":100.0,
                    "min":0.0,
                    "type":"double"
                  },
                  {
                    "max":100.0,
                    "min":0.0,
                    "type":"double"
                  },
                  {
                    "max":100.0,
                    "min":0.0,
                    "type":"double"
                  }
                ],
                "type":"tuple"
              }
            },
            "type":"struct"
          },
          "description":"nestedstruct dict containing other structs and tuples ",
          "readonly":True
        }
        }