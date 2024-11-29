# mypy: disable-error-code="attr-defined"
import os

from secop_ophyd.SECoPDevices import SECoPNodeDevice


async def test_class_gen(nested_struct_sim, nested_node: SECoPNodeDevice):
    nested_node.class_from_instance()

    if os.path.exists("genNodeClass.py"):
        os.remove("genNodeClass.py")

    await nested_node.disconnect_async()


async def test_class_gen_path(nested_struct_sim, nested_node: SECoPNodeDevice):
    nested_node.class_from_instance("tests")

    if os.path.exists("tests/genNodeClass.py"):
        os.remove("tests/genNodeClass.py")

    await nested_node.disconnect_async()
