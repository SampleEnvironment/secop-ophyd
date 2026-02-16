"""Simple test to verify GenNodeCode refactoring works."""

import sys
from pathlib import Path

from ophyd_async.core import SignalR, init_devices

from secop_ophyd.GenNodeCode import (
    GenNodeCode,
    Method,
    ModuleAttribute,
    ModuleClass,
    NodeClass,
    ParameterAttribute,
    PropertyAttribute,
)
from secop_ophyd.SECoPDevices import ParamPath, PropPath, SECoPNodeDevice

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
        bases=["SECoPDevice"],
        parameters=[
            ParameterAttribute(
                name="temperature",
                type="SignalR",
                type_param="float",
                path_annotation=str(ParamPath("test:temperature")),
            ),
            ParameterAttribute(
                name="pressure",
                type="SignalR",
                type_param="float",
                path_annotation=str(ParamPath("test:pressure")),
            ),
            ParameterAttribute(
                name="count",
                type="SignalRW",
                type_param="int",
                path_annotation=str(ParamPath("test:count")),
            ),
        ],
        properties=[],
        cmd_plans=[method],
        description="Test module class",
    )

    # Add a node class
    gen_code.add_node_class(
        node_cls="TestNode",
        bases=["SECoPNodeDevice"],
        modules=[
            ModuleAttribute(name="module1", type="TestModule"),
        ],
        properties=[
            PropertyAttribute(
                name="status",
                type="SignalR",
                type_param="str",
                path_annotation=str(PropPath("status")),
            ),
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
    assert "class TestModule(SECoPDevice):" in code
    assert 'temperature: A[SignalR[float], ParamPath("test:temperature")]' in code
    assert 'count: A[SignalRW[int], ParamPath("test:count")]' in code
    assert "class TestNode(SECoPNodeDevice):" in code
    assert "module1: TestModule" in code
    assert 'status: A[SignalR[str], PropPath("status")]' in code
    assert "def sample_command" in code

    print("\n✓ All basic tests passed!")


def test_dataclasses():
    """Test the new dataclasses."""
    print("\nTesting dataclasses...")

    # Test ParameterAttribute
    param_attr = ParameterAttribute(name="test_param", type="SignalR")
    assert param_attr.name == "test_param"
    assert param_attr.type == "SignalR"

    # Test PropertyAttribute
    prop_attr = PropertyAttribute(name="test_prop", type="SignalR")
    assert prop_attr.name == "test_prop"
    assert prop_attr.type == "SignalR"

    # Test ModuleAttribute
    mod_attr = ModuleAttribute(name="test_mod", type="TestModule")
    assert mod_attr.name == "test_mod"
    assert mod_attr.type == "TestModule"

    # Test ModuleClass
    mod_cls = ModuleClass(
        name="TestMod",
        bases=["Device"],
        parameters=[param_attr],
        properties=[prop_attr],
        methods=[],
    )
    assert mod_cls.name == "TestMod"
    assert len(mod_cls.parameters) == 1
    assert len(mod_cls.properties) == 1

    # Test NodeClass
    node_cls = NodeClass(
        name="TestNode", bases=["Device"], modules=[mod_attr], properties=[prop_attr]
    )
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

    from inspect import signature

    # ===== STEP 1: Generate and write first node (NodeA) =====

    gen_code1 = GenNodeCode(path=str(clean_generated_file), log=None)

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
        bases=["SECoPDevice"],
        parameters=[
            ParameterAttribute(
                name="temperature",
                type="SignalR",
                type_param="float",
                description="this has to be in the final output",
                path_annotation="ParamPath('type1:temperature')",
            ),
            ParameterAttribute(
                name="setpoint",
                type="SignalRW",
                type_param="float",
                path_annotation="ParamPath('type1:setpoint')",
            ),
        ],
        properties=[
            PropertyAttribute(
                name="description",
                type="SignalR",
                type_param="str",
                path_annotation="PropPath('type1:description')",
            ),
            PropertyAttribute(
                name="interface_classes",
                type="SignalR",
                type_param="int",
                path_annotation="PropPath('type1:interface_classes')",
            ),
        ],
        cmd_plans=[method_type1],
        description="Type1 module - shared between nodes",
    )

    # Add module class Type2 (only in nodeA)
    gen_code1.add_mod_class(
        module_cls="Type2",
        bases=["SECoPDevice"],
        parameters=[
            ParameterAttribute(
                name="pressure",
                type="SignalR",
                type_param="float",
                path_annotation="ParamPath('type2:pressure')",
            ),
            ParameterAttribute(
                name="mode",
                type="SignalRW",
                type_param="str",
                path_annotation="ParamPath('type2:mode')",
            ),
        ],
        properties=[
            PropertyAttribute(
                name="implementation",
                type="SignalR",
                type_param="str",
                path_annotation="PropPath('type2:implementation')",
            ),
        ],
        cmd_plans=[method_type2],
        description="Type2 module - only in nodeA",
    )

    # Add nodeA
    gen_code1.add_node_class(
        node_cls="NodeA",
        bases=["SECoPNodeDevice"],
        modules=[
            ModuleAttribute(name="modA", type="Type1"),
            ModuleAttribute(name="modB", type="Type2"),
        ],
        properties=[
            PropertyAttribute(
                name="status",
                type="SignalR",
                type_param="str",
                path_annotation="PropPath('status')",
            ),
        ],
        description="NodeA with Type1 and Type2 modules",
    )

    # Generate and write first node
    code1 = gen_code1.generate_code()
    gen_code1.write_gen_node_class_file()

    # Verify first generation
    assert "class Type1(SECoPDevice):" in code1
    assert "class Type2(SECoPDevice):" in code1
    assert "class NodeA(SECoPNodeDevice):" in code1
    assert "modA: Type1" in code1
    assert "modB: Type2" in code1

    # ===== STEP 2: Load existing file and add second node (NodeB) =====

    gen_code2 = GenNodeCode(path=str(clean_generated_file), log=None)

    # Add necessary imports again
    gen_code2.add_import("secop_ophyd.SECoPDevices", "SECoPDevice")
    gen_code2.add_import("secop_ophyd.SECoPDevices", "SECoPNodeDevice")

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
        bases=["SECoPDevice"],
        parameters=[
            ParameterAttribute(
                name="temperature",
                type="SignalR",
                type_param="float",
                path_annotation="ParamPath('type1:temperature')",
            ),
            ParameterAttribute(
                name="setpoint",
                type="SignalRW",
                type_param="float",
                path_annotation="ParamPath('type1:setpoint')",
            ),
        ],
        properties=[
            PropertyAttribute(
                name="description",
                type="SignalR",
                type_param="str",
                path_annotation="PropPath('type1:description')",
            ),
            PropertyAttribute(
                name="interface_classes",
                type="SignalR",
                type_param="list",
                path_annotation="PropPath('type1:interface_classes')",
            ),
        ],
        cmd_plans=[method_type1],
        description="Type1 module - shared between nodes",
    )

    # Add module class Type3 (only in nodeB)
    gen_code2.add_mod_class(
        module_cls="Type3",
        bases=["SECoPDevice"],
        parameters=[
            ParameterAttribute(
                name="count",
                type="SignalRW",
                type_param="int",
                description="this is a description",
                path_annotation="ParamPath('type3:count')",
            ),
            ParameterAttribute(
                name="enabled",
                type="SignalR",
                type_param="bool",
                path_annotation="ParamPath('type3:enabled')",
            ),
        ],
        properties=[
            PropertyAttribute(
                name="group",
                type="SignalR",
                type_param="str",
                path_annotation="PropPath('type3:group')",
            ),
        ],
        cmd_plans=[method_type3],
        description="Type3 module - only in nodeB",
    )

    # Add nodeB
    gen_code2.add_node_class(
        node_cls="NodeB",
        bases=["SECoPNodeDevice"],
        modules=[
            ModuleAttribute(name="modA", type="Type1"),
            ModuleAttribute(name="modB", type="Type3"),
        ],
        properties=[
            PropertyAttribute(
                name="name",
                type="SignalR",
                type_param="str",
                path_annotation="PropPath('name')",
            ),
        ],
        description="NodeB with Type1 and Type3 modules",
    )

    # Generate and write second node (appends to the file)
    code2 = gen_code2.generate_code()
    gen_code2.write_gen_node_class_file()

    # ===== VERIFICATION =====
    # Verify that Type1 appears only once in the final code
    type1_count = code2.count("class Type1(SECoPDevice):")

    assert (
        type1_count == 1
    ), f"Type1 should appear exactly once, but appears {type1_count} times"

    # Verify all module classes are present
    assert "class Type1(SECoPDevice):" in code2
    assert "class Type2(SECoPDevice):" in code2
    assert "class Type3(SECoPDevice):" in code2

    # Verify both node classes are present
    assert "class NodeA(SECoPNodeDevice):" in code2
    assert "class NodeB(SECoPNodeDevice):" in code2

    # Verify all methods are present
    assert "def type1_cmd" in code2
    assert "def type2_cmd" in code2
    assert "def type3_cmd" in code2

    # Verify section comments are present
    assert "# Module Properties" in code2
    assert "# Module Parameters" in code2

    # Verify that descriptive  comments are preserved in generated code
    assert "# this is a description" in code2
    assert "# this has to be in the final output" in code2


