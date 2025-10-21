# mypy: disable-error-code="attr-defined"
import os

from secop_ophyd.SECoPDevices import SECoPNodeDevice


async def test_class_gen(nested_struct_sim, nested_node_no_re: SECoPNodeDevice):
    nested_node_no_re.class_from_instance()

    if os.path.exists("genNodeClass.py"):
        os.remove("genNodeClass.py")


async def test_class_gen_path(nested_struct_sim, nested_node_no_re: SECoPNodeDevice):
    nested_node_no_re.class_from_instance("tests")

    if os.path.exists("tests/genNodeClass.py"):
        os.remove("tests/genNodeClass.py")
