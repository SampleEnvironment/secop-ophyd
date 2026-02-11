"""Test enum merging logic for StrictEnum vs SupersetEnum."""

from secop_ophyd.GenNodeCode import (
    EnumClass,
    EnumMember,
    GenNodeCode,
    ParameterAttribute,
)


def test_identical_enums_use_strict():
    """When module instances have identical enum values, use StrictEnum."""
    gen_code = GenNodeCode()

    # Create two module classes with identical enums
    enum1 = EnumClass(
        name="TestParamEnum",
        members=[
            EnumMember("H2", "H2", "Hydrogen"),
            EnumMember("N2", "N2", "Nitrogen"),
        ],
    )

    enum2 = EnumClass(
        name="TestParamEnum",
        members=[
            EnumMember("H2", "H2", "Hydrogen"),
            EnumMember("N2", "N2", "Nitrogen"),
        ],
    )

    # Add module classes with these enums
    gen_code.module_classes = []
    from secop_ophyd.GenNodeCode import ModuleClass

    mod1 = ModuleClass(name="Module1", bases=["Device"], enums=[enum1])
    mod2 = ModuleClass(name="Module2", bases=["Device"], enums=[enum2])

    gen_code.module_classes = [mod1, mod2]

    # Collect enums
    merged = gen_code._collect_all_enums()

    # Should have one enum using StrictEnum
    assert len(merged) == 1
    assert merged[0].base_enum_class == "StrictEnum"
    assert len(merged[0].members) == 2
    print("✓ Identical enums correctly use StrictEnum")


def test_different_enums_use_superset():
    """When module instances have different enum values, use SupersetEnum."""
    gen_code = GenNodeCode()

    # Create two module classes with different enum members
    enum1 = EnumClass(
        name="TestParamEnum",
        members=[
            EnumMember("H2", "H2", "Hydrogen"),
            EnumMember("N2", "N2", "Nitrogen"),
        ],
    )

    enum2 = EnumClass(
        name="TestParamEnum",
        members=[
            EnumMember("N2", "N2", "Nitrogen"),
            EnumMember("AR", "Ar", "Argon"),
        ],
    )

    # Add module classes with these enums
    from secop_ophyd.GenNodeCode import ModuleClass

    mod1 = ModuleClass(name="Module1", bases=["Device"], enums=[enum1])
    mod2 = ModuleClass(name="Module2", bases=["Device"], enums=[enum2])

    gen_code.module_classes = [mod1, mod2]

    # Collect enums
    merged = gen_code._collect_all_enums()

    # Should have one enum using SupersetEnum with all members
    assert len(merged) == 1
    assert merged[0].base_enum_class == "SupersetEnum"
    assert len(merged[0].members) == 3  # H2, N2, AR

    member_names = {m.name for m in merged[0].members}
    assert member_names == {"H2", "N2", "AR"}
    print("✓ Different enums correctly merged into SupersetEnum")


def test_single_enum_uses_strict():
    """When only one module has an enum, use StrictEnum."""
    gen_code = GenNodeCode()

    enum1 = EnumClass(
        name="TestParamEnum",
        members=[
            EnumMember("H2", "H2", "Hydrogen"),
            EnumMember("N2", "N2", "Nitrogen"),
        ],
    )

    from secop_ophyd.GenNodeCode import ModuleClass

    mod1 = ModuleClass(name="Module1", bases=["Device"], enums=[enum1])

    gen_code.module_classes = [mod1]

    # Collect enums
    merged = gen_code._collect_all_enums()

    # Should have one enum using StrictEnum
    assert len(merged) == 1
    assert merged[0].base_enum_class == "StrictEnum"
    assert len(merged[0].members) == 2
    print("✓ Single enum correctly uses StrictEnum")


def test_same_class_different_instances():
    """When same module class has different instances with different enums, use
    SupersetEnum."""
    gen_code = GenNodeCode()

    # Simulate adding the same module class twice (different instances)
    # First instance with AR, N2, H2
    enum1 = EnumClass(
        name="Test_EnumGas_typeEnum",
        members=[
            EnumMember("AR", "AR", "Argon"),
            EnumMember("N2", "N2", "Nitrogen"),
            EnumMember("H2", "H2", "Hydrogen"),
        ],
    )

    # Second instance with AR, He, CO2
    enum2 = EnumClass(
        name="Test_EnumGas_typeEnum",
        members=[
            EnumMember("AR", "AR", "Argon"),
            EnumMember("HE", "He", "Helium"),
            EnumMember("CO2", "CO2", "Carbon Dioxide"),
        ],
    )

    # First add_mod_class call
    gen_code.add_mod_class(
        module_cls="Test_Enum",
        bases=["Device"],
        parameters=[
            ParameterAttribute(
                name="gas_type",
                type="SignalRW",
                type_param="Test_EnumGas_typeEnum",
                path_annotation="balh:gas_type",
            )
        ],
        properties=[],
        cmd_plans=[],
        enum_classes=[enum1],
    )

    # Second add_mod_class call (same class name, different enum)
    gen_code.add_mod_class(
        module_cls="Test_Enum",
        bases=["Device"],
        parameters=[
            ParameterAttribute(
                name="gas_type",
                type="SignalRW",
                type_param="Test_EnumGas_typeEnum",
                path_annotation="balh:gas_type",
            )
        ],
        properties=[],
        cmd_plans=[],
        enum_classes=[enum2],
    )

    # Should only have one module class, but with both enums
    assert len(gen_code.module_classes) == 1
    assert len(gen_code.module_classes[0].enums) == 2

    # Collect and merge enums
    merged = gen_code._collect_all_enums()

    # Should have one enum using SupersetEnum with all 5 unique members
    assert len(merged) == 1
    assert merged[0].base_enum_class == "SupersetEnum"
    assert len(merged[0].members) == 5  # AR, N2, H2, He, CO2

    member_names = {m.name for m in merged[0].members}
    assert member_names == {"AR", "N2", "H2", "HE", "CO2"}