async def test_gen_cryo_node(
    clean_generated_file, cryo_sim, cryo_node_no_re: SECoPNodeDevice
):
    """Test generating code for a real SECoP node."""

    cryo_node_no_re.class_from_instance(clean_generated_file)

    from tests.testgen.genNodeClass import Cryo_7_frappy_demo  # type: ignore

    async with init_devices():
        cryo_gen_code = Cryo_7_frappy_demo(sec_node_uri="localhost:10769")

    cryo_val = await cryo_gen_code.read()

    # target and value shoule be present in readback, since they are read signals with
    # HINTED Format --> this is tested to verify that the correct annotations are
    # generated and interpreted in the generated code
    val_name = cryo_gen_code.cryo.value.name
    target_name = cryo_gen_code.cryo.target.name
    read_val = cryo_val[val_name].get("value")
    read_target = cryo_val[target_name].get("value")

    print(cryo_val)

    assert read_val is not None
    assert read_val > 5

    assert read_target is not None
    assert read_target == 10


async def test_gen_cryo_status_not_in_cfg(
    clean_generated_file, cryo_sim, cryo_node_no_re: SECoPNodeDevice
):
    """Test that Status signal is not marked as configuration signal but is still
    instantiated."""

    cryo_node_no_re.class_from_instance(clean_generated_file)

    cryo_cfg = await cryo_node_no_re.read_configuration()
    cryo_reading = await cryo_node_no_re.read()

    print(cryo_reading)

    assert hasattr(cryo_node_no_re.cryo, "status")
    assert isinstance(cryo_node_no_re.cryo.status, SignalR)

    stat_name = cryo_node_no_re.cryo.status.name

    assert (
        cryo_cfg.get(stat_name) is None
    ), "Status signal should not be in configuration"
    assert cryo_reading.get(stat_name) is None, "Status signal should be readable"

    # check if status signal is working
    status_reding = await cryo_node_no_re.cryo.status.read()

    assert status_reding.get(stat_name) is not None, "Status signal should be readable"

    # Import generated class
    from tests.testgen.genNodeClass import Cryo_7_frappy_demo  # type: ignore

    async with init_devices():
        cryo_gen_code = Cryo_7_frappy_demo(sec_node_uri="localhost:10769")

    # Status signal should still be present and functional in the generated code, even
    # though it's not in the configuration
    assert hasattr(cryo_gen_code.cryo, "status")
    assert isinstance(cryo_gen_code.cryo.status, SignalR)

    cryo_cfg = await cryo_gen_code.read_configuration()
    cryo_reading = await cryo_gen_code.read()

    print(cryo_reading)

    stat_name = cryo_gen_code.cryo.status.name

    assert (
        cryo_cfg.get(stat_name) is None
    ), "Status signal should not be in configuration"
    assert cryo_reading.get(stat_name) is None, "Status signal should be readable"

    # check if status signal is working
    status_reding = await cryo_gen_code.cryo.status.read()

    assert status_reding.get(stat_name) is not None, "Status signal should be readable"


