# SEC_Node Properties

MANDATORY = "mandatory"
OPTIONAL = "optional"

# mandatory
MODULES = "modules"
EQUIPMENT_ID = "equipment_id"
DESCRIPTION = "description"

# optional
FIRMWARE = "firmware"
IMPLEMENTOR = "implementor"
TIMEOUT = "timeout"


# Module Properties

# mandatory
ACCESSIBLES = "accessibles"
# DESCRIPTION = "description"
INTERFACE_CLASSES = "interface_classes"

# optional
VISIBILITY = "visibility"
GROUP = "group"
MEANING = "meaning"
# IMPLEMENTOR = "implementor"
IMPLEMENTATION = "implementation"
FEATURES = "features"


# Accessible Properties

# mandatory
# DESCRIPTION = "description"
READONLY = "readonly"
DATAINFO = "datainfo"

# optional
# GROUP = "group"
# VISIBILITY = "visibility"
CONSTANT = "constant"


# Property Dicionaries

NODE_PROPERTIES = {
    MANDATORY: [MODULES, EQUIPMENT_ID, DESCRIPTION],
    OPTIONAL: [FIRMWARE, IMPLEMENTOR, TIMEOUT],
}

MODULE_PROPERTIES = {
    MANDATORY: [ACCESSIBLES, DESCRIPTION, INTERFACE_CLASSES],
    OPTIONAL: [VISIBILITY, GROUP, MEANING, IMPLEMENTOR, IMPLEMENTATION, FEATURES],
}

ACESSIBLE_PROPERTIES = {
    MANDATORY: [DESCRIPTION, READONLY, DATAINFO],
    OPTIONAL: [GROUP, VISIBILITY, CONSTANT],
}
