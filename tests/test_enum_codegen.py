"""Test full code generation with enums."""

from secop_ophyd.GenNodeCode import (
    EnumClass,
    EnumMember,
    GenNodeCode,
    ModuleClass,
    ParameterAttribute,
)


def test_code_generation_with_enums(clean_generated_file):
    """Test that generated code includes correct enum base classes."""

    gen_code = GenNodeCode(path=clean_generated_file)

    # Create two module classes with different enums for the same parameter
    enum1 = EnumClass(
        name="MassflowControllerGastypeEnum",
        members=[
            EnumMember("H2", "H2", "Hydrogen"),
            EnumMember("N2", "N2", "Nitrogen"),
        ],
        description="Gastype enum for MassflowController",
    )

    enum2 = EnumClass(
        name="MassflowControllerGastypeEnum",
        members=[
            EnumMember("N2", "N2", "Nitrogen"),
            EnumMember("AR", "Ar", "Argon"),
        ],
        description="Gastype enum for MassflowController",
    )

    mod1 = ModuleClass(
        name="MassflowController1",
        bases=["Device"],
        parameters=[
            ParameterAttribute("gastype", "SignalRW", "MassflowControllerGastypeEnum"),
        ],
        properties=[],
        enums=[enum1],
    )

    mod2 = ModuleClass(
        name="MassflowController2",
        bases=["Device"],
        parameters=[
            ParameterAttribute("gastype", "SignalRW", "MassflowControllerGastypeEnum"),
        ],
        properties=[],
        enums=[enum2],
    )

    gen_code.module_classes = [mod1, mod2]

    # Generate code
    code = gen_code.generate_code()

    print("Generated code:")
    print("=" * 60)
    print(code)
    print("=" * 60)

    # Verify the enum uses SupersetEnum
    assert "class MassflowControllerGastypeEnum(SupersetEnum):" in code
    assert "SupersetEnum" in code

    # Verify all three members are present
    assert 'H2 = "H2"' in code
    assert 'N2 = "N2"' in code
    assert 'AR = "Ar"' in code

    print("\n✓ Code generation with SupersetEnum successful!")


def test_code_generation_strict_enum(clean_generated_file):
    """Test that identical enums generate StrictEnum."""

    gen_code = GenNodeCode(path=clean_generated_file)

    # Create two module classes with identical enums
    enum1 = EnumClass(
        name="StatusEnum",
        members=[
            EnumMember("IDLE", "idle", "Idle state"),
            EnumMember("BUSY", "busy", "Busy state"),
        ],
        description="Status enum",
    )

    enum2 = EnumClass(
        name="StatusEnum",
        members=[
            EnumMember("IDLE", "idle", "Idle state"),
            EnumMember("BUSY", "busy", "Busy state"),
        ],
        description="Status enum",
    )

    mod1 = ModuleClass(
        name="Device1",
        bases=["Device"],
        parameters=[
            ParameterAttribute("status", "SignalR", "StatusEnum"),
        ],
        properties=[],
        enums=[enum1],
    )

    mod2 = ModuleClass(
        name="Device2",
        bases=["Device"],
        parameters=[
            ParameterAttribute("status", "SignalR", "StatusEnum"),
        ],
        properties=[],
        enums=[enum2],
    )

    gen_code.module_classes = [mod1, mod2]

    # Generate code
    code = gen_code.generate_code()

    print("\nGenerated code with StrictEnum:")
    print("=" * 60)
    print(code)
    print("=" * 60)

    # Verify the enum uses StrictEnum
    assert "class StatusEnum(StrictEnum):" in code

    print("\n✓ Code generation with StrictEnum successful!")
