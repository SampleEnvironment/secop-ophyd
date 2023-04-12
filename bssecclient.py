import sys



from AsyncSecopClient import AsyncSecopClient

from SECoPDevices import SECoP_Node_Device
from frappy.client import SecopClient
from SECoPSignal import SECoPSignalR

import asyncio


async def main():
    

    secclient = AsyncSecopClient('localhost:10769')

    await secclient.connect(1)

    print(secclient.online)
    print(secclient.activate)


    testSig = SECoPSignalR(path=('cryo','value'),prefix='cryo:',secclient=secclient)


    print( await testSig.read())
    print( await testSig.describe())
    
    _signal_desc = secclient.modules.get('cryo').get('accessibles').get('value')


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