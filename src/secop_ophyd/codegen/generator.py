"""Code generation for annotated ophyd device classes using Jinja2 templates."""

import json
from collections import defaultdict
from logging import Logger
from pathlib import Path

import autoflake
import black
from jinja2 import Environment, PackageLoader, select_autoescape
from ophyd_async.core import SignalR

from secop_ophyd.codegen.model import (
    ChannelAttribute,
    CommandAttribute,
    EnumClass,
    Method,
    ModuleAttribute,
    ModuleClass,
    NodeClass,
    ParameterAttribute,
    PropertyAttribute,
    TypedDictClass,
)
from secop_ophyd.codegen.module_class import (
    ModuleClassBuilder,
    module_class_name,
    normalize_description,
    split_accessibles,
)
from secop_ophyd.datatypes.properties import get_type_prop
from secop_ophyd.devices.base import PropertyType
from secop_ophyd.devices.connector import acquisition_channels
from secop_ophyd.devices.node import class_from_interface


def _class_description(module_descriptions: list[tuple[str, str]]) -> str:
    """Docstring of a module class: the description of its module, or -- for
    a class of several modules with different descriptions -- the description
    of each module."""
    descriptions = {description for _, description in module_descriptions}
    if len(descriptions) == 1:
        return module_descriptions[0][1]
    lines = ["Class of the modules:", ""]
    for module, description in module_descriptions:
        first, *rest = (description or "").split("\n")
        lines.append(f"- {module}: {first}".rstrip())
        lines.extend(f"  {line}".rstrip() for line in rest)
    return "\n".join(lines)


