"""Simple test to verify GenNodeCode refactoring works."""

import importlib
import inspect
import sys
from pathlib import Path

from frappy.datatypes import StructOf
from ophyd_async.core import SignalR, SignalW, init_devices

from secop_ophyd.GenNodeCode import (
    CommandAttribute,
    GenNodeCode,
    Method,
    ModuleAttribute,
    ModuleClass,
    NodeClass,
    ParameterAttribute,
    PropertyAttribute,
)
from secop_ophyd.SECoPDevices import ParameterType, PropertyType, SECoPNodeDevice

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


def test_generated_command_methods_are_concrete(tmp_path: Path):
    """Generated command wrappers must be concrete so devices are instantiable."""
    gen_code = GenNodeCode(path=str(tmp_path), log=None)

    def command_plan_no_arg(self, wait_for_idle: bool = False):
        pass

    gen_code.add_mod_class(
        module_cls="CommandTestModule",
        bases=["SECoPReadableDevice"],
        parameters=[],
        properties=[],
        cmd_plans=[
            Method(
                cmd_name="factory_reset",
                description="Reset module",
                cmd_sign=inspect.signature(command_plan_no_arg),
            )
        ],
        description="test module",
    )

    generated_code = gen_code.generate_code()

    assert "def factory_reset" in generated_code
    assert "@abstractmethod" not in generated_code
    assert "raise RuntimeError(" in generated_code


def test_generated_command_annotations(tmp_path: Path):
    """Commands should be rendered as Command/TriggerableCommand annotations
    (no bound bluesky-plan-method stub is generated anymore)."""
    gen_code = GenNodeCode(path=str(tmp_path), log=None)

    gen_code.add_mod_class(
        module_cls="CommandAnnotationTestModule",
        bases=["SECoPReadableDevice"],
        parameters=[],
        properties=[],
        cmd_plans=[],
        description="test module",
        commands=[
            CommandAttribute(
                name="test_cmd",
                command_type="Command",
                arg_type="dict[str, Any]",
                return_type="int",
            ),
            CommandAttribute(name="go", command_type="TriggerableCommand"),
        ],
    )

    generated_code = gen_code.generate_code()

    assert "test_cmd: Command[[dict[str, Any]], int]" in generated_code
    assert "go: TriggerableCommand" in generated_code
    assert "@abstractmethod" not in generated_code


def test_build_command_signature_unravels_struct_argument():
    """A StructOf command argument should unravel into one KEYWORD_ONLY
    parameter per member, used for the runtime backend signature. Required
    members (not in `.optional`) have no default; optional members default
    to None. A trailing 'wait_for_idle' keyword-only param is always added."""
    from frappy.datatypes import BoolType, CommandType, IntRange

    from secop_ophyd.util import build_command_signature

    cmd_datatype = CommandType(
        argument=StructOf(a=IntRange(), b=BoolType(), optional=["b"]), result=None
    )

    sig = build_command_signature(cmd_datatype)

    assert (
        str(sig) == "(*, a: int, b: bool = None, wait_for_idle: bool = False) -> None"
    )


def test_build_command_signature_enum_argument():
    """A bare Enum command argument should be typed as a dynamically built
    StrictEnum subclass carrying the real SECoP member names (frappy's own
    tolerant EnumType.validate() still accepts ints or member names at call
    time; this annotation only affects introspection/typing, not binding)."""
    from frappy.datatypes import CommandType, EnumType
    from ophyd_async.core import StrictEnum

    from secop_ophyd.util import build_command_signature

    cmd_datatype = CommandType(argument=EnumType(LOW=0, HIGH=1), result=None)
    sig = build_command_signature(cmd_datatype)

    annotation = sig.parameters["arg"].annotation
    assert issubclass(annotation, StrictEnum)
    assert annotation is not StrictEnum
    assert {m.name for m in annotation} == {"LOW", "HIGH"}


def test_build_command_signature_enum_result():
    """A bare Enum command result should be typed as a dynamically built
    StrictEnum subclass carrying the real SECoP member names."""
    from frappy.datatypes import CommandType, EnumType
    from ophyd_async.core import StrictEnum

    from secop_ophyd.util import build_command_signature

    cmd_datatype = CommandType(argument=None, result=EnumType(OFF=0, ON=1))
    sig = build_command_signature(cmd_datatype)

    annotation = sig.return_annotation
    assert issubclass(annotation, StrictEnum)
    assert {m.name for m in annotation} == {"OFF", "ON"}


