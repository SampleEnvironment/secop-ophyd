"""The generated class of one SECoP module, built from its description."""

from dataclasses import dataclass, field

from frappy.client import get_datatype
from frappy.datatypes import CommandType, EnumType, StructOf, TupleOf
from ophyd_async.core import SignalR, SignalRW, SignalW
from ophyd_async.core import StandardReadableFormat as Format
from ophyd_async.core._device import DEVICE_RESERVED_ATTRS

from secop_ophyd.codegen.model import (
    ChannelAttribute,
    CommandAttribute,
    EnumClass,
    EnumMember,
    ParameterAttribute,
    PropertyAttribute,
    TypedDictClass,
    TypedDictMember,
)
from secop_ophyd.datatypes.commands import (
    build_command_signature,
    command_dtype_to_annotation_str,
    command_result_python_type,
    python_type_to_str,
    secop_enum_name_to_python,
)
from secop_ophyd.datatypes.composite import (
    CompositeKind,
    classify_datatype,
    get_composite_members,
    optional_struct_members,
)
from secop_ophyd.datatypes.properties import module_property_signals
from secop_ophyd.datatypes.secop_dtype import get_type_param
from secop_ophyd.devices.acquisition.control import AcquisitionChannelType
from secop_ophyd.devices.base import (
    ParameterMemberType,
    ParameterType,
    PropertyType,
    SECoPDevice,
)
from secop_ophyd.devices.connector import (
    IGNORED_PROPS,
    acquisition_channels,
    clean_identifier,
    command_attr_name,
)
from secop_ophyd.devices.drivable import (
    SECoPCompositeMoveableDevice,
    SECoPMoveableDevice,
)
from secop_ophyd.devices.writable import SECoPWritableDevice

# device classes that are generic over the type of their 'target'
_SETTABLE_CLASSES = (
    SECoPWritableDevice,
    SECoPMoveableDevice,
    SECoPCompositeMoveableDevice,
)


def normalize_description(description: str | None) -> str:
    """Description text for generated comments: without trailing whitespace
    and newlines, keeping intentional internal newlines."""
    if description is None:
        return ""
    return description.rstrip()


def internalize_name(name: str) -> str:
    """how to create internal names"""
    if name.startswith("_"):
        return name[1:]
    return name


def _upper_first(word: str) -> str:
    """``word`` as part of a class name: a CamelCase word keeps its case
    (``FastController``), any other is capitalized (``OPHYD`` -> ``Ophyd``)."""
    if word.islower() or word.isupper():
        return word.capitalize()
    return word[:1].upper() + word[1:]


def _camel(identifier: str) -> str:
    words = identifier.replace(" ", "_").replace("-", "_").split("_")
    return "".join(word.capitalize() for word in words)


def _build_enum_class(
    enum_class_name: str, members_dict: dict, description: str
) -> "EnumClass | None":
    """Build an EnumClass from a SECoP enum members dict ({name: value}),
    shared by the parameter and command enum-detection code paths.
    Returns None if there are no members."""
    if not members_dict:
        return None

    enum_members = [
        EnumMember(name=secop_enum_name_to_python(member_name), value=member_name)
        for member_name in members_dict
    ]

    return EnumClass(
        name=enum_class_name, members=enum_members, description=description
    )


def _value_type(type_param: str | None) -> str:
    """Type of a struct/tuple member in the argument of set(), from the type
    parameter of the member's Signal: arrays are given as any sequence."""
    if type_param is None:
        return "Any"
    if type_param == "ndarray":
        return "Sequence[Any]"
    return type_param


def _composite_type(
    datatype: StructOf | TupleOf,
    optional: list[str],
    member_types: list[tuple[str, str]],
    name: str,
    description: str,
    typed_dicts: list[TypedDictClass],
) -> str:
    """Type annotation for the value of a struct/tuple parameter: a tuple
    type, or the name of a TypedDict that is added to ``typed_dicts``.
    ``optional`` are the struct members that may be left out."""
    if isinstance(datatype, TupleOf):
        return f"tuple[{', '.join(member_type for _, member_type in member_types)}]"

    typed_dicts.append(
        TypedDictClass(
            name=name,
            members=[
                TypedDictMember(key=key, type=member_type, required=key not in optional)
                for key, member_type in member_types
            ],
            description=description,
        )
    )
    return name


