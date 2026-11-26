"""What the code generator renders: the generated classes and their attributes."""

import keyword
from dataclasses import dataclass, field
from inspect import Signature


@dataclass
class EnumMember:
    """Represents an enum member with name and value."""

    name: str  # Python identifier (e.g., "LOW")
    value: str  # Original SECoP string (e.g., "Low Energy")
    description: str | None = None  # Optional description


@dataclass
class EnumClass:
    """Represents an enum class definition."""

    name: str  # Enum class name (e.g., "TemperatureRegulatorModeEnum")
    members: list[EnumMember]
    description: str | None = None  # Optional enum description
    base_enum_class: str = "StrictEnum"  # "StrictEnum" or "SupersetEnum"


@dataclass
class TypedDictMember:
    """One key of a generated TypedDict."""

    key: str  # SECoP struct member name
    type: str  # type annotation, e.g. "float"
    required: bool = True  # False for an optional struct member


@dataclass
class TypedDictClass:
    """A TypedDict describing a SECoP struct, e.g. the target of a Drivable."""

    name: str
    members: list[TypedDictMember]
    description: str | None = None

    @property
    def class_syntax(self) -> bool:
        """Whether every key can be written as a class attribute; otherwise
        the TypedDict needs the functional syntax."""
        return all(
            member.key.isidentifier() and not keyword.iskeyword(member.key)
            for member in self.members
        )


@dataclass
class ModuleAttribute:
    """Represents a module attribute with name, type, and optional description."""

    name: str
    type: str


@dataclass
class ChannelAttribute:
    """A channel device of an acquisition controller: ``name`` (from its
    role), ``type`` (its module class) and the annotation naming its role and
    module."""

    name: str
    type: str
    annotation: str


@dataclass
class PropertyAttribute:
    """Represents a module property attribute with name, type"""

    name: str
    type_param: str | None = (
        None  # Optional type parameter like float for SignalRW[float]
    )
    path_annotation: str | None = (
        None  # Annotation like ParamPath(...) or PropPath(...)
    )

    type: str = "SignalR"  # Default to SignalR for properties


@dataclass
class ParameterAttribute:
    """Represents a module parameter attribute with name, type, and
    optional description."""

    name: str
    type: str
    type_param: str | None = (
        None  # Optional type parameter like float for SignalRW[float]
    )
    description: str | None = None  # Optional description from SECoP or docstrings

    path_annotation: str | None = (
        None  # Annotation like ParamPath(...) or PropPath(...)
    )
    format_annotation: str | None = None  # StandardReadableFormat.CONFIG_SIGNAL, etc.


@dataclass
class CommandAttribute:
    """Represents a SECoP command exposed as a Command/TriggerableCommand
    class-level annotation (the '<name>' attribute)."""

    name: str  # SECoP command name, e.g. "test_cmd"; also the attribute name
    command_type: str = "Command"  # "Command" or "TriggerableCommand"
    arg_type: str | None = None  # e.g. "dict[str, Any]"; None means no argument
    return_type: str | None = None  # e.g. "int"; None means no result
    description: str | None = None
    # Human-readable call signature, e.g. "execute(*, preset: PresetEnum, ...) -> None",
    # rendered as an attribute docstring so IDEs can show it on hover -- the
    # Command[[ArgT], ResT] annotation itself can't express per-field keyword
    # arguments for struct commands (ParamSpec only encodes a positional type list).
    call_signature: str | None = None


class Method:
    """Represents a class method with signature and description.

    This class supports both old-style initialization (for backward compatibility)
    and new-style dataclass initialization.
    """

    def __init__(self, cmd_name: str, description: str, cmd_sign: Signature) -> None:
        """Initialize Method (backward compatibility constructor).

        Args:
            cmd_name: Name of the command
            description: Description of the command
            cmd_sign: Signature of the command
        """
        raw_sig_str: str = str(cmd_sign)
        raw_sig_str = raw_sig_str.replace("typing.", "")

        if "self" in raw_sig_str:
            sig_str = raw_sig_str
        else:
            sig_str = "(self, " + raw_sig_str[1:]

        self.name: str = cmd_name
        self.signature: str = sig_str
        self.description: str = description


@dataclass
class ModuleClass:
    """Represents a module class to be generated."""

    name: str
    bases: list[str]
    parameters: list[ParameterAttribute] = field(default_factory=list)
    properties: list[PropertyAttribute] = field(default_factory=list)
    methods: list[Method] = field(default_factory=list)
    commands: list[CommandAttribute] = field(default_factory=list)
    description: str = ""
    enums: list[EnumClass] = field(default_factory=list)  # Enum classes for this module
    typed_dicts: list[TypedDictClass] = field(default_factory=list)
    # channel devices of an acquisition controller
    channels: list[ChannelAttribute] = field(default_factory=list)
    # (module name, description) of every module of this class
    module_descriptions: list[tuple[str, str]] = field(default_factory=list)


@dataclass
class NodeClass:
    """Represents a node class to be generated."""

    name: str
    bases: list[str]
    properties: list[PropertyAttribute] = field(default_factory=list)
    modules: list[ModuleAttribute] = field(default_factory=list)
    description: str = ""
