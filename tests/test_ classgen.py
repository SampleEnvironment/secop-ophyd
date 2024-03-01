import os

from secop_ophyd.SECoPDevices import SECoP_Node_Device


async def test_class_gen(nested_struct_sim, nested_node: SECoP_Node_Device):
    nested_node.class_from_instance()

    if os.path.exists("genNodeClass.py"):
        os.remove("genNodeClass.py")

    await nested_node.disconnect_async()


async def test_class_gen_path(nested_struct_sim, nested_node: SECoP_Node_Device):
    nested_node.class_from_instance("tests/testgen")

    if os.path.exists("tests/testgen/genNodeClass.py"):
        os.remove("tests/testgen/genNodeClass.py")

    await nested_node.disconnect_async()