async def test_gen_real_node(
    clean_generated_file,
    nested_struct_sim,
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
        "class TestEnum_GasType_Enum(SupersetEnum):" in generated_code
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


async def test_subsequent_real_nodes_with_enum(
    clean_generated_file,
    cryo_sim,
    cryo_node_no_re: SECoPNodeDevice,
    nested_struct_sim,
    nested_node_no_re: SECoPNodeDevice,
):

    nested_node_no_re.class_from_instance(clean_generated_file)

    # Read the generated file and verify its contents
    gen_file = clean_generated_file / "genNodeClass.py"
    assert gen_file.exists(), "Generated file should exist"

    generated_code = gen_file.read_text()

    # ===== Assertions for generated enum classes =====
    cls = [
        "class TestEnum_GasType_Enum(SupersetEnum):",
        "class TestModStr(SECoPReadableDevice):",
        "class OphydTestPrimitiveArrays(SECoPReadableDevice):",
        "class TestEnum(SECoPReadableDevice):",
        "class TestNdArrays(SECoPReadableDevice):",
        "class TestStructOfArrays(SECoPReadableDevice):",
        "class Ophyd_secop_frappy_demo(SECoPNodeDevice):",
    ]
    for classs_str in cls:
        assert classs_str in generated_code

    cryo_node_no_re.class_from_instance(clean_generated_file)

    # Read the generated file and verify its contents
    gen_file = clean_generated_file / "genNodeClass.py"
    assert gen_file.exists(), "Generated file should exist"

    generated_code = gen_file.read_text()

    # ===== Assertions for generated enum classes =====

    cls = [
        "class TestEnum_GasType_Enum(SupersetEnum):",
        "class TestModStr(SECoPReadableDevice):",
        "class OphydTestPrimitiveArrays(SECoPReadableDevice):",
        "class TestEnum(SECoPReadableDevice):",
        "class TestNdArrays(SECoPReadableDevice):",
        "class TestStructOfArrays(SECoPReadableDevice):",
        "class Ophyd_secop_frappy_demo(SECoPNodeDevice):",
        "class Cryo_7_frappy_demo(SECoPNodeDevice):",
        "class Cryostat(SECoPMoveableDevice):",
        "class Cryostat_Mode_Enum(StrictEnum):",
    ]
    for classs_str in cls:
        assert classs_str in generated_code


def test_gen_shall_mass_spec_node(
    clean_generated_file, mass_spectrometer_description: str
):
    """Test generating code for the SHALL mass spectrometer node using a
    real description."""

    gen_code = GenNodeCode(path=str(clean_generated_file))

    gen_code.from_json_describe(mass_spectrometer_description)

    gen_code.write_gen_node_class_file()

    gen_file = clean_generated_file / "genNodeClass.py"
    assert gen_file.exists(), "Generated file should exist"

    generated_code = gen_file.read_text()

    # Trailing newlines in source descriptions should not produce broken split comments
    assert "\n# ; Unit: (V)" not in generated_code
    assert "\n#  ; Unit: (%)" not in generated_code

    # Intentionally multiline descriptions should be rendered as multiline comments
    assert (
        'mid_descriptor: A[SignalRW[ndarray], ParamPath("mass_spec:mid_descriptor")]'
        in generated_code
    )
    assert "#           Example:" in generated_code
    assert "#             {" in generated_code
    assert "#               mass:    [12,15,28,75]," in generated_code
    assert "#               device:  [FARADAY,SEM,SEM,SEM]" in generated_code

    # Long descriptions should be rendered fully below the declaration and wrapped
    assert (
        'resolution: A[SignalR[float], ParamPath("mass_spec:resolution")]\n'
        in generated_code
    )
    assert (
        "#  The high mass peak width/valley adjustment used during set up and"
        in generated_code
    )
    assert (
        "# low masses and should be adjusted in conjunction with the Delta-M."
        in generated_code
    )

    # Reparse generated code and verify multiline comments survive round-trip generation
    roundtrip_gen = GenNodeCode(path=str(clean_generated_file))
    roundtrip_code = roundtrip_gen.generate_code()

    assert (
        'mid_descriptor: A[SignalRW[ndarray], ParamPath("mass_spec:mid_descriptor")]'
        in roundtrip_code
    )
    assert "Example:" in roundtrip_code
    assert "\n# ; Unit: (V)" not in roundtrip_code
    assert (
        'resolution: A[SignalR[float], ParamPath("mass_spec:resolution")]\n'
        in roundtrip_code
    )


def test_gen_shall_mass_spec_node_no_impl(
    clean_generated_file, mass_spectrometer_description_no_impl: str
):
    """Test generating code for the SHALL mass spectrometer node using a
    real description."""

    gen_code = GenNodeCode(path=str(clean_generated_file))

    gen_code.from_json_describe(mass_spectrometer_description_no_impl)

    gen_code.write_gen_node_class_file()
