"""Code generation for annotated ophyd device classes using Jinja2 templates."""

import inspect
import json
import re
from dataclasses import dataclass, field
from importlib import import_module, reload
from inspect import Signature
from pathlib import Path
from types import ModuleType

import autoflake
import black
from jinja2 import Environment, PackageLoader, select_autoescape
from ophyd_async.core import Signal


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
class Attribute:
    """Represents a class attribute with name and type."""

    name: str
    type: str
    type_param: str | None = (
        None  # Optional type parameter like float for SignalRW[float]
    )
    description: str | None = None  # Optional description from SECoP or docstrings
    category: str = (
        "parameter"  # "property" or "parameter" - for organizing generated code
    )


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
        self.sig_str: str = self.signature  # For backward compatibility

    @classmethod
    def from_cmd(cls, cmd_name: str, description: str, cmd_sign: Signature) -> "Method":
        """Create Method from command signature.

        Args:
            cmd_name: Name of the command
            description: Description of the command
            cmd_sign: Signature of the command

        Returns:
            Method instance
        """
        return cls(cmd_name, description, cmd_sign)


@dataclass
class ModuleClass:
    """Represents a module class to be generated."""

    name: str
    bases: list[str]
    attributes: list[Attribute] = field(default_factory=list)
    methods: list[Method] = field(default_factory=list)
    description: str = ""
    enums: list[EnumClass] = field(default_factory=list)  # Enum classes for this module


@dataclass
class NodeClass:
    """Represents a node class to be generated."""

    name: str
    bases: list[str]
    attributes: list[Attribute] = field(default_factory=list)
    description: str = ""


