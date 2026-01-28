"""Simple test to verify GenNodeCode refactoring works."""

import sys
from pathlib import Path

from secop_ophyd.GenNodeCode import (
    Attribute,
    GenNodeCode,
    Method,
    ModuleClass,
    NodeClass,
)
from secop_ophyd.SECoPDevices import SECoPNodeDevice

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


def test_basic_functionality(clean_generated_file):
    """Test basic GenNodeCode functionality."""
    print("Testing GenNodeCode refactored implementation...")

    # Create instance
    gen_code = GenNodeCode(path=str(clean_generated_file), log=None)

    # Add some imports
    gen_code.add_import("ophyd_async.core", "Device")

    # Create a simple method
    from inspect import signature

    def sample_method(self, value: int) -> str:
        """Sample method description"""
        return str(value)

    # Method can be created in the old way (backward compatible)
    method = Method(
        cmd_name="sample_command",
        description="Sample command for testing",
        cmd_sign=signature(sample_method),
    )

    # Add a module class
    gen_code.add_mod_class(
        module_cls="TestModule",
        bases=["Device"],
        attrs=[
            ("temperature", "SignalR", "float", None, "parameter"),
            ("pressure", "SignalR", "float", None, "parameter"),
            ("count", "SignalRW", "int", None, "parameter"),
        ],
        cmd_plans=[method],
        description="Test module class",
    )

    # Add a node class
    gen_code.add_node_class(
        node_cls="TestNode",
        bases=["Device"],
        attrs=[
            ("module1", "TestModule", None, None, "module"),
            ("status", "SignalR", "str", None, "property"),
        ],
        description="Test node class",
    )

    # Generate code
    code = gen_code.generate_code()

    gen_code.write_gen_node_class_file()

    print("\n" + "=" * 60)
    print("Generated Code:")
    print("=" * 60)
    print(code)
    print("=" * 60)

    # Verify code contains expected elements
    assert "from abc import abstractmethod" in code
    assert "class TestModule(Device):" in code
    assert "temperature: SignalR[float]" in code
    assert "count: SignalRW[int]" in code
    assert "class TestNode(Device):" in code
    assert "module1: TestModule" in code
    assert "status: SignalR[str]" in code
    assert "def sample_command" in code

    print("\n✓ All basic tests passed!")


def test_dataclasses():
    """Test the new dataclasses."""
    print("\nTesting dataclasses...")

    # Test Attribute
    attr = Attribute(name="test_attr", type="SignalR")
    assert attr.name == "test_attr"
    assert attr.type == "SignalR"

    # Test ModuleClass
    mod_cls = ModuleClass(
        name="TestMod", bases=["Device"], attributes=[attr], methods=[]
    )
    assert mod_cls.name == "TestMod"
    assert len(mod_cls.attributes) == 1

    # Test NodeClass
    node_cls = NodeClass(name="TestNode", bases=["Device"], attributes=[attr])
    assert node_cls.name == "TestNode"

    print("✓ Dataclass tests passed!")