def split_accessibles(moddescr: dict) -> tuple[dict[str, dict], dict[str, dict]]:
    """The parameters and the commands of a module description, by internal
    name, each with its frappy datatype added under 'datatype'."""
    parameters = {}
    commands = {}
    for aname, aentry in moddescr["accessibles"].items():
        iname = internalize_name(aname)
        datatype = get_datatype(aentry["datainfo"], iname)

        aentry = dict(aentry, datatype=datatype)

        if datatype.IS_COMMAND:
            commands[iname] = aentry
        else:
            parameters[iname] = aentry
    return parameters, commands


def module_class_name(modname: str, properties: dict) -> str:
    """Name of the generated class of a module: its self-reported
    "implementation" module property (or else the module name), in CamelCase.

    A controller class declares its channels, which differ between
    controllers of one implementation: there is one class per controller,
    with the module name appended."""
    module_class = modname
    if properties.get("implementation", ""):
        module_class = properties.get("implementation", "").split(".").pop()

    module_class_list = module_class.replace(" ", "_").replace("-", "_").split("_")

    # keep the case inside CamelCase words: FastController stays one
    module_class = "".join(_upper_first(word) for word in module_class_list)

    if acquisition_channels(properties):
        module_class += "".join(
            _upper_first(word) for word in modname.replace("-", "_").split("_")
        )
    return module_class


def _format_annotation(param_data: dict, hinted: bool) -> str | None:
    """The StandardReadableFormat of a parameter, as written in its
    annotation: from its '_signal_format' property, HINTED_SIGNAL if its
    device class hints it, None for the default (CONFIG_SIGNAL)."""
    format = None

    match param_data.get("_signal_format", None):
        case "HINTED_SIGNAL":
            format = Format.HINTED_SIGNAL
        case "HINTED_UNCACHED_SIGNAL":
            format = Format.HINTED_UNCACHED_SIGNAL
        case "UNCACHED_SIGNAL":
            format = Format.UNCACHED_SIGNAL

    if hinted:
        format = format or Format.HINTED_SIGNAL

    # Remove "StandardReadable" prefix from format for cleaner annotation
    return str(format).removeprefix("StandardReadable") if format else None


def _parameter_description(param_data: dict) -> str:
    descr = normalize_description(param_data.get("description", ""))
    unit = param_data["datainfo"].get("unit")

    if unit:
        return f"{descr}; Unit: ({unit})" if descr else f"Unit: ({unit})"
    return descr


