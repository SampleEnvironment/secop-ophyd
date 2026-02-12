"""Code generation for annotated ophyd device classes using Jinja2 templates."""

import inspect
import json
import linecache
import re
import sys
from dataclasses import dataclass, field
from enum import StrEnum
from importlib import import_module, reload
from inspect import Signature
from logging import Logger
from pathlib import Path
from types import ModuleType
from typing import get_type_hints

import autoflake
import black
from frappy.client import get_datatype
from frappy.datatypes import DataType
from jinja2 import Environment, PackageLoader, select_autoescape
from ophyd_async.core import Signal, SignalR, SignalRW, StandardReadable
from ophyd_async.core import StandardReadableFormat as Format
from ophyd_async.core._utils import get_origin_class

from secop_ophyd.SECoPDevices import (
    IGNORED_PROPS,
    ParamPath,
    PropPath,
    class_from_interface,
    secop_enum_name_to_python,
)
from secop_ophyd.SECoPSignal import secop_dtype_obj_from_json
from secop_ophyd.util import SECoPdtype


def internalize_name(name: str) -> str:
    """how to create internal names"""
    if name.startswith("_"):
        return name[1:]
    return name


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
class ModuleAttribute:
    """Represents a module attribute with name, type, and optional description."""

    name: str
    type: str


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
    description: str = ""
    enums: list[EnumClass] = field(default_factory=list)  # Enum classes for this module


@dataclass
class NodeClass:
    """Represents a node class to be generated."""

    name: str
    bases: list[str]
    properties: list[PropertyAttribute] = field(default_factory=list)
    modules: list[ModuleAttribute] = field(default_factory=list)
    description: str = ""