def test_subsequent_node_generation(clean_generated_file):
    """Test generating code for two nodes sequentially, appending to the same file.

    Tests that:
    - First: Generate NodeA with modules Type1 and Type2, write to file
    - Second: Load existing file, add NodeB with Type1 (shared) and Type3 (new)
    - Type1 should appear only once in the final file (not duplicated)
    - All classes (Type1, Type2, Type3, NodeA, NodeB) are in the final file
    """
    print("\nTesting subsequent node generation with file appending...")
    print(f"Using output directory: {clean_generated_file}")

    from inspect import signature

    # ===== STEP 1: Generate and write first node (NodeA) =====
    print("\n--- Step 1: Generating NodeA ---")
    gen_code1 = GenNodeCode(path=str(clean_generated_file), log=None)

    # Add necessary imports
    gen_code1.add_import("ophyd_async.core", "Device")

    # Create sample methods
    def type1_command(self, value: float) -> float:
        """Type1 command"""
        return value * 2.0

    method_type1 = Method(
        cmd_name="type1_cmd",
        description="Type1 command",
        cmd_sign=signature(type1_command),
    )

    def type2_command(self, mode: str) -> str:
        """Type2 command"""
        return f"Mode: {mode}"

    method_type2 = Method(
        cmd_name="type2_cmd",
        description="Type2 command",
        cmd_sign=signature(type2_command),
    )

    # Add module class Type1 (will be shared)
    gen_code1.add_mod_class(
        module_cls="Type1",
        bases=["Device"],
        attrs=[
            ("description", "SignalR", "str", None, "property"),
            ("interface_classes", "SignalR", "list", None, "property"),
            ("temperature", "SignalR", "float", None, "parameter"),
            ("setpoint", "SignalRW", "float", None, "parameter"),
        ],
        cmd_plans=[method_type1],
        description="Type1 module - shared between nodes",
    )

    # Add module class Type2 (only in nodeA)
    gen_code1.add_mod_class(
        module_cls="Type2",
        bases=["Device"],
        attrs=[
            ("implementation", "SignalR", "str", None, "property"),
            ("pressure", "SignalR", "float", None, "parameter"),
            ("mode", "SignalRW", "str", None, "parameter"),
        ],
        cmd_plans=[method_type2],
        description="Type2 module - only in nodeA",
    )

    # Add nodeA
    gen_code1.add_node_class(
        node_cls="NodeA",
        bases=["Device"],
        attrs=[
            ("modA", "Type1", None, None, "module"),
            ("modB", "Type2", None, None, "module"),
            ("status", "SignalR", "str", None, "property"),
        ],
        description="NodeA with Type1 and Type2 modules",
    )

    # Generate and write first node
    code1 = gen_code1.generate_code()
    gen_code1.write_gen_node_class_file()

    print("\n" + "=" * 60)
    print("First Generation (NodeA):")
    print("=" * 60)
    print(code1)

    # Verify first generation
    assert "class Type1(Device):" in code1
    assert "class Type2(Device):" in code1
    assert "class NodeA(Device):" in code1
    assert "modA: Type1" in code1
    assert "modB: Type2" in code1

    # ===== STEP 2: Load existing file and add second node (NodeB) =====
    print("\n--- Step 2: Loading file and adding NodeB ---")
    gen_code2 = GenNodeCode(path=str(clean_generated_file), log=None)

    # Add necessary imports again
    gen_code2.add_import("ophyd_async.core", "Device")

    # Create method for Type3
    def type3_command(self, count: int) -> int:
        """Type3 command"""
        return count + 1

    method_type3 = Method(
        cmd_name="type3_cmd",
        description="Type3 command",
        cmd_sign=signature(type3_command),
    )

    # Add Type1 again - GenNodeCode should detect it already exists
    gen_code2.add_mod_class(
        module_cls="Type1",
        bases=["Device"],
        attrs=[
            ("description", "SignalR", "str", None, "property"),
            ("interface_classes", "SignalR", "list", None, "property"),
            ("temperature", "SignalR", "float", None, "parameter"),
            ("setpoint", "SignalRW", "float", None, "parameter"),
        ],
        cmd_plans=[method_type1],
        description="Type1 module - shared between nodes",
    )

    # Add module class Type3 (only in nodeB)
    gen_code2.add_mod_class(
        module_cls="Type3",
        bases=["Device"],
        attrs=[
            ("group", "SignalR", "str", None, "property"),
            ("count", "SignalRW", "int", None, "parameter"),
            ("enabled", "SignalR", "bool", None, "parameter"),
        ],
        cmd_plans=[method_type3],
        description="Type3 module - only in nodeB",
    )

    # Add nodeB
    gen_code2.add_node_class(
        node_cls="NodeB",
        bases=["Device"],
        attrs=[
            ("modA", "Type1", None, None, "module"),
            ("modB", "Type3", None, None, "module"),
            ("name", "SignalR", "str", None, "property"),
        ],
        description="NodeB with Type1 and Type3 modules",
    )

    # Generate and write second node (appends to the file)
    code2 = gen_code2.generate_code()
    gen_code2.write_gen_node_class_file()

    print("\n" + "=" * 60)
    print("Second Generation (NodeA + NodeB):")
    print("=" * 60)
    print(code2)

    # ===== VERIFICATION =====
    # Verify that Type1 appears only once in the final code
    type1_count = code2.count("class Type1(Device):")
    print(f"\nType1 class count: {type1_count}")
    assert (
        type1_count == 1
    ), f"Type1 should appear exactly once, but appears {type1_count} times"

    # Verify all module classes are present
    assert "class Type1(Device):" in code2
    assert "class Type2(Device):" in code2
    assert "class Type3(Device):" in code2

    # Verify both node classes are present
    assert "class NodeA(Device):" in code2
    assert "class NodeB(Device):" in code2

    # Verify all methods are present
    assert "def type1_cmd" in code2
    assert "def type2_cmd" in code2
    assert "def type3_cmd" in code2

    # Verify section comments are present
    assert "# Module Properties" in code2
    assert "# Module Parameters" in code2


def test_gen_cryo_node(
    clean_generated_file, cryo_sim, cryo_node_no_re: SECoPNodeDevice
):
    """Test generating code for a real SECoP node."""

    cryo_node_no_re.class_from_instance(clean_generated_file)


async def test_gen_real_node(
    clean_generated_file,
    nested_struct_sim,
    RE,
    nested_node_no_re: SECoPNodeDevice,  # noqa: N803
):

    nested_node_no_re.class_from_instance(clean_generated_file)

    # Read the generated file and verify its contents
    gen_file = clean_generated_file / "genNodeClass.py"
    assert gen_file.exists(), "Generated file should exist"

    generated_code = gen_file.read_text()

    # ===== Assertions for generated command plans =====
    # The ophy_struct module has a test_cmd command
    assert "def test_cmd" in generated_code, "test_cmd plan should be generated"
    assert (
        "@abstractmethod" in generated_code
    ), "Command methods should be marked as abstract"

    # ===== Assertions for generated enum classes =====
    # Enum classes should be generated for enum parameters
    # The gas_type parameter in enum1/enum2 modules should generate enum classes
    assert (
        "class Test_EnumGas_typeEnum(SupersetEnum):" in generated_code
    ), "Enum class for gas_type should be generated"

    # Verify enum members are present
    # gas_type enums should have AR, N2, H2 (and CO2 for enum2)
    assert "AR" in generated_code, "AR enum member should be present"
    assert "N2" in generated_code, "N2 enum member should be present"
    assert "H2" in generated_code, "H2 enum member should be present"
    assert "CO2" in generated_code, "CO2 enum member should be present"

    # Verify SupersetEnum import
    assert (
        "from enum import Enum" in generated_code or "SupersetEnum" in generated_code
    ), "Enum import should be present"
