import os

from secop_ophyd.SECoPDevices import SECoP_Node_Device


async def test_class_gen(
    nested_struct_sim, nested_node: SECoP_Node_Device
):
    nested_node.class_from_instance()

    if os.path.exists("genNodeClass.py"):
        os.remove("genNodeClass.py")