class GenNodeCode:
    """Generates annotated Python classes for SECoP ophyd devices.

    This class can generate Python code in two ways:
    1. By introspecting a fully instantiated SECoP ophyd device
    2. From a SECoP JSON describe message (future feature)

    The generated code uses Jinja2 templates and is formatted with Black.
    """

    ModName: str = "genNodeClass"

    def __init__(self, path: str | None = None, log=None):
        """Initialize the code generator.

        Args:
            path: Optional path to the module folder
            log: Optional logger instance
        """
        self.log: Logger | None = log
        self.module_folder_path: Path | None = None
        if path is not None:
            self.module_folder_path = Path(path)

        # Data structures for classes and imports
        self.imports: dict[str, set[str] | None] = {}
        self.module_classes: list[ModuleClass] = []
        self.node_classes: list[NodeClass] = []
        self.enum_classes: list[EnumClass] = []
        self.node_mod: ModuleType | None = None

        # Required imports for abstract classes
        self.add_import("abc", "abstractmethod")
        self.add_import("typing", "Annotated as A")
        self.add_import("ophyd_async.core", "SignalR")
        self.add_import("ophyd_async.core", "SignalRW")
        self.add_import("ophyd_async.core", "SignalX")
        self.add_import("ophyd_async.core", "StandardReadableFormat as Format")
        self.add_import("ophyd_async.core", "StrictEnum")
        self.add_import("ophyd_async.core", "SupersetEnum")
        self.add_import("typing", "Any")
        self.add_import("numpy", "ndarray")
        self.add_import("secop_ophyd.SECoPDevices", "ParamPath")
        self.add_import("secop_ophyd.SECoPDevices", "PropPath")
        # Add necessary Device imports
        self.add_import("secop_ophyd.SECoPDevices", "SECoPDevice")
        self.add_import("secop_ophyd.SECoPDevices", "SECoPCommunicatorDevice")
        self.add_import("secop_ophyd.SECoPDevices", "SECoPReadableDevice")
        self.add_import("secop_ophyd.SECoPDevices", "SECoPTriggerableDevice")
        self.add_import("secop_ophyd.SECoPDevices", "SECoPWritableDevice")
        self.add_import("secop_ophyd.SECoPDevices", "SECoPMoveableDevice")
        self.add_import("secop_ophyd.SECoPDevices", "SECoPNodeDevice")

        # Setup Jinja2 environment
        self.jinja_env = Environment(
            loader=PackageLoader("secop_ophyd", "templates"),
            autoescape=select_autoescape(),
            trim_blocks=False,
            lstrip_blocks=False,
            keep_trailing_newline=True,
        )

        # Try to load existing generated module
        self._load_existing_module()

    def _load_existing_module(self):
        """Load existing generated module if present."""

        mod_path = self.ModName

        if self.module_folder_path is not None:
            # For absolute paths, we need to add to sys.path and import just the module
            # name
            if self.module_folder_path.is_absolute():
                str_path = str(self.module_folder_path)
                if str_path not in sys.path:
                    sys.path.insert(0, str_path)
                # Just use the module name when the folder is in sys.path
                mod_path = self.ModName
            else:
                # For relative paths, construct the module path with dots
                str_path = str(self.module_folder_path)
                rep_slash = str_path.replace("/", ".").replace("\\", ".")
                mod_path = f"{rep_slash}.{self.ModName}"

        # Remove cached module to ensure fresh import (important when module file
        # has been modified or recreated between imports)
        if mod_path in sys.modules:
            del sys.modules[mod_path]

        # Clear linecache for the module file to ensure inspect.getsource() works
        if self.module_folder_path is not None:
            module_file = self.module_folder_path / f"{self.ModName}.py"
            linecache.checkcache(str(module_file))

        try:
            self.node_mod = import_module(mod_path)
            self._parse_existing_module()
        except ModuleNotFoundError:
            if self.log is None:
                print("No code generated yet, building from scratch")
            else:
                self.log.info("No code generated yet, building from scratch")

    def _parse_existing_module(self):
        """Parse an existing generated module to extract class definitions."""
        # Prevent circular import

        from secop_ophyd.SECoPDevices import (
            SECoPDevice,
            SECoPNodeDevice,
        )

        if self.node_mod is None:
            return

        modules = inspect.getmembers(self.node_mod)
        # Filter to only classes defined in this module, not imported ones
        class_members = [
            m
            for m in modules
            if inspect.isclass(m[1]) and m[1].__module__ == self.node_mod.__name__
        ]

        enum_classes = [m for m in class_members if issubclass(m[1], StrEnum)]
        node_classes = [m for m in class_members if issubclass(m[1], SECoPNodeDevice)]
        module_classes = [
            m
            for m in class_members
            if issubclass(m[1], SECoPDevice) and not issubclass(m[1], SECoPNodeDevice)
        ]

        for class_symbol, class_obj in enum_classes:
            self._parse_enum_class(class_symbol, class_obj)

        for class_symbol, class_obj in node_classes:
            self._parse_node_class(class_symbol, class_obj)

        for class_symbol, class_obj in module_classes:
            self._parse_module_class(class_symbol, class_obj)

    def _parse_node_class(self, class_symbol: str, class_obj: type):
        """Parse a node class from existing module.

        Args:
            class_symbol: Name of the class
            class_obj: The class object
        """
        # attrs = self._extract_attrs_from_source(inspect.getsource(class_obj))

        bases = [base.__name__ for base in class_obj.__bases__]

        # Extract description from docstring
        description = inspect.getdoc(class_obj) or ""

        _, properties, modules = self._get_attr_list(class_obj)

        node_cls = NodeClass(
            name=class_symbol,
            bases=bases,
            properties=properties,
            modules=modules,
            description=description,
        )
        self.node_classes.append(node_cls)

    def _extract_descriptions_from_source(self, class_obj: type) -> dict[str, str]:
        """Extract trailing comment descriptions from class source code.

        Args:
            class_obj: The class object to extract descriptions from

        Returns:
            Dictionary mapping attribute names to their descriptions
        """
        descriptions = {}
        try:
            source = inspect.getsource(class_obj)
            for line in source.split("\n"):
                # Skip lines without comments
                if "#" not in line:
                    continue

                # Extract the part before # (attribute assignment)
                code_part, comment_part = line.split("#", 1)

                # Find attribute name (e.g., "count" from "count: A[SignalRW[int],...")
                match = re.match(r"\s*(\w+)\s*:", code_part)
                if match:
                    attr_name = match.group(1)
                    description = comment_part.strip()
                    if description:
                        descriptions[attr_name] = description
        except Exception as e:
            if self.log:
                self.log.debug(f"Could not extract descriptions from source: {e}")

        return descriptions

    def _get_attr_list(
        self, class_obj: type
    ) -> tuple[
        list[ParameterAttribute], list[PropertyAttribute], list[ModuleAttribute]
    ]:
        hints = get_type_hints(class_obj)
        # Get hints with Annotated for wrapping signals and backends
        extra_hints = get_type_hints(class_obj, include_extras=True)

        # Extract description comments from source code
        descriptions = self._extract_descriptions_from_source(class_obj)

        modules = []
        properties = []
        parameters = []

        for attr_name, annotation in hints.items():
            extras = getattr(extra_hints[attr_name], "__metadata__", ())

            origin = get_origin_class(annotation)

            if issubclass(origin, Signal):

                sig_type = annotation.__args__[0]
                # Get the module name
                module = sig_type.__module__

                type_param = (
                    sig_type.__name__ if module == "builtins" else sig_type.__name__
                )

                path_annotation = next(
                    (e for e in extras if isinstance(e, (ParamPath, PropPath))), None
                )
                category = (
                    "property" if isinstance(path_annotation, PropPath) else "parameter"
                )
                format_annotation = next(
                    (e for e in extras if isinstance(e, Format)), None
                )
                if format_annotation is not None:
                    format_annotation = f"Format.{format_annotation.name}"

                # Get description from comments
                description = descriptions.get(attr_name)

                match category:
                    case "property":
                        properties.append(
                            PropertyAttribute(
                                name=attr_name,
                                type=origin.__name__,
                                type_param=type_param,
                                path_annotation=str(path_annotation),
                            )
                        )
                    case "parameter":
                        parameters.append(
                            ParameterAttribute(
                                name=attr_name,
                                type=origin.__name__,
                                type_param=type_param,
                                description=description,
                                path_annotation=str(path_annotation),
                                format_annotation=format_annotation,
                            )
                        )

            if issubclass(origin, StandardReadable):
                modules.append(ModuleAttribute(name=attr_name, type=origin.__name__))

        return parameters, properties, modules

    def _parse_enum_class(self, class_symbol: str, class_obj: type):
        """Parse an enum class from existing module.

        Args:
            class_symbol: Name of the class
            class_obj: The class object

        """
        # Extract description from docstring
        description = inspect.getdoc(class_obj) or ""

        # Extract enum members from class attributes
        members = []

        for attr_name, attr_value in class_obj.__dict__.items():
            # Skip private/magic attributes and methods
            if attr_name.startswith("_") or callable(attr_value):
                continue

            # Create an EnumMember for each enum value
            # attr_name is the member name (e.g., "RAMP")
            # attr_value is the member value (e.g., "ramp")
            member = EnumMember(name=attr_name, value=attr_value, description=None)
            members.append(member)

        bases = [base.__name__ for base in class_obj.__bases__]

        # Create and return the EnumClass
        self.enum_classes.append(
            EnumClass(
                name=class_symbol,
                members=members,
                description=description,
                base_enum_class=bases[0] if bases else "StrictEnum",
            )
        )

    def _parse_module_class(self, class_symbol: str, class_obj: type):
        """Parse a module class from existing module.

        Args:
            class_symbol: Name of the class
            class_obj: The class object
        """
        # Extract attributes from source code to get proper type annotations

        parameters, properties, _ = self._get_attr_list(class_obj)

        methods = []
        for method_name, method in class_obj.__dict__.items():
            if callable(method) and not method_name.startswith("__"):
                method_source = inspect.getsource(method)
                description = self._extract_method_description(method_source)
                methods.append(
                    Method(method_name, description, inspect.signature(method))
                )

        bases = [base.__name__ for base in class_obj.__bases__]

        # Extract description from docstring
        description = inspect.getdoc(class_obj) or ""

        mod_enums: list[EnumClass] = []
        enums = {enum_class.name: enum_class for enum_class in self.enum_classes}

        for param in parameters:
            if param.type_param in enums:
                enum_class = enums[param.type_param]
                if enum_class not in mod_enums:
                    mod_enums.append(enum_class)

        mod_cls = ModuleClass(
            name=class_symbol,
            bases=bases,
            parameters=parameters,
            properties=properties,
            methods=methods,
            description=description,
            enums=mod_enums,
        )
        self.module_classes.append(mod_cls)

    def _extract_method_description(self, method_source: str) -> str:
        """Extract description from method docstring.

        Args:
            method_source: Source code of the method

        Returns:
            Description string
        """
        match = re.search(r"\s*def\s+\w+\s*\(.*\).*:\s*", method_source)
        if match:
            function_body = method_source[match.end() :]
            description_list = function_body.split('"""', 2)
            if len(description_list) > 1:
                return description_list[1]
        return ""

    def add_import(self, module: str, class_str: str | None = None):
        """Add an import to the import dictionary.

        Args:
            module: Python module to import from
            class_str: Class/symbol to import. If None or empty, imports the module
            directly.
        """
        if class_str is None or class_str == "":
            # For module-only imports (import module), use None as value
            if module not in self.imports:
                self.imports[module] = None
        else:
            existing = self.imports.get(module)
            if existing is None:
                # Convert from module-only import to specific imports
                self.imports[module] = {class_str}
            elif isinstance(existing, set):
                existing.add(class_str)
            else:
                self.imports[module] = {class_str}

    def add_mod_class(
        self,
        module_cls: str,
        bases: list[str],
        parameters: list[ParameterAttribute],
        properties: list[PropertyAttribute],
        cmd_plans: list[Method],
        description: str = "",
        enum_classes: list[EnumClass] | None = None,
    ):
        """Add a module class to be generated.

        Args:
            module_cls: Name of the module class
            bases: Base classes
            parameters: List of parameter attributes
            properties: List of property attributes
            cmd_plans: List of method definitions
            description: Optional class description
        """
        # Check if class already exists (loaded from file)
        existing_class = next(
            (cls for cls in self.module_classes if cls.name == module_cls), None
        )
        if existing_class:
            # Class already exists - merge enums if provided
            if enum_classes:
                existing_class.enums.extend(enum_classes)
                if self.log:
                    self.log.info(
                        f"Module class {module_cls} already exists, "
                        f"merged {len(enum_classes)} enum(s)"
                    )
            return

        mod_cls = ModuleClass(
            name=module_cls,
            bases=bases,
            parameters=parameters,
            properties=properties,
            methods=cmd_plans,
            description=description,
            enums=enum_classes or [],
        )
        self.module_classes.append(mod_cls)

    def add_node_class(
        self,
        node_cls: str,
        bases: list[str],
        properties: list[PropertyAttribute],
        modules: list[ModuleAttribute],
        description: str = "",
    ):
        """Add a node class to be generated.

        Args:
            node_cls: Name of the node class
            bases: Base classes
            attrs: List of attribute tuples. Supported formats:
                   - (name, type)
                   - (name, type, type_param)
                   - (name, type, type_param, description, category)
        """
        # Check if class already exists (loaded from file)
        existing_class = next(
            (cls for cls in self.node_classes if cls.name == node_cls), None
        )
        if existing_class:
            # Class already exists, skip adding it
            if self.log:
                self.log.info(f"Node class {node_cls} already exists, skipping")
            return

        node_class = NodeClass(
            name=node_cls,
            bases=bases,
            properties=properties,
            modules=modules,
            description=description,
        )
        self.node_classes.append(node_class)

    def _parse_command_signature(
        self, cmd_name: str, datainfo: dict, description: str
    ) -> Method:
        """Parse command datainfo to create Method signature.

        Args:
            cmd_name: Name of the command
            datainfo: Command datainfo with argument/result types
            description: Command description

        Returns:
            Method object with signature
        """
        # Extract argument and result types
        arg_type = datainfo.get("argument")

        # Create a basic signature object
        sig = Signature.from_callable(lambda self, wait_for_idle=False: None)
        if arg_type is not None:
            sig = Signature.from_callable(lambda self, arg, wait_for_idle=False: None)

        return Method(cmd_name=cmd_name, description=description, cmd_sign=sig)

    def from_json_describe(self, json_data: str | dict):
        """Generate classes from a SECoP JSON describe message.

        Args:
            json_data: JSON string or dict containing SECoP describe data
        """
        # Parse JSON if string
        if isinstance(json_data, str):
            describe_data = json.loads(json_data)
        else:
            describe_data = json_data

        modules: dict[str, dict] = describe_data.get("modules", {})
        node_properties = {k: v for k, v in describe_data.items() if k != "modules"}

        # Parse modules
        node_module_attrs: list[ModuleAttribute] = []
        node_property_attrs: list[PropertyAttribute] = []

        for modname, moddescr in modules.items():
            #  separate accessibles into command and parameters
            parameters = {}
            commands = {}
            accessibles = moddescr["accessibles"]
            for aname, aentry in accessibles.items():
                iname = internalize_name(aname)
                datatype = get_datatype(aentry["datainfo"], iname)

                aentry = dict(aentry, datatype=datatype)

                if datatype.IS_COMMAND:
                    commands[iname] = aentry
                else:
                    parameters[iname] = aentry

            properties = {k: v for k, v in moddescr.items() if k != "accessibles"}

            # Add module class (highest secop interface class) that the actual
            # module class is derived from
            secop_ophyd_modclass = class_from_interface(properties)
            module_bases = [secop_ophyd_modclass.__name__]

            # Add the module class, use self reported "implementation" module property,
            # if not present use the module name
            module_class = modname
            if properties.get("implementation") is not None:
                module_class = properties.get("implementation", "").split(".").pop()

            # Module enum classes
            module_enum_classes = []

            # Prepare attributes

            # Module Commands
            command_plans = []

            for command, command_data in commands.items():
                # Stop is already an ophyd native operation
                if command == "stop":
                    continue

                argument = command_data["datainfo"].get("argument")
                result = command_data["datainfo"].get("result")

                description: str = ""
                description += f"{command_data['description']}\n"

                if argument:
                    description += (
                        f"       argument: {command_data['datainfo'].get('argument')}\n"
                    )
                if result:
                    description += (
                        f"       result: {command_data['datainfo'].get('result')}"
                    )

                def command_plan(self, arg, wait_for_idle: bool = False):
                    pass

                def command_plan_no_arg(self, wait_for_idle: bool = False):
                    pass

                plan = Method(
                    cmd_name=command,
                    description=description,
                    cmd_sign=inspect.signature(
                        command_plan if argument else command_plan_no_arg
                    ),
                )

                command_plans.append(plan)

            mod_parameters: list[ParameterAttribute] = []

            for param_name, param_data in parameters.items():

                descr = param_data["description"]
                unit = param_data["datainfo"].get("unit")

                param_descr = f"{descr}; Unit: ({unit})" if unit else descr
                signal_base = SignalR if param_data["readonly"] else SignalRW

                format = None

                # infer format from parameter property
                match param_data.get("_signal_format", None):
                    case "HINTED_SIGNAL":
                        format = Format.HINTED_SIGNAL
                    case "HINTED_UNCACHED_SIGNAL":
                        format = Format.HINTED_UNCACHED_SIGNAL
                    case "UNCACHED_SIGNAL":
                        format = Format.UNCACHED_SIGNAL
                    case _:
                        format = None

                # depending on the Interface class other parameter need to be declared
                # as readsignals as well
                if param_name in secop_ophyd_modclass.hinted_signals:
                    format = format or Format.HINTED_SIGNAL

                # Remove "StandardReadable" prefix from format for cleaner annotation
                format = (
                    str(format).removeprefix("StandardReadable") if format else None
                )

                datainfo = param_data.get("datainfo", {})

                # infer the ophyd type from secop datatype
                type_param = get_type_param(param_data["datatype"])

                # Handle StrictEnum types - generate enum class
                if type_param and "StrictEnum" in type_param:
                    # Generate unique enum class name:
                    # ModuleClass + ParamName + Enum
                    enum_class_name = f"{module_class}{param_name.capitalize()}Enum"

                    # Extract enum members from datainfo
                    enum_members_dict = datainfo.get("members", {})
                    if enum_members_dict:
                        from secop_ophyd.GenNodeCode import EnumClass, EnumMember

                        enum_members = []
                        for member_value, _ in enum_members_dict.items():
                            # Convert member name to Python identifier
                            python_name = secop_enum_name_to_python(member_value)
                            enum_members.append(
                                EnumMember(
                                    name=python_name,
                                    value=member_value,
                                    description=None,
                                )
                            )

                        # Create enum class definition
                        enum_descr = f"{param_name} enum for `{module_class}`."

                        enum_cls = EnumClass(
                            name=enum_class_name,
                            members=enum_members,
                            description=enum_descr,
                        )

                        module_enum_classes.append(enum_cls)

                        # Use the specific enum class name instead of generic
                        # StrictEnum
                        type_param = enum_class_name

                # Default format for parameters is CONFIG_SIGNAL

                mod_parameters.append(
                    ParameterAttribute(
                        name=param_name,
                        type=signal_base.__name__,
                        type_param=type_param,
                        description=param_descr,
                        path_annotation=str(ParamPath(f"{modname}:{param_name}")),
                        format_annotation=format,
                    )
                )

            # Module properties
            module_properties: list[PropertyAttribute] = []

            # Process module properties
            for prop_name, property_value in properties.items():

                if prop_name in IGNORED_PROPS:
                    continue

                type_param = get_type_param(secop_dtype_obj_from_json(property_value))

                module_properties.append(
                    PropertyAttribute(
                        name=prop_name,
                        type=SignalR.__name__,
                        type_param=type_param,
                        path_annotation=str(PropPath(f"{modname}:{prop_name}")),
                    )
                )

            self.add_mod_class(
                module_cls=module_class,
                bases=module_bases,
                parameters=mod_parameters,
                properties=module_properties,
                cmd_plans=command_plans,
                description=properties.get("description", ""),
                enum_classes=module_enum_classes,
            )

            # Add to node attributes
            # Type the None explicitly as str | None to match other entries
            node_module_attrs.append(ModuleAttribute(name=modname, type=module_class))

        # Process module properties
        for prop_name, property_value in node_properties.items():
            type_param = get_type_param(secop_dtype_obj_from_json(property_value))

            # Generate PropPath annotation for node-level properties

            node_property_attrs.append(
                PropertyAttribute(
                    name=prop_name,
                    type=SignalR.__name__,
                    type_param=type_param,
                    path_annotation=str(PropPath(prop_name)),
                )
            )

        # Add node class
        node_bases = ["SECoPNodeDevice"]

        equipment_id: str = node_properties["equipment_id"]

        # format node class accordingly
        node_class_name = equipment_id.replace(".", "_").replace("-", "_").capitalize()

        self.add_node_class(
            node_cls=node_class_name,
            bases=node_bases,
            modules=node_module_attrs,
            properties=node_property_attrs,
            description=node_properties.get("description", ""),
        )

        # Add required imports
        self.add_import("secop_ophyd.SECoPDevices", "SECoPNodeDevice")
        self.add_import("secop_ophyd.SECoPDevices", "SECoPBaseDevice")
        self.add_import("secop_ophyd.SECoPDevices", "SECoPCommunicatorDevice")
        self.add_import("secop_ophyd.SECoPDevices", "SECoPReadableDevice")
        self.add_import("secop_ophyd.SECoPDevices", "SECoPWritableDevice")
        self.add_import("secop_ophyd.SECoPDevices", "SECoPMoveableDevice")
        self.add_import("secop_ophyd.SECoPDevices", "SECoPTriggerableDevice")

    def generate_code(self) -> str:
        """Generate Python code using Jinja2 template.

        Returns:
            Generated Python code as string
        """
        template = self.jinja_env.get_template("generated_classes.py.jinja2")

        # Prepare template context
        context = {
            "imports": self.imports,
            "module_classes": self.module_classes,
            "node_classes": self.node_classes,
            "enum_classes": self._collect_all_enums(),
        }

        # Render template
        code = template.render(**context)

        # Remove unused imports with autoflake
        try:

            code = autoflake.fix_code(
                code,
                remove_all_unused_imports=True,
                remove_unused_variables=False,
                remove_duplicate_keys=True,
            )
        except Exception as e:
            if self.log:
                self.log.warning(f"Autoflake processing failed: {e}")
            else:
                print(f"Warning: Autoflake processing failed: {e}")

        # Format with Black
        try:
            code = black.format_str(code, mode=black.Mode(line_length=200))
        except Exception as e:
            if self.log:
                self.log.warning(f"Black formatting failed: {e}")
            else:
                print(f"Warning: Black formatting failed: {e}")

        return code

    def _collect_all_enums(self) -> list[EnumClass]:
        """Collect and merge enum definitions from all module classes.

        When multiple module classes have enums with the same base name but different
        members, they are merged into a single SupersetEnum containing the union of all
        members.

        Returns:
            List of deduplicated EnumClass definitions
        """
        from collections import defaultdict

        # Group enums by their base name (ModuleClass + ParamName + Enum)
        # We need to track which module classes use each enum
        enum_groups = defaultdict(list)  # base_name -> [(module_class, enum)]

        for mod_cls in self.module_classes:
            for enum in mod_cls.enums:
                # Extract base enum name by removing module class prefix
                # e.g., "MassflowController1Gastype_selectEnum" -> need module class
                # name
                enum_groups[enum.name].append((mod_cls.name, enum))

        # Process each enum group
        merged_enums = []
        for enum_name, enum_list in enum_groups.items():
            if len(enum_list) == 1:
                # Single enum definition - use StrictEnum
                _, enum = enum_list[0]

                # an already merged enum read in from a file
                if enum.base_enum_class != "StrictEnum":
                    merged_enums.append(enum)
                    continue

                enum.base_enum_class = "StrictEnum"
                merged_enums.append(enum)
            else:
                # Multiple definitions - need to check if members are identical
                member_sets = [
                    frozenset((m.name, m.value) for m in enum.members)
                    for _, enum in enum_list
                ]

                if len(set(member_sets)) == 1:
                    # All enums have identical members - use StrictEnum
                    _, enum = enum_list[0]
                    enum.base_enum_class = "StrictEnum"
                    merged_enums.append(enum)
                else:
                    # Different members - merge into SupersetEnum
                    all_members_dict = {}  # (name, value) -> EnumMember

                    for _, enum in enum_list:
                        for member in enum.members:
                            key = (member.name, member.value)
                            if key not in all_members_dict:
                                all_members_dict[key] = member

                    # Create merged enum with all unique members
                    _, base_enum = enum_list[0]
                    merged_enum = EnumClass(
                        name=enum_name,
                        members=list(all_members_dict.values()),
                        description=base_enum.description,
                        base_enum_class="SupersetEnum",
                    )
                    merged_enums.append(merged_enum)

        return merged_enums

    def write_gen_node_class_file(self):
        """Generate and write the class file to disk."""
        code = self.generate_code()

        # Determine file path
        if self.module_folder_path is None:
            filep = Path(f"{self.ModName}.py")
        else:
            filep = self.module_folder_path / f"{self.ModName}.py"

        # Write to file
        with open(filep, "w") as file:
            file.write(code)

        if self.log:
            self.log.info(f"Generated class file: {filep}")
        else:
            print(f"Generated class file: {filep}")

        # Reload the module
        if self.node_mod is not None:
            reload(self.node_mod)


def get_type_param(secop_dtype: DataType) -> str | None:
    sig_type = SECoPdtype(secop_dtype).np_datatype

    # Get the module name
    module = sig_type.__module__

    # For builtins, just return the name without module prefix
    if module == "builtins":
        return sig_type.__name__

    return sig_type.__name__


def get_type_prop(prop_value) -> str | None:
    secop_dtype: DataType = secop_dtype_obj_from_json(prop_value)
    return get_type_param(secop_dtype)
