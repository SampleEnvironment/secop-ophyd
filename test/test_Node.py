from bssecop.SECoPDevices import SECoP_Node_Device,SECoPMoveableDevice
import numpy as np

async def test_node_structure(cryo_sim,cryo_node):
    assert isinstance(cryo_node,SECoP_Node_Device)
    
async def test_node_read(cryo_sim,cryo_node:SECoP_Node_Device):
    # Node device has no read value, it has to return an empty dict
    val_read = await cryo_node.read()
    assert  val_read == {}
    
async def test_node_describe(cryo_sim,cryo_node:SECoP_Node_Device):
    # Node device has no read value, it has to return an empty dict
    val_desc = await cryo_node.describe()
    assert  val_desc == {}
    

#async def test_node_read_config(cryo_sim,cryo_node:SECoP_Node_Device):
#    # Node device has no read value, it has to return an empty dict
#    val_desc = await cryo_node.read_configuration()
#    assert  val_desc == {}


async def test_node_drive(cryo_sim,cryo_node:SECoP_Node_Device):
    # Node device has no read value, it has to return an empty dict
    cryo_dev:SECoPMoveableDevice = cryo_node.cryo
    
    conf_old = await cryo_dev.read_configuration()
    print(conf_old)
    new_target = 11
    
    old_target = conf_old.get(cryo_dev.target.name).get('value')

    
    stat =  cryo_dev.set(new_target=new_target) 
    
    conf_new = await cryo_dev.read_configuration()

    #assert new_target == conf_new.get('target').get('value')
    
    await stat       

    reading = await cryo_dev.read()
    
    assert np.isclose(reading.get(cryo_dev.value.name).get('value'),new_target,atol=0.2)
    
    
async def test_node_drive_second(cryo_sim,cryo_node:SECoP_Node_Device):
    # Node device has no read value, it has to return an empty dict
    cryo_dev:SECoPMoveableDevice = cryo_node.cryo
    
    conf_old = await cryo_dev.read_configuration()
    
    new_target = 11
    
    old_target = conf_old.get('target').get('value')

    assert old_target != new_target
    
    stat = await cryo_dev.set(new_target=new_target) 
    
    conf_new = await cryo_dev.read_configuration()

    #assert new_target == conf_new.get('target').get('value')
    
    await stat       

    reading = await cryo_dev.read()
    
    assert np.isclose(reading.get('value').get('value'),new_target,atol=0.2)