def get_python_type_from_signal(signal_obj: Signal) -> str | None:
    """Extract Python type from signal backend datatype.

    Args:
        signal_obj: Signal object (SignalR, SignalRW, etc.)
        debug: If True, print debug information

    Returns:
        Python type string (e.g., 'float', 'int', 'str') or None
    """
    try:

        type_obj = signal_obj.datatype

        # Get the module name
        module = type_obj.__module__

        # For builtins, just return the name without module prefix
        if module == "builtins":
            return type_obj.__name__

        return f"{module}.{type_obj.__name__}"

    except Exception as e:
        print(f"DEBUG: Exception occurred: {type(e).__name__}: {e}")
        import traceback

        traceback.print_exc()
        return None


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
        self.log = log
        self.module_folder_path: Path | None = None
        if path is not None:
            self.module_folder_path = Path(path)

        # Data structures for classes and imports
        self.imports: dict[str, set[str] | None] = {}
        self.module_classes: list[ModuleClass] = []
        self.node_classes: list[NodeClass] = []
        self.node_mod: ModuleType | None = None

        # Required imports for abstract classes
        self.add_import("abc", "abstractmethod")
        self.add_import("ophyd_async.core", "SignalR")
        self.add_import("ophyd_async.core", "SignalRW")
        self.add_import("ophyd_async.core", "StrictEnum")
        self.add_import("ophyd_async.core", "SupersetEnum")
        self.add_import("typing", "Any")
        self.add_import("numpy")

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
                import sys

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
        from ophyd_async.core import Device

        try:
            from secop_ophyd.SECoPDevices import (
                SECoPBaseDevice,
                SECoPCommunicatorDevice,
                SECoPMoveableDevice,
                SECoPNodeDevice,
                SECoPReadableDevice,
                SECoPTriggerableDevice,
                SECoPWritableDevice,
            )

            has_secop_devices = True
        except ImportError:
            has_secop_devices = False

        if self.node_mod is None:
            return

        modules = inspect.getmembers(self.node_mod)
        results = filter(lambda m: inspect.isclass(m[1]), modules)

        for class_symbol, class_obj in results:
            module = class_obj.__module__

            if module == self.ModName:
                # Parse classes defined in the generated module
                parsed = False

                # Try SECoP-specific device types first if available
                if has_secop_devices:
                    if issubclass(class_obj, SECoPNodeDevice):
                        self._parse_node_class(class_symbol, class_obj)
                        parsed = True
                    elif issubclass(
                        class_obj,
                        (
                            SECoPBaseDevice,
                            SECoPCommunicatorDevice,
                            SECoPMoveableDevice,
                            SECoPReadableDevice,
                            SECoPWritableDevice,
                            SECoPTriggerableDevice,
                        ),
                    ):
                        self._parse_module_class(class_symbol, class_obj)
                        parsed = True

                # Fall back to generic Device parsing
                if not parsed and issubclass(class_obj, Device):
                    # Determine if it's a node or module class by checking if it has
                    # attributes that are themselves Device subclasses
                    is_node = self._is_node_class(class_obj)
                    if is_node:
                        self._parse_node_class(class_symbol, class_obj)
                    else:
                        self._parse_module_class(class_symbol, class_obj)

    def _is_node_class(self, class_obj: type) -> bool:
        """Determine if a Device class is a node (contains other Device classes).

        Args:
            class_obj: The class to check

        Returns:
            True if this appears to be a node class, False if it's a module class
        """
        from ophyd_async.core import Device

        # Check annotations for Device subclass attributes
        if hasattr(class_obj, "__annotations__"):
            for attr_name, attr_type in class_obj.__annotations__.items():
                # Check if the type is a class and a Device subclass
                if inspect.isclass(attr_type) and issubclass(attr_type, Device):
                    return True
                # Handle string annotations and generic types
                type_str = str(attr_type)
                # If any annotation refers to a custom class (not Signal*), it's likely
                # a node
                if "Signal" not in type_str and not type_str.startswith("<class"):
                    # Check if it's a class defined in the same module
                    try:
                        if hasattr(class_obj, attr_name):
                            continue
                    except Exception:
                        pass
        return False

    def _parse_node_class(self, class_symbol: str, class_obj: type):
        """Parse a node class from existing module.

        Args:
            class_symbol: Name of the class
            class_obj: The class object
        """
        attrs = self._extract_attrs_from_source(inspect.getsource(class_obj))
        bases = [base.__name__ for base in class_obj.__bases__]

        # Extract description from docstring
        description = inspect.getdoc(class_obj) or ""

        # Parse attributes with type parameters
        attributes = []
        for attr_name, attr_type_str in attrs:
            # Parse type and type parameter from string like "SignalR[float]"
            if "[" in attr_type_str and "]" in attr_type_str:
                base_type = attr_type_str.split("[")[0]
                type_param = attr_type_str.split("[")[1].rstrip("]")
                attributes.append(
                    Attribute(name=attr_name, type=base_type, type_param=type_param)
                )
            else:
                attributes.append(Attribute(name=attr_name, type=attr_type_str))

        node_cls = NodeClass(
            name=class_symbol,
            bases=bases,
            attributes=attributes,
            description=description,
        )
        self.node_classes.append(node_cls)

    def _parse_module_class(self, class_symbol: str, class_obj: type):
        """Parse a module class from existing module.

        Args:
            class_symbol: Name of the class
            class_obj: The class object
        """
        # Extract attributes from source code to get proper type annotations
        source = inspect.getsource(class_obj)
        attrs = self._extract_attrs_from_source(source)

        attributes = []
        for attr_name, attr_type_str in attrs:
            # Parse type and type parameter from string like "SignalR[float]"
            if "[" in attr_type_str and "]" in attr_type_str:
                base_type = attr_type_str.split("[")[0]
                type_param = attr_type_str.split("[")[1].rstrip("]")
                attributes.append(
                    Attribute(name=attr_name, type=base_type, type_param=type_param)
                )
            else:
                attributes.append(Attribute(name=attr_name, type=attr_type_str))

        methods = []
        for method_name, method in class_obj.__dict__.items():
            if callable(method) and not method_name.startswith("__"):
                method_source = inspect.getsource(method)
                description = self._extract_method_description(method_source)
                methods.append(
                    Method.from_cmd(method_name, description, inspect.signature(method))
                )

        bases = [base.__name__ for base in class_obj.__bases__]

        # Extract description from docstring
        description = inspect.getdoc(class_obj) or ""

        mod_cls = ModuleClass(
            name=class_symbol,
            bases=bases,
            attributes=attributes,
            methods=methods,
            description=description,
        )
        self.module_classes.append(mod_cls)

    def _extract_attrs_from_source(self, source: str) -> list[tuple[str, str]]:
        """Extract attributes from class source code.

        Args:
            source: Source code of the class

        Returns:
            List of (name, type) tuples
        """
        source_list: list[str] = source.split("\n")
        # Remove first and last line
        if len(source_list) > 1:
            source_list.pop(0)
            source_list.pop()

        # Extract attributes (skip methods, decorators, docstrings)
        attrs: list[tuple[str, str]] = []
        for line in source_list:
            stripped = line.strip()
            # Skip empty lines, decorators, method definitions, and docstrings
            if (
                not stripped
                or stripped.startswith("@")
                or stripped.startswith("def ")
                or stripped.startswith('"""')
                or stripped.startswith("'''")
            ):
                continue

            # Check if it's an attribute annotation (has : but not inside parentheses)
            if ":" in stripped and "(" not in stripped.split(":", 1)[0]:
                # Remove spaces for consistent parsing
                line_no_space = line.replace(" ", "")
                parts = line_no_space.split(":", maxsplit=1)
                if len(parts) == 2:
                    attr_name = parts[0]
                    attr_type = parts[1]
                    attrs.append((attr_name, attr_type))

        return attrs

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
        attrs: list[tuple[str, str, str | None, str | None, str]],
        cmd_plans: list[Method],
        description: str = "",
        enum_classes: list[EnumClass] | None = None,
    ):
        """Add a module class to be generated.

        Args:
            module_cls: Name of the module class
            bases: Base classes
            attrs: List of attribute tuples (name, type) or (name, type, type_param)
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

        attributes = []
        for attr in attrs:
            type_param = attr[2] if attr[2] else None
            descr = attr[3] if len(attr) > 3 else None
            category = attr[4] if len(attr) > 4 else "parameter"

            attributes.append(
                Attribute(
                    name=attr[0],
                    type=attr[1],
                    type_param=type_param,
                    description=descr,
                    category=category,
                )
            )

        mod_cls = ModuleClass(
            name=module_cls,
            bases=bases,
            attributes=attributes,
            methods=cmd_plans,
            description=description,
            enums=enum_classes or [],
        )
        self.module_classes.append(mod_cls)

    def add_node_class(
        self,
        node_cls: str,
        bases: list[str],
        attrs: list[tuple[str, str] | tuple[str, str, str | None]],
        description: str = "",
    ):
        """Add a node class to be generated.

        Args:
            node_cls: Name of the node class
            bases: Base classes
            attrs: List of attribute tuples (name, type) or (name, type, type_param)
            description: Optional class description
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

        attributes = []
        for attr in attrs:
            if len(attr) == 3:
                # Clean the type_param to extract just the type name
                attributes.append(
                    Attribute(name=attr[0], type=attr[1], type_param=attr[2])
                )
            else:
                attributes.append(Attribute(name=attr[0], type=attr[1]))

        node_class = NodeClass(
            name=node_cls,
            bases=bases,
            attributes=attributes,
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
            _ = json.loads(json_data)

        # TODO: Implement parsing of SECoP JSON describe format
        # This will extract module and node information from the JSON
        # and populate self.module_classes and self.node_classes
        raise NotImplementedError(
            "Generation from JSON describe message is not yet implemented"
        )

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
            code = black.format_str(code, mode=black.Mode())
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
