
 

from frappy.client import SecopClient
from SECoPDevices import SECoP_Node_Device

from frappy.server import Server
from frappy.logging import logger

#secclient = SecopClient('localhost:10769')

#secclient.connect(1)

#print(secclient.online)
#print(secclient.activate)

#print(secclient.getParameter('cryo','value'))

#print(secclient.modules)


#print(secclient.identifier)
#print(secclient.internal)

#print(secclient.descriptive_data)
#secclient.disconnect()

#loglevel = 'debug'
#logger.init(loglevel)


#srv = Server('cryo', logger.log, cfgfiles="cfg/cryo_cfg.py")



cryoNode = SECoP_Node_Device('localhost:10769')

cryoDev = cryoNode.Devices['cryo']


print(cryoDev.__class__.__name__)
print(cryoDev.read())
print(cryoDev.read_configuration())
print(cryoDev.describe())
print(cryoDev.describe_configuration())
#print(cryoNode.properties)


#print(cryoNode.equipment_Id)
#print(cryoNode.secclient.properties)

#print(cryoNode.properties.__class__.__name__)cr

#vals = cryoNode.read_configuration()

#print(vals)


#desc = cryoNode.describe_configuration()

#print(desc)


#print(cryoNode.modules.values())

print(cryoNode.Devices['cryo'].describe())