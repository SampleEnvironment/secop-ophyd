import sys


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



from bssecop.AsyncSecopClient import AsyncSecopClient

from bssecop.SECoPDevices import SECoP_Node_Device, SECoPMoveableDevice

from frappy.client import SecopClient
import asyncio
import time

from concurrent.futures import Future






async def main( ):
    
    # Create a run engine, with plotting, progressbar and transform
    RE = RunEngine({}, call_returns_result=True)
    bec = BestEffortCallback()
    RE.subscribe(bec)
    RE.waiting_hook = ProgressBarManager()
    RE.ignore_callback_exceptions = False
    plt.ion()


    #register_transform("RE", prefix="<")



    secclient = AsyncSecopClient(host='localhost',port='10769', loop=RE._loop)
    future =asyncio.run_coroutine_threadsafe(secclient.connect(1),loop=RE._loop)
    
    while(not future.done()):
        pass



 

    cryoNode = SECoP_Node_Device(secclient=secclient)
    
    p = RE(rd(cryoNode.cryo))
    p = RE(count([cryoNode.cryo],num=5,delay=1))
    print(p)
    #testSig = SECoPSignalR(path=('cryo','value'),prefix='cryo:',secclient=secclient      )

    #testSigRW = SECoPSignalRW(path=('cryo','target'),prefix='cryo:',secclient=secclient)
    
    
    #print( await testSigRW.read())
    #secclient.disconnect()
    
    #await secclient.connect(2)
    #print( await testSigRW.describe())


    #await testSigRW.set(20)
    
    #print( await testSigRW.read())
    #await testSigRW.set(21)
    #print( await testSigRW.read())
    #_signal_desc = secclient.modules.get('cryo').get('accessibles').get('value')



    #
    
    #print(secclient.properties)
    #print(cryoNode.equipment_Id)
    #print(cryoNode.properties)
    

    #cryo:SECoPMoveableDevice = cryoNode.cryo
    #cryoNode.set_name('sample_changer')
    #await cryo.target._backend.put(10)
    #new_conf = await cryo.read_configuration()
    #print(new_conf.get(cryo.target.name))
    
    #stat =  cryo.set(13.5)
    #new_conf = await cryo.read_configuration()
    #print(new_conf.get(cryo.target.name))
    #await asyncio.sleep(5)
    #await cryo.stop(False)
    #await stat 
    
    #print(await cryo.read_configuration())
    
    
    #print(await cryo.read())
    
    #while True:
    #    time.sleep(1)
    #    print(await cryo.read())
    
   # print(await cryoNode.describe_configuration())
#print(secclient.getParameter('cryo','value'))

#print(secclient.modules)


#print(secclient.identifier)
#print(secclient.internal)

#print(secclient.descriptive_data)
#secclient.disconnect()

#loglevel = 'debug'
#logger.init(loglevel)


#srv = Server('cryo', logger.log, cfgfiles="cfg/cryo_cfg.py")



#cryoNode = SECoP_Node_Device('localhost:10769')



#cryo = cryoNode.cryo


#print(cryo.describe_configuration())
#print("\n")

#r = cryo.read_configuration()

#print(cryo.read_configuration())

#print(cryoDev.read_configuration())
#print(cryoDev.describe())
#print(cryoDev.describe_configuration())
#print(cryoNode.properties)


#print(cryoNode.equipment_Id)
#print(cryoNode.secclient.properties)

#print(cryoNode.properties.__class__.__name__)cr

#vals = cryoNode.read_configuration()

#print(vals)


#desc = cryoNode.describe_configuration()

#print(desc)


#print(cryoNode.modules.values())

#print(cryoNode.Devices['cryo'].describe_configuration())



if __name__ == "__main__":
    asyncio.run(main(),debug=True)