class GenNodeCode:
    """Generates annotated Python classes for a single SECoP node.

    Each instance describes exactly one SEC node. ``write_gen_node_class_file``
    writes it to its own file, named after the generated node class, inside
    the configured output directory -- overwriting any previous file of that
    name.

    The generated code uses Jinja2 templates and is formatted with Black.
    """

    default_output_dir: str = "secop_ophyd_devs"

    def __init__(self, path: str | None = None, log=None):
        """Initialize the code generator.

        Args:
            path: Optional path to the output folder. Defaults to
                ``secop_ophyd_devs`` relative to the current working
                directory when not given.
            log: Optional logger instance
        """
        self.log: Logger | None = log
        self.module_folder_path: Path = Path(path or self.default_output_dir)

        # Data structures for classes and imports
        self.imports: dict[str, set[str] | None] = {}
        self.module_classes: list[ModuleClass] = []
        self.node_classes: list[NodeClass] = []
        self.enum_classes: list[EnumClass] = []
        self.inline_comment_threshold: int = 120
        self.comment_wrap_width: int = 100

        # Required imports for generated classes
        self.add_import("typing", "Annotated as A")
        self.add_import("ophyd_async.core", "SignalR")
        self.add_import("ophyd_async.core", "SignalRW")
        self.add_import("ophyd_async.core", "SignalW")
        self.add_import("ophyd_async.core", "Command")
        self.add_import("ophyd_async.core", "TriggerableCommand")
        self.add_import("ophyd_async.core", "StandardReadableFormat as Format")
        self.add_import("ophyd_async.core", "StrictEnum")
        self.add_import("ophyd_async.core", "SupersetEnum")
        self.add_import("typing", "Any")
        self.add_import("typing", "NotRequired")
        self.add_import("typing", "TypedDict")
        self.add_import("collections.abc", "Sequence")
        self.add_import("numpy", "ndarray")
        self.add_import("secop_ophyd.devices", "ParameterType as ParamT")
        self.add_import("secop_ophyd.devices", "ParameterMemberType as ParamMemberT")
        self.add_import("secop_ophyd.devices", "PropertyType as PropT")
        # Add necessary Device imports
        self.add_import("secop_ophyd.devices", "SECoPDevice")
        self.add_import("secop_ophyd.devices", "SECoPCommunicatorDevice")
        self.add_import("secop_ophyd.devices", "SECoPReadableDevice")
        self.add_import("secop_ophyd.devices", "SECoPAcquisitionDevice")
        self.add_import("secop_ophyd.devices", "SECoPAcquisitionControllerDevice")
        self.add_import("secop_ophyd.devices", "SECoPAcquisitionChannelDevice")
        self.add_import("secop_ophyd.devices", "AcquisitionChannelType as ChannelT")
        self.add_import("secop_ophyd.devices", "SECoPWritableDevice")
        self.add_import("secop_ophyd.devices", "SECoPMoveableDevice")
        self.add_import("secop_ophyd.devices", "SECoPCompositeMoveableDevice")
        self.add_import("secop_ophyd.devices", "SECoPNodeDevice")

        # Setup Jinja2 environment
        self.jinja_env = Environment(
            loader=PackageLoader("secop_ophyd.codegen", "templates"),
            autoescape=select_autoescape(),
            trim_blocks=False,
            lstrip_blocks=False,
            keep_trailing_newline=True,
        )

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
        commands: list[CommandAttribute] | None = None,
        typed_dicts: list[TypedDictClass] | None = None,
        channels: list[ChannelAttribute] | None = None,
        module_name: str | None = None,
    ):
        """Add a module class to be generated.

        Args:
            module_cls: Name of the module class
            bases: Base classes
            parameters: List of parameter attributes
            properties: List of property attributes
            cmd_plans: List of method definitions
            description: Optional class description
            commands: List of command annotations (Command/TriggerableCommand)
        """
        # Check if this module class was already added, e.g. two module
        # instances within the same node share this implementation class
        existing_class = next(
            (cls for cls in self.module_classes if cls.name == module_cls), None
        )
        if existing_class:
            if module_name is not None:
                existing_class.module_descriptions.append((module_name, description))
                existing_class.description = _class_description(
                    existing_class.module_descriptions
                )

            # Optional properties (e.g. 'meaning', 'group') may be set on only
            # some instances: annotate only those that all instances have,
            # auto_fill_signals adds the rest at connect
            names = {prop.name for prop in properties}
            existing_class.properties = [
                prop for prop in existing_class.properties if prop.name in names
            ]

            # merge enums if provided
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
            commands=commands or [],
            description=description,
            enums=enum_classes or [],
            typed_dicts=typed_dicts or [],
            channels=channels or [],
            module_descriptions=(
                [] if module_name is None else [(module_name, description)]
            ),
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
        node_class = NodeClass(
            name=node_cls,
            bases=bases,
            properties=properties,
            modules=modules,
            description=description,
        )
        self.node_classes.append(node_class)

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

        # the channels of acquisition controllers are child devices of their
        # controller, not module devices of the node: generate their classes
        # first, the controller classes refer to them
        channel_modules = {
            channel
            for moddescr in modules.values()
            for channel in acquisition_channels(moddescr).values()
        }
        module_class_names: dict[str, str] = {}

        node_module_attrs: list[ModuleAttribute] = []
        for modname, moddescr in sorted(
            modules.items(), key=lambda item: item[0] not in channel_modules
        ):
            module_class = self._add_module(modname, moddescr, module_class_names)

            if modname not in channel_modules:
                node_module_attrs.append(
                    ModuleAttribute(name=modname, type=module_class)
                )

        node_property_attrs = [
            PropertyAttribute(
                name=prop_name,
                type=SignalR.__name__,
                type_param=get_type_prop(property_value),
                path_annotation=str(PropertyType()),
            )
            for prop_name, property_value in node_properties.items()
        ]

        equipment_id: str = node_properties["equipment_id"]

        # format node class accordingly
        node_class_name = equipment_id.replace(".", "_").replace("-", "_").capitalize()

        self.add_node_class(
            node_cls=node_class_name,
            bases=["SECoPNodeDevice"],
            modules=node_module_attrs,
            properties=node_property_attrs,
            description=node_properties.get("description", ""),
        )

    def _add_module(
        self, modname: str, moddescr: dict, module_class_names: dict[str, str]
    ) -> str:
        """Add the class of one module and return its name.
        ``module_class_names`` are the classes of the modules added so far,
        to which this one is added: a controller class refers to the classes
        of its channels."""
        parameters, commands = split_accessibles(moddescr)
        properties = {k: v for k, v in moddescr.items() if k != "accessibles"}

        # the class is derived from the device class of the module's highest
        # SECoP interface class
        builder = ModuleClassBuilder(
            name=module_class_name(modname, properties),
            device_class=class_from_interface(properties, parameters),
        )
        module_class_names[modname] = builder.name

        # commands first: this is the order of their enum classes
        mod_commands = builder.commands(commands)
        mod_parameters = builder.parameters(parameters)
        mod_properties = builder.properties(properties)

        taken = (
            {param.name for param in mod_parameters}
            | {prop.name for prop in mod_properties}
            | {cmd.name for cmd in mod_commands}
        )
        mod_channels = builder.channels(
            acquisition_channels(properties), taken, module_class_names
        )

        self.add_mod_class(
            module_cls=builder.name,
            bases=builder.bases(),
            parameters=mod_parameters,
            properties=mod_properties,
            cmd_plans=[],
            description=normalize_description(properties.get("description", "")),
            enum_classes=builder.enums,
            commands=mod_commands,
            typed_dicts=builder.typed_dicts,
            module_name=modname,
            channels=mod_channels,
        )
        return builder.name

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
            "typed_dict_classes": self._collect_typed_dicts(),
            "inline_comment_threshold": self.inline_comment_threshold,
            "comment_wrap_width": self.comment_wrap_width,
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

    def _collect_typed_dicts(self) -> list[TypedDictClass]:
        """TypedDict classes of all module classes, once per name."""
        typed_dicts: dict[str, TypedDictClass] = {}
        for mod_cls in self.module_classes:
            for typed_dict in mod_cls.typed_dicts:
                typed_dicts.setdefault(typed_dict.name, typed_dict)
        return list(typed_dicts.values())

    def _collect_all_enums(self) -> list[EnumClass]:
        """Collect and merge enum definitions from all module classes.

        When multiple module classes have enums with the same base name but different
        members, they are merged into a single SupersetEnum containing the union of all
        members.

        Returns:
            List of deduplicated EnumClass definitions
        """
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
        """Generate and write one file for the generated SEC node, named after
        its node class, overwriting any existing file of that name."""
        if not self.node_classes:
            raise ValueError(
                "No node class has been added yet (call from_json_describe() "
                "or add_node_class() first) -- cannot determine output filename."
            )

        code = self.generate_code()

        self.module_folder_path.mkdir(parents=True, exist_ok=True)
        filep = self.module_folder_path / f"{self.node_classes[0].name}.py"

        with open(filep, "w") as file:
            file.write(code)

        self._write_package_init()

        if self.log:
            self.log.info(f"Generated class file: {filep}")
        else:
            print(f"Generated class file: {filep}")

    def _write_package_init(self):
        """(Re)generate __init__.py so the output directory is an importable
        package that re-exports every generated node class currently on disk,
        e.g. ``from <output_dir> import <NodeClassName>``."""
        node_names = sorted(
            p.stem
            for p in self.module_folder_path.glob("*.py")
            if p.stem != "__init__" and p.stem.isidentifier()
        )
        lines = [f"from .{name} import {name}\n" for name in node_names]

        init_file = self.module_folder_path / "__init__.py"
        with open(init_file, "w") as file:
            file.writelines(lines)