def test_build_command_signature_enum_struct_member():
    """An Enum member nested in a StructOf command argument should also be
    typed as a dynamically built StrictEnum subclass (mirrors the original
    bug report: a 'preset' member inside a struct argument)."""
    from frappy.datatypes import CommandType, EnumType, IntRange
    from ophyd_async.core import StrictEnum

    from secop_ophyd.util import build_command_signature

    cmd_datatype = CommandType(
        argument=StructOf(preset=EnumType(preset_01=1, preset_02=2), other=IntRange()),
        result=None,
    )
    sig = build_command_signature(cmd_datatype)

    preset_annotation = sig.parameters["preset"].annotation
    assert issubclass(preset_annotation, StrictEnum)
    assert {m.name for m in preset_annotation} == {"PRESET_01", "PRESET_02"}
    assert sig.parameters["other"].annotation is int


def test_build_command_signature_reuses_annotated_enum_argument():
    """When a Command class annotation already carries a concrete generated
    enum class (e.g. Cryostat_SetMode_Arg_Enum), build_command_signature()
    should reuse that exact class instead of building a fresh, differently-
    named one -- this is what makes the concrete generated class survive
    connect()."""
    import inspect

    from frappy.datatypes import CommandType, EnumType
    from ophyd_async.core import StrictEnum

    from secop_ophyd.util import build_command_signature

    class SomeGeneratedArgEnum(StrictEnum):
        LOW = "low"
        HIGH = "high"

    annotated_signature = inspect.Signature(
        [
            inspect.Parameter(
                "arg0",
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
                annotation=SomeGeneratedArgEnum,
            )
        ]
    )

    cmd_datatype = CommandType(argument=EnumType(LOW=0, HIGH=1), result=None)
    sig = build_command_signature(cmd_datatype, annotated_signature=annotated_signature)

    assert sig.parameters["arg"].annotation is SomeGeneratedArgEnum


def test_build_command_signature_reuses_annotated_enum_result():
    """Same as above, for the return annotation."""
    import inspect

    from frappy.datatypes import CommandType, EnumType
    from ophyd_async.core import StrictEnum

    from secop_ophyd.util import build_command_signature

    class SomeGeneratedResultEnum(StrictEnum):
        OK = "ok"
        FAIL = "fail"

    annotated_signature = inspect.Signature(
        [], return_annotation=SomeGeneratedResultEnum
    )

    cmd_datatype = CommandType(argument=None, result=EnumType(OK=0, FAIL=1))
    sig = build_command_signature(cmd_datatype, annotated_signature=annotated_signature)

    assert sig.return_annotation is SomeGeneratedResultEnum


def test_build_command_signature_ignores_annotated_signature_for_struct():
    """A StructOf argument's annotated_signature has a different shape (a
    single flat arg0, not a per-member decomposition) and struct members
    never get named enum classes at codegen time -- struct members must
    always get a fresh _dynamic_enum_class, never attempt to reuse
    annotated_signature."""
    import inspect

    from frappy.datatypes import CommandType, EnumType
    from ophyd_async.core import StrictEnum

    from secop_ophyd.util import build_command_signature

    class SomeGeneratedArgEnum(StrictEnum):
        LOW = "low"
        HIGH = "high"

    annotated_signature = inspect.Signature(
        [
            inspect.Parameter(
                "arg0",
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
                annotation=dict,
            )
        ]
    )

    cmd_datatype = CommandType(
        argument=StructOf(preset=EnumType(LOW=0, HIGH=1)), result=None
    )
    sig = build_command_signature(cmd_datatype, annotated_signature=annotated_signature)

    preset_annotation = sig.parameters["preset"].annotation
    assert issubclass(preset_annotation, StrictEnum)
    assert preset_annotation is not SomeGeneratedArgEnum


