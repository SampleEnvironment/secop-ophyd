

from frappy.client import SecopClient
from SECoPDevices import SECoP_Node_Device

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

cryoNode = SECoP_Node_Device('localhost:10769')


#print(cryoNode.equipment_Id)
#print(cryoNode.secclient.properties)

#print(cryoNode.properties.__class__.__name__)

#vals = cryoNode.read_configuration()

#print(vals)


#desc = cryoNode.describe_configuration()

#print(desc)


#print(cryoNode.modules.values())

print(cryoNode.Devices['cryo'].describe())