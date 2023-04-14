import sys



from AsyncSecopClient import AsyncSecopClient

from SECoPDevices import SECoP_Node_Device
from frappy.client import SecopClient
from SECoPSignal import SECoPSignalR ,SECoPSignalRW

import asyncio
import time

async def main():
    

    secclient = AsyncSecopClient('localhost:10769')

    await secclient.connect(1)

    print(secclient.online)
    print(secclient.activate)


    #testSig = SECoPSignalR(path=('cryo','value'),prefix='cryo:',secclient=secclient)

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
    

    cryo = cryoNode.cryo
    
    stat = cryo.set(11)
    
    await stat 
    
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
    asyncio.run(main())