def test_build_command_signature_no_reuse_when_no_annotation():
    """With no annotated_signature (pure introspection instantiation, no
    generated class involved), behavior must be identical to before this
    fix: a fresh, dynamically built enum class."""
    from frappy.datatypes import CommandType, EnumType
    from ophyd_async.core import StrictEnum

    from secop_ophyd.util import build_command_signature

    cmd_datatype = CommandType(argument=EnumType(LOW=0, HIGH=1), result=None)
    sig = build_command_signature(cmd_datatype)

    annotation = sig.parameters["arg"].annotation
    assert issubclass(annotation, StrictEnum)
    assert annotation is not StrictEnum


def test_command_backend_preserves_annotated_enum_end_to_end():
    """SECoPCommandBackend, constructed with a signature carrying a concrete
    generated enum class (as ophyd_async's DeviceFiller does from a real
    `Command[[SomeEnum], ...]` class annotation), should still have that
    exact class as its signature's annotation after
    init_command_from_introspection() runs -- not a freshly, anonymously
    built one."""
    from frappy.datatypes import CommandType, EnumType
    from ophyd_async.core import StrictEnum

    from secop_ophyd.SECoPSignal import SECoPCommandBackend
    from secop_ophyd.util import Path as SECoPPath

    class SomeGeneratedArgEnum(StrictEnum):
        LOW = "low"
        HIGH = "high"

    annotated_signature = inspect.Signature(
        [
            inspect.Parameter(
                "arg0",
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
                annotation=SomeGeneratedArgEnum,
            )
        ]
    )

    backend = SECoPCommandBackend(signature=annotated_signature)

    cmd_datatype = CommandType(argument=EnumType(LOW=0, HIGH=1), result=None)
    backend.init_command_from_introspection(
        cmd_datatype,
        SECoPPath(parameter_name="set_mode", module_name="cryo"),
        secclient=object(),  # type: ignore[arg-type]
    )

    assert backend.signature.parameters["arg"].annotation is SomeGeneratedArgEnum


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
                path_annotation=str(ParameterType()),
            ),
            ParameterAttribute(
                name="pressure",
                type="SignalR",
                type_param="float",
                path_annotation=str(ParameterType()),
            ),
            ParameterAttribute(
                name="count",
                type="SignalRW",
                type_param="int",
                path_annotation=str(ParameterType()),
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
                path_annotation=str(PropertyType()),
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
    assert "class TestModule(SECoPDevice):" in code
    assert "temperature: A[SignalR[float], ParamT()]" in code
    assert "count: A[SignalRW[int], ParamT()]" in code
    assert "class TestNode(SECoPNodeDevice):" in code
    assert "module1: TestModule" in code
    assert "status: A[SignalR[str], PropT()]" in code
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
    """Test generating code for two different nodes into the same output
    directory.

    Tests that:
    - First: Generate NodeA with modules Type1 and Type2, written to NodeA.py
    - Second: Generate NodeB with Type1 (same module class name) and Type3,
      written to NodeB.py
    - Each node gets its own file, with no cross-file merging or dedup: both
      files independently contain their own "Type1" class definition
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
                path_annotation=str(ParameterType()),
            ),
            ParameterAttribute(
                name="setpoint",
                type="SignalRW",
                type_param="float",
                path_annotation=str(ParameterType()),
            ),
        ],
        properties=[
            PropertyAttribute(
                name="description",
                type="SignalR",
                type_param="str",
                path_annotation=str(PropertyType()),
            ),
            PropertyAttribute(
                name="interface_classes",
                type="SignalR",
                type_param="int",
                path_annotation=str(PropertyType()),
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
                path_annotation=str(ParameterType()),
            ),
            ParameterAttribute(
                name="mode",
                type="SignalRW",
                type_param="str",
                path_annotation=str(ParameterType()),
            ),
        ],
        properties=[
            PropertyAttribute(
                name="implementation",
                type="SignalR",
                type_param="str",
                path_annotation=str(PropertyType()),
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
                path_annotation=str(PropertyType()),
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

    # ===== STEP 2: Generate second node (NodeB) into its own file =====

    gen_code2 = GenNodeCode(path=str(clean_generated_file), log=None)

    # Create method for Type3
    def type3_command(self, count: int) -> int:
        """Type3 command"""
        return count + 1

    method_type3 = Method(
        cmd_name="type3_cmd",
        description="Type3 command",
        cmd_sign=signature(type3_command),
    )

    # Add a module class also named "Type1" -- a different GenNodeCode
    # instance/node, so this must NOT be affected by gen_code1's Type1 at all
    gen_code2.add_mod_class(
        module_cls="Type1",
        bases=["SECoPDevice"],
        parameters=[
            ParameterAttribute(
                name="temperature",
                type="SignalR",
                type_param="float",
                path_annotation=str(ParameterType()),
            ),
            ParameterAttribute(
                name="setpoint",
                type="SignalRW",
                type_param="float",
                path_annotation=str(ParameterType()),
            ),
        ],
        properties=[
            PropertyAttribute(
                name="description",
                type="SignalR",
                type_param="str",
                path_annotation=str(PropertyType()),
            ),
            PropertyAttribute(
                name="interface_classes",
                type="SignalR",
                type_param="list",
                path_annotation=str(PropertyType()),
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
                path_annotation=str(ParameterType()),
            ),
            ParameterAttribute(
                name="enabled",
                type="SignalR",
                type_param="bool",
                path_annotation=str(ParameterType()),
            ),
        ],
        properties=[
            PropertyAttribute(
                name="group",
                type="SignalR",
                type_param="str",
                path_annotation=str(PropertyType()),
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
                path_annotation=str(PropertyType()),
            ),
        ],
        description="NodeB with Type1 and Type3 modules",
    )

    # Generate and write second node to its own file
    code2 = gen_code2.generate_code()
    gen_code2.write_gen_node_class_file()

    # ===== VERIFICATION =====
    # Each node was written to its own file, named after the node class
    node_a_file = clean_generated_file / "NodeA.py"
    node_b_file = clean_generated_file / "NodeB.py"
    assert node_a_file.exists()
    assert node_b_file.exists()

    # NodeA's file only has NodeA's classes -- unaffected by NodeB's generation
    assert "class Type1(SECoPDevice):" in code1
    assert "class Type2(SECoPDevice):" in code1
    assert "class NodeA(SECoPNodeDevice):" in code1
    assert "class Type3(SECoPDevice):" not in code1
    assert "class NodeB(SECoPNodeDevice):" not in code1
    assert "def type1_cmd" in code1
    assert "def type2_cmd" in code1

    # NodeB's file only has NodeB's classes, including its own independent
    # "Type1" class definition -- no cross-file merge/dedup
    assert "class Type1(SECoPDevice):" in code2
    assert "class Type3(SECoPDevice):" in code2
    assert "class NodeB(SECoPNodeDevice):" in code2
    assert "class Type2(SECoPDevice):" not in code2
    assert "class NodeA(SECoPNodeDevice):" not in code2
    assert "def type1_cmd" in code2
    assert "def type3_cmd" in code2

    # Verify section comments are present
    assert "# Module Properties" in code2
    assert "# Module Parameters" in code2

    # Verify that descriptive comments are preserved in generated code
    assert "# this is a description" in code2
    assert "# this has to be in the final output" in code1


def test_package_init_generated(tmp_path: Path):
    """write_gen_node_class_file() should (re)generate an __init__.py in the
    output directory that re-exports every generated node class currently on
    disk, making the directory itself importable as a package."""

    out_dir = tmp_path / "gen_pkg"

    gen_code_a = GenNodeCode(path=str(out_dir), log=None)
    gen_code_a.add_node_class(
        node_cls="InitTestNodeA",
        bases=["SECoPNodeDevice"],
        properties=[],
        modules=[],
    )
    gen_code_a.write_gen_node_class_file()

    init_file = out_dir / "__init__.py"
    assert init_file.exists()
    assert init_file.read_text() == "from .InitTestNodeA import InitTestNodeA\n"

    # Generating a second, independent node into the same directory should
    # update __init__.py to re-export both, without losing NodeA's entry.
    gen_code_b = GenNodeCode(path=str(out_dir), log=None)
    gen_code_b.add_node_class(
        node_cls="InitTestNodeB",
        bases=["SECoPNodeDevice"],
        properties=[],
        modules=[],
    )
    gen_code_b.write_gen_node_class_file()

    init_contents = init_file.read_text()
    assert "from .InitTestNodeA import InitTestNodeA\n" in init_contents
    assert "from .InitTestNodeB import InitTestNodeB\n" in init_contents

    # The directory should actually import as a package re-exporting both.
    sys.path.insert(0, str(tmp_path))
    try:
        package = importlib.import_module(out_dir.name)
        assert hasattr(package, "InitTestNodeA")
        assert hasattr(package, "InitTestNodeB")
    finally:
        sys.path.remove(str(tmp_path))
        sys.modules.pop(out_dir.name, None)
        sys.modules.pop(f"{out_dir.name}.InitTestNodeA", None)
        sys.modules.pop(f"{out_dir.name}.InitTestNodeB", None)


async def test_gen_cryo_node(
    clean_generated_file, cryo_sim, cryo_node_no_re: SECoPNodeDevice
):
    """Test generating code for a real SECoP node."""

    cryo_node_no_re.class_from_instance(clean_generated_file)

    from tests.testgen.Cryo_7_frappy_demo import Cryo_7_frappy_demo  # type: ignore

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


async def test_generated_enum_parameter_datatype_is_preserved(
    clean_generated_file, cryo_sim, cryo_node_no_re: SECoPNodeDevice
):
    """A Parameter declared with a concrete generated enum class annotation
    (e.g. `mode: A[SignalRW[Cryostat_Mode_Enum], ParamT()]`) should keep that
    exact class as its runtime datatype after connect() -- not be silently
    replaced by the generic, member-less StrictEnum base class."""

    cryo_node_no_re.class_from_instance(clean_generated_file)

    from tests.testgen.Cryo_7_frappy_demo import (  # type: ignore
        Cryo_7_frappy_demo,
        Cryostat_Mode_Enum,
    )

    async with init_devices():
        cryo_gen_code = Cryo_7_frappy_demo(sec_node_uri="localhost:10769")

    assert cryo_gen_code.cryo.mode.datatype is Cryostat_Mode_Enum


async def test_gen_cryo_status_in_cfg(
    clean_generated_file, cryo_sim, cryo_node_no_re: SECoPNodeDevice
):
    """status is StatusType == TupleOf(EnumType, StringType), always split
    into status_0 (code)/status_1 (message). Unlike the old monolithic
    structured-array 'status' signal (which was excluded from both read()
    and read_configuration() since tiled/databroker couldn't handle its
    dtype), status_0/status_1 are plain atomic signals with no such
    restriction, so they get the same default CONFIG_SIGNAL format as any
    other un-hinted parameter."""

    cryo_node_no_re.class_from_instance(clean_generated_file)

    cryo_cfg = await cryo_node_no_re.read_configuration()
    cryo_reading = await cryo_node_no_re.read()

    print(cryo_reading)

    assert hasattr(cryo_node_no_re.cryo, "status_0")
    assert isinstance(cryo_node_no_re.cryo.status_0, SignalR)

    stat_name = cryo_node_no_re.cryo.status_0.name

    assert (
        cryo_cfg.get(stat_name) is not None
    ), "Status signal should be in configuration"
    assert (
        cryo_reading.get(stat_name) is None
    ), "Status signal should not be a read signal"

    # Import generated class
    from tests.testgen.Cryo_7_frappy_demo import Cryo_7_frappy_demo  # type: ignore

    async with init_devices():
        cryo_gen_code = Cryo_7_frappy_demo(sec_node_uri="localhost:10769")

    assert hasattr(cryo_gen_code.cryo, "status_0")
    assert isinstance(cryo_gen_code.cryo.status_0, SignalR)

    cryo_cfg = await cryo_gen_code.read_configuration()
    cryo_reading = await cryo_gen_code.read()

    print(cryo_reading)

    stat_name = cryo_gen_code.cryo.status_0.name

    assert (
        cryo_cfg.get(stat_name) is not None
    ), "Status signal should be in configuration"
    assert (
        cryo_reading.get(stat_name) is None
    ), "Status signal should not be a read signal"


async def test_gen_real_node(
    clean_generated_file,
    nested_struct_sim,
    nested_node_no_re: SECoPNodeDevice,  # noqa: N803
):

    nested_node_no_re.class_from_instance(clean_generated_file)

    # Read the generated file and verify its contents
    gen_file = clean_generated_file / "Ophyd_secop_frappy_demo.py"
    assert gen_file.exists(), "Generated file should exist"

    generated_code = gen_file.read_text()

    # ===== Assertions for generated commands =====
    assert (
        "def test_cmd(" not in generated_code
    ), "no bare test_cmd method should shadow the Command attribute"
    assert (
        "@abstractmethod" not in generated_code
    ), "Command methods should be concrete so generated classes are instantiable"

    # test_cmd takes a struct argument and returns an int
    assert (
        "test_cmd: Command[[dict[str, Any]], int]" in generated_code
    ), "test_cmd annotation should be generated"

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


async def test_gen_real_node_composite_movable_sibling_connects(
    clean_generated_file,
    nested_struct_sim,
    nested_node_no_re: SECoPNodeDevice,
):
    """Regression test: a Drivable module with a composite 'value' (no bare
    'value' attribute, only split value_* signals) that is a sibling of
    other modules in a fully class-annotated *generated* node must connect
    without crashing.

    create_children_from_annotations() pre-creates every declared child
    device/signal -- including 'target' -- at construction time, before any
    connect() call runs, purely from the generated class's annotations. So
    by the time init_devices() does its first naming pass (also before
    connect()), a sibling module being named can trigger this Drivable's
    set_name() while 'target' already structurally exists but movable_logic
    isn't actually resolvable yet (nothing has been connected). Regression
    for a crash where SECoPMoveableDevice.set_name() only guarded on
    hasattr(self, 'target'), not on whether the (possibly composite) readback
    was ready too: AttributeError: 'NoneType' object has no attribute
    'set_name' from StandardMovable.set_name() -> movable_logic.readback.
    """
    nested_node_no_re.class_from_instance(clean_generated_file)

    from tests.testgen.Ophyd_secop_frappy_demo import (  # type: ignore
        Ophyd_secop_frappy_demo,
    )

    async with init_devices():
        gen_node = Ophyd_secop_frappy_demo(sec_node_uri="localhost:10771")

    struct_mod = gen_node.ophy_struct
    assert not hasattr(struct_mod, "value")
    assert isinstance(struct_mod.target, SignalW)

    reading = await struct_mod.value_x.read()
    assert isinstance(reading[struct_mod.value_x.name]["value"], float)


async def test_subsequent_real_nodes_with_enum(
    clean_generated_file,
    cryo_sim,
    cryo_node_no_re: SECoPNodeDevice,
    nested_struct_sim,
    nested_node_no_re: SECoPNodeDevice,
):
    """Generating two different real nodes into the same output directory
    must write two independent files -- each containing only its own node's
    classes, with no cross-node merging."""

    nested_node_no_re.class_from_instance(clean_generated_file)
    cryo_node_no_re.class_from_instance(clean_generated_file)

    # ===== nested node: its own file, only its own classes =====
    nested_file = clean_generated_file / "Ophyd_secop_frappy_demo.py"
    assert nested_file.exists(), "Generated file should exist"

    nested_code = nested_file.read_text()

    nested_cls = [
        "class TestEnum_GasType_Enum(SupersetEnum):",
        "class TestModStr(SECoPReadableDevice):",
        "class OphydTestPrimitiveArrays(SECoPReadableDevice):",
        "class TestEnum(SECoPReadableDevice):",
        "class TestNdArrays(SECoPReadableDevice):",
        "class TestStructOfArrays(SECoPReadableDevice):",
        "class Ophyd_secop_frappy_demo(SECoPNodeDevice):",
    ]
    for classs_str in nested_cls:
        assert classs_str in nested_code

    assert "class Cryo_7_frappy_demo(SECoPNodeDevice):" not in nested_code
    assert "class Cryostat(SECoPMoveableDevice[float]):" not in nested_code

    # ===== cryo node: its own file, only its own classes =====
    cryo_file = clean_generated_file / "Cryo_7_frappy_demo.py"
    assert cryo_file.exists(), "Generated file should exist"

    cryo_code = cryo_file.read_text()

    cryo_cls = [
        "class Cryo_7_frappy_demo(SECoPNodeDevice):",
        "class Cryostat(SECoPMoveableDevice[float]):",
        "class Cryostat_Mode_Enum(StrictEnum):",
    ]
    for classs_str in cryo_cls:
        assert classs_str in cryo_code

    assert "class Ophyd_secop_frappy_demo(SECoPNodeDevice):" not in cryo_code
    assert "class TestEnum_GasType_Enum(SupersetEnum):" not in cryo_code


def test_gen_shall_mass_spec_node(
    clean_generated_file, mass_spectrometer_description: str
):
    """Test generating code for the SHALL mass spectrometer node using a
    real description."""

    gen_code = GenNodeCode(path=str(clean_generated_file))

    gen_code.from_json_describe(mass_spectrometer_description)

    gen_code.write_gen_node_class_file()

    gen_file = clean_generated_file / "Hiden_ms.py"
    assert gen_file.exists(), "Generated file should exist"

    generated_code = gen_file.read_text()

    # Trailing newlines in source descriptions should not produce broken split comments
    assert "\n# ; Unit: (V)" not in generated_code
    assert "\n#  ; Unit: (%)" not in generated_code

    # mid_descriptor is a writable StructOf(device=Array, mass=Array) --
    # depth 1, so it's split into one read-only member Signal per struct
    # field, plus a write-only SignalW at the base name for setting the
    # whole struct (accepting a plain dict, see SECoPBackend.put/val2secop)
    assert (
        "mid_descriptor_device: A[SignalR[ndarray], ParamMemberT()]" in generated_code
    )
    assert "mid_descriptor_mass: A[SignalR[ndarray], ParamMemberT()]" in generated_code
    assert "mid_descriptor: A[SignalW[ndarray], ParamT()]" in generated_code

    # Intentionally multiline descriptions should be rendered as multiline comments
    assert "#           Example:" in generated_code
    assert "#             {" in generated_code
    assert "#               mass:    [12,15,28,75]," in generated_code
    assert "#               device:  [FARADAY,SEM,SEM,SEM]" in generated_code

    # Long descriptions should be rendered fully below the declaration and wrapped
    assert "resolution: A[SignalR[float], ParamT()]\n" in generated_code
    assert (
        "#  The high mass peak width/valley adjustment used during set up and"
        in generated_code
    )
    assert (
        "# low masses and should be adjusted in conjunction with the Delta-M."
        in generated_code
    )

    # Void "go" command should be annotated as TriggerableCommand; "stop" is
    # skipped since SECoPMoveableDevice already implements Stoppable.stop()
    # natively and a raw command device would shadow it.
    assert "go: TriggerableCommand" in generated_code
    assert "stop: TriggerableCommand" not in generated_code


def test_gen_shall_mass_spec_node_no_impl(
    clean_generated_file, mass_spectrometer_description_no_impl: str
):
    """Test generating code for the SHALL mass spectrometer node using a
    real description."""

    gen_code = GenNodeCode(path=str(clean_generated_file))

    gen_code.from_json_describe(mass_spectrometer_description_no_impl)

    gen_code.write_gen_node_class_file()


def test_gen_command_with_enum_argument_and_result(clean_generated_file):
    """A command with a bare Enum argument and a bare Enum result should each
    get their own concrete named StrictEnum class generated and substituted
    into the Command[[...], ...] annotation."""

    describe_data = {
        "equipment_id": "enum_cmd.test.demo",
        "description": "node for testing command enum codegen",
        "modules": {
            "enummod": {
                "description": "module for testing command enum codegen",
                "interface_classes": ["Readable"],
                "accessibles": {
                    "value": {
                        "datainfo": {"type": "double"},
                        "description": "the value",
                        "readonly": True,
                    },
                    "set_mode": {
                        "datainfo": {
                            "type": "command",
                            "argument": {
                                "type": "enum",
                                "members": {"ramp": 0, "pid": 1},
                            },
                            "result": {
                                "type": "enum",
                                "members": {"ok": 0, "fail": 1},
                            },
                        },
                        "description": "set the mode",
                    },
                },
            },
        },
    }

    gen_code = GenNodeCode(path=str(clean_generated_file))
    gen_code.from_json_describe(describe_data)

    generated_code = gen_code.generate_code()

    assert "class Enummod_SetMode_Arg_Enum(StrictEnum):" in generated_code
    assert 'RAMP = "ramp"' in generated_code
    assert 'PID = "pid"' in generated_code

    assert "class Enummod_SetMode_Result_Enum(StrictEnum):" in generated_code
    assert 'OK = "ok"' in generated_code
    assert 'FAIL = "fail"' in generated_code

    assert (
        "set_mode: Command[[Enummod_SetMode_Arg_Enum], Enummod_SetMode_Result_Enum]"
        in generated_code
    )