@dataclass
class ModuleClassBuilder:
    """Builds the attributes of the generated class of one module, and the
    enum classes and TypedDicts they need."""

    #: name of the generated class
    name: str
    #: the secop-ophyd device class it is derived from
    device_class: type[SECoPDevice]
    enums: list[EnumClass] = field(default_factory=list)
    typed_dicts: list[TypedDictClass] = field(default_factory=list)
    #: type of 'target', which the settable device classes are generic over
    target_type: str | None = None

    @property
    def _set_takes_target(self) -> bool:
        """The device class's set() takes the whole target: a struct/tuple
        target is only changed through set(), so it gets no writable
        Signal."""
        return issubclass(self.device_class, SECoPWritableDevice)

    def bases(self) -> list[str]:
        """Base classes of the generated class: the device class,
        parametrized with the type of 'target' if it is generic over it (e.g.
        SECoPMoveableDevice[float] instead of the implicitly Any form)."""
        base = self.device_class.__name__
        if self.target_type and self.device_class in _SETTABLE_CLASSES:
            return [f"{base}[{self.target_type}]"]
        return [base]

    def _add_enum(self, name: str, members: dict, description: str) -> str | None:
        """Add an enum class and return its name; None if it has no
        members."""
        enum_cls = _build_enum_class(name, members, description)
        if enum_cls is None:
            return None
        self.enums.append(enum_cls)
        return enum_cls.name

    def _enum_type_param(
        self,
        type_param: str | None,
        enum_class_name: str,
        members: dict,
        enum_descr: str,
    ) -> str | None:
        """If `type_param` is the generic 'StrictEnum', generate a concrete
        named enum class for it and return that class's name instead;
        otherwise return `type_param` unchanged."""
        if not (type_param and "StrictEnum" in type_param):
            return type_param
        return self._add_enum(enum_class_name, members, enum_descr) or type_param

    # --- commands --------------------------------------------------------

    def commands(self, commands: dict[str, dict]) -> list[CommandAttribute]:
        # "stop" is skipped: the Drivable and Acquisition device classes
        # already implement Stoppable.stop() (a real bound method), so the
        # generated annotation must not claim an attribute that will never
        # actually be created (see the matching skip in SECoPDeviceConnector)
        return [
            self._command(command, command_data)
            for command, command_data in commands.items()
            if command != "stop"
        ]

    def _command(self, command: str, command_data: dict) -> CommandAttribute:
        cmd_datatype: CommandType = command_data["datatype"]
        arg_dt, res_dt = cmd_datatype.argument, cmd_datatype.result

        command_camel = _camel(command)

        arg_type = (
            command_dtype_to_annotation_str(arg_dt) if arg_dt is not None else None
        )
        if isinstance(arg_dt, EnumType):
            arg_type = (
                self._add_enum(
                    f"{self.name}_{command_camel}_Arg_Enum",
                    arg_dt.export_datatype().get("members", {}),
                    f"{command} argument enum for `{self.name}`.",
                )
                or arg_type
            )

        return_type = (
            python_type_to_str(command_result_python_type(res_dt))
            if res_dt is not None
            else None
        )
        if isinstance(res_dt, EnumType):
            return_type = (
                self._add_enum(
                    f"{self.name}_{command_camel}_Result_Enum",
                    res_dt.export_datatype().get("members", {}),
                    f"{command} result enum for `{self.name}`.",
                )
                or return_type
            )

        is_triggerable = arg_dt is None and res_dt is None

        # Struct args unravel into one keyword-only parameter per member -- a
        # shape the Command[[ArgT], ResT] annotation can't express, so spell
        # it out here. build_command_signature() is the same function used to
        # build the real runtime signature, so the rendered text matches
        # `.signature` exactly (including the dynamically-named member enum
        # classes, e.g. PresetEnum). For a bare (non-struct) arg/result, the
        # already-resolved arg_type/return_type (which reuse the concrete
        # generated enum class name, unlike a fresh build_command_signature()
        # call) are used directly instead.
        call_signature: str | None = None
        if not is_triggerable:
            if isinstance(arg_dt, StructOf):
                sig = build_command_signature(cmd_datatype)
                call_signature = "execute" + str(sig).replace("typing.", "")
            else:
                arg_part = f"arg: {arg_type}, " if arg_type else ""
                call_signature = (
                    f"execute({arg_part}wait_for_idle: bool = False) -> "
                    f"{return_type or 'None'}"
                )

        return CommandAttribute(
            name=command_attr_name(command),
            command_type="TriggerableCommand" if is_triggerable else "Command",
            arg_type=arg_type,
            return_type=return_type,
            description=normalize_description(command_data.get("description", "")),
            call_signature=call_signature,
        )

    # --- parameters ------------------------------------------------------

    def parameters(self, parameters: dict[str, dict]) -> list[ParameterAttribute]:
        attributes: list[ParameterAttribute] = []
        for param_name, param_data in parameters.items():
            attributes += self._parameter(param_name, param_data)
        return attributes

    def _parameter(self, param_name: str, param_data: dict) -> list[ParameterAttribute]:
        """The Signals of a parameter: one for an atomic parameter, one per
        member (and a write-only one for the whole) for a struct/tuple."""
        param_descr = _parameter_description(param_data)

        # depending on the Interface class other parameter need to be declared
        # as readsignals as well
        format_str = _format_annotation(
            param_data, param_name in self.device_class.hinted_signals
        )

        raw_datatype = param_data["datatype"]
        kind = classify_datatype(raw_datatype)
        readonly = param_data["readonly"]

        if kind is CompositeKind.UNSUPPORTED:
            # mirrors SECoPDeviceConnector.connect_real(): no signal is
            # generated for a struct/tuple nested inside another
            # struct/tuple/array. Unlike the runtime path, codegen has no
            # notion of "mandatory for this instance's interface class" at
            # generation time, so it always just skips -- the runtime path is
            # still the one that raises for a genuinely mandatory parameter
            # when actually connecting.
            return []

        if kind is CompositeKind.DECOMPOSABLE:
            return self._composite_parameter(
                param_name, param_data, readonly, param_descr, format_str
            )

        # ATOMIC: single signal
        signal_base = SignalR if readonly else SignalRW
        datainfo = param_data.get("datainfo", {})

        # infer the ophyd type from secop datatype
        type_param = self._enum_type_param(
            get_type_param(raw_datatype),
            f"{self.name}_{_camel(param_name)}_Enum",
            datainfo.get("members", {}),
            f"{param_name} enum for `{self.name}`.",
        )

        if param_name == "target":
            self.target_type = type_param

        return [
            ParameterAttribute(
                name=param_name,
                type=signal_base.__name__,
                type_param=type_param,
                description=param_descr,
                path_annotation=str(ParameterType()),
                format_annotation=format_str,
            )
        ]

    def _composite_parameter(
        self,
        param_name: str,
        param_data: dict,
        readonly: bool,
        param_descr: str,
        format_str: str | None,
    ) -> list[ParameterAttribute]:
        """A read-only Signal per member of a struct/tuple parameter, and a
        write-only Signal for the whole parameter if it can be written."""
        raw_datatype = param_data["datatype"]
        attributes: list[ParameterAttribute] = []

        # (member key, type of the member's value)
        member_types: list[tuple[str, str]] = []

        for member_key, member_dt in get_composite_members(raw_datatype):
            member_type_param = get_type_param(member_dt)
            if isinstance(member_dt, EnumType):
                # an EnumType member embedded in a struct/tuple resolves to
                # its member name string at runtime, just like a standalone
                # top-level enum parameter -- so it gets its own generated
                # enum class too
                member_type_param = self._enum_type_param(
                    member_type_param,
                    f"{self.name}_{_camel(param_name)}_{_camel(member_key)}_Enum",
                    member_dt.export_datatype().get("members", {}),
                    f"{param_name}.{member_key} enum for `{self.name}`.",
                )

            member_types.append((member_key, _value_type(member_type_param)))

            attributes.append(
                ParameterAttribute(
                    name=f"{param_name}_{member_key}",
                    type=SignalR.__name__,
                    type_param=member_type_param,
                    description=param_descr,
                    path_annotation=str(ParameterMemberType(param_name, member_key)),
                    format_annotation=format_str,
                )
            )

        if self._set_takes_target and param_name == "target":
            # changed only through set(): no writable Signal
            self.target_type = _composite_type(
                raw_datatype,
                optional_struct_members(param_data["datainfo"]),
                member_types,
                f"{self.name}_Target",
                f"target of `{self.name}`.",
                self.typed_dicts,
            )
        elif not readonly:
            attributes.append(
                ParameterAttribute(
                    name=param_name,
                    type=SignalW.__name__,
                    type_param=get_type_param(raw_datatype),
                    description=param_descr,
                    path_annotation=str(ParameterType()),
                    # a write-only SignalW can't carry a StandardReadableFormat
                    # (nothing to read)
                    format_annotation=None,
                )
            )
        return attributes

    # --- properties and channels -----------------------------------------

    def properties(self, properties: dict) -> list[PropertyAttribute]:
        return [
            PropertyAttribute(
                name=prop_name,
                type=SignalR.__name__,
                type_param=get_type_param(prop_dtype),
                path_annotation=str(PropertyType()),
            )
            for prop_name, (prop_dtype, _) in module_property_signals(
                properties
            ).items()
            if prop_name not in IGNORED_PROPS
        ]

    def channels(
        self,
        controller_channels: dict[str, str],
        taken: set[str],
        class_names: dict[str, str],
    ) -> list[ChannelAttribute]:
        """The channel devices of an acquisition controller, named by role.

        ``controller_channels`` maps role -> channel module, ``taken`` are the
        attribute names already in use and ``class_names`` the generated
        class of each module."""
        taken = set(taken)
        attributes: list[ChannelAttribute] = []
        for role, channel_module in controller_channels.items():
            attr_name = clean_identifier(role)
            while attr_name in DEVICE_RESERVED_ATTRS or attr_name in taken:
                attr_name += "_"
            taken.add(attr_name)
            attributes.append(
                ChannelAttribute(
                    name=attr_name,
                    type=class_names[channel_module],
                    annotation=repr(AcquisitionChannelType(role, channel_module)),
                )
            )
        return attributes
