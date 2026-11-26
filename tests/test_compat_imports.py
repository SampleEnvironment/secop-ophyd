"""The module paths of earlier versions keep working: device classes generated
by them import from ``secop_ophyd.SECoPDevices``."""

import importlib

import pytest

# old module -> names it must still provide, each with the module it moved to
OLD_NAMES = {
    "secop_ophyd.SECoPDevices": {
        "secop_ophyd.devices": [
            "SECoPDevice",
            "SECoPNodeDevice",
            "SECoPCommunicatorDevice",
            "SECoPReadableDevice",
            "SECoPWritableDevice",
            "SECoPMoveableDevice",
            "SECoPCompositeMoveableDevice",
            "SECoPAcquisitionDevice",
            "SECoPAcquisitionControllerDevice",
            "SECoPAcquisitionChannelDevice",
            "SECoPTriggerableDevice",
            "AcquisitionGoal",
            "ParameterType",
            "ParameterMemberType",
            "PropertyType",
            "AcquisitionChannelType",
            "class_from_interface",
        ],
        "secop_ophyd.devices.readable": ["IDLE", "BUSY", "ERROR", "SECoPReadableMock"],
        "secop_ophyd.devices.writable": ["T", "SECoPWritableMock"],
        "secop_ophyd.devices.drivable": ["SECoPMovableMock"],
        "secop_ophyd.devices.acquisition.control": ["PrepareT_"],
        # imported by the old module, and so importable from it
        "secop_ophyd.signals": ["SECoPBackend", "AttributeType"],
        "secop_ophyd.datatypes": ["SECoPdtype", "IncompatibleSECoPDatatype"],
    },
    "secop_ophyd.SECoPSignal": {
        "secop_ophyd.signals": ["SECoPBackend", "SECoPCommandBackend", "AttributeType"],
        "secop_ophyd.datatypes": [
            "SECoPdtype",
            "module_property_signals",
            "property_dtype_and_value",
        ],
    },
    "secop_ophyd.GenNodeCode": {
        "secop_ophyd.codegen": ["GenNodeCode"],
        "secop_ophyd.codegen.model": ["EnumClass", "ModuleClass", "NodeClass"],
        "secop_ophyd.datatypes": ["get_type_param", "get_type_prop"],
        "secop_ophyd.devices": ["ParameterType", "class_from_interface"],
    },
    "secop_ophyd.util": {
        "secop_ophyd.datatypes": [
            "SECoPdtype",
            "SECoPReading",
            "CompositeKind",
            "build_command_signature",
        ],
    },
}


@pytest.mark.parametrize("old", OLD_NAMES)
def test_old_paths_give_the_same_objects(old: str):
    old_module = importlib.import_module(old)
    for new, names in OLD_NAMES[old].items():
        new_module = importlib.import_module(new)
        for name in names:
            assert getattr(old_module, name) is getattr(new_module, name), name
