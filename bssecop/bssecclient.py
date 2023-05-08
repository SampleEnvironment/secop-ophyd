import sys



from bssecop.AsyncSecopClient import AsyncSecopClient

from bssecop.SECoPDevices import SECoP_Node_Device, SECoPMoveableDevice

from bssecop.SECoPSignal import SECoPSignalR ,SECoPSignalRW
from frappy.client import SecopClient
import asyncio
import time

async def main():

    secclient_threaded = SecopClient("localhost:10769")
    secclient_threaded.connect(1)

    secclient = AsyncSecopClient(host='localhost',port='10769')

    await secclient.connect(1)



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



    cryoNode = SECoP_Node_Device(secclient=secclient)
    
    #print(secclient.properties)
    #print(cryoNode.equipment_Id)
    #print(cryoNode.properties)
    

    cryo:SECoPMoveableDevice = cryoNode.cryo
    #cryoNode.set_name('sample_changer')
    await cryo.target._backend.put(10)
    new_conf = await cryo.read_configuration()
    print(new_conf.get(cryo.target.name))
    
    stat =  cryo.set(13.5)
    new_conf = await cryo.read_configuration()
    print(new_conf.get(cryo.target.name))
    await stat 
    
    print(await cryo.read_configuration())
    
    
    print(await cryo.read())
    
    while True:
        time.sleep(1)
        print(await cryo.read())
    
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
    asyncio.run(main(),debug=False)