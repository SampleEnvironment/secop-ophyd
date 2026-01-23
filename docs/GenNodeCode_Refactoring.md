# GenNodeCode Refactoring Documentation

## Overview

The `GenNodeCode` class has been refactored to use **Jinja2 templates** and **Black formatting** for generating annotated ophyd device classes. This provides a more maintainable and extensible code generation system.

## Key Improvements

### 1. Template-Based Code Generation
- Uses Jinja2 templates for cleaner separation of code structure and generation logic
- Template file: `src/secop_ophyd/templates/generated_classes.py.jinja2`
- Makes it easy to modify the structure of generated classes

### 2. Black Formatting
- All generated code is automatically formatted with Black
- Ensures consistent, PEP 8-compliant code style
- Makes generated code more readable

### 3. Dataclass-Based Architecture
New dataclasses for better type safety and clarity:
- `Attribute`: Represents class attributes (name + type)
- `Method`: Represents class methods (name + signature + description)
- `ModuleClass`: Represents a module class to be generated
- `NodeClass`: Represents a node class to be generated

### 4. Dual Generation Modes

The refactored `GenNodeCode` supports two ways of generating classes:

#### Mode 1: Device Introspection (Existing Functionality)
```python
# Generate classes from a fully instantiated SECoP device
node = SECoPNodeDevice(...)
await node.connect()
node.class_from_instance(path_to_module="my_modules")
```

This works exactly as before - introspecting the device at runtime.

#### Mode 2: From JSON Describe (Future Feature)
```python
# Generate classes from SECoP JSON describe message
gen_code = GenNodeCode()
gen_code.from_json_describe(json_describe_message)
gen_code.write_gen_node_class_file()
```

This will allow generating classes without needing a running device (planned feature).

## Usage Examples

### Basic Usage (Programmatic)

```python
from secop_ophyd.GenNodeCode import GenNodeCode, Method, Attribute
from inspect import signature

# Create generator
gen_code = GenNodeCode(path="my_modules")

# Add imports
gen_code.add_import("ophyd_async.core", "Device")
gen_code.add_import("ophyd_async.core", "SignalR")

# Define a method
def my_command(self, value: float) -> str:
    """Execute a command with a value"""
    pass

method = Method(
    cmd_name="execute_command",
    description="Execute a command with a value",
    cmd_sign=signature(my_command)
)

# Add a module class with type parameters
gen_code.add_mod_class(
    module_cls="MyModule",
    bases=["Device", "ABC"],
    attrs=[
        ("temperature", "SignalR", "float"),  # With type parameter
        ("setpoint", "SignalRW", "float"),    # With type parameter
        ("status", "SignalR", "str"),         # With type parameter
    ],
    cmd_plans=[method],
    description="Temperature control module"
)

# Add a node class
gen_code.add_node_class(
    node_cls="MyNode",
    bases=["SECoPNodeDevice", "ABC"],
    attrs=[("temp_module", "MyModule")],
    description="Main node device"
)

# Generate and write
gen_code.write_gen_node_class_file()
```

### Using with Existing Devices

```python
# This is the standard way - works as before
from secop_ophyd.SECoPDevices import SECoPNodeDevice

node = SECoPNodeDevice(uri="tcp://localhost:10767", node_name="cryo")
await node.connect()

# Generate annotated classes for IDE support
# Type parameters are automatically extracted from signal datatypes!
node.class_from_instance(path_to_module="my_modules")
```

## Generated Code Example

The template generates code with type parameters like this:

```python
from abc import ABC, abstractmethod
from ophyd_async.core import Device, SignalR, SignalRW


class MyModule(ABC, Device):
    """Temperature control module"""
    temperature: SignalR[float]
    setpoint: SignalRW[float]
    status: SignalR[str]

    @abstractmethod
    def execute_command(self, value: float) -> str:
        """Execute a command with a value"""


class MyNode(ABC, SECoPNodeDevice):
    """Main node device"""
    temp_module: MyModule
```

## Backward Compatibility

The refactored implementation maintains full backward compatibility:

- All existing methods work as before
- `Method` class still supports the old `__str__()` method
- `dimport`, `dmodule`, `dnode` attributes still exist (they reference the new structures)
- Old methods like `_write_imports_string()` are kept (but do nothing)

## Template Customization

To customize the generated code, edit the template file:
```
src/secop_ophyd/templates/generated_classes.py.jinja2
```

The template has access to:
- `imports`: Dictionary of module -> set of classes
- `module_classes`: List of ModuleClass objects
- `node_classes`: List of NodeClass objects

## Future Development: JSON Describe Support

The placeholder for JSON describe support is in the `from_json_describe()` method:

```python
def from_json_describe(self, json_data: str | dict):
    """Generate classes from a SECoP JSON describe message."""
    # Parse JSON if string
    if isinstance(json_data, str):
        _ = json.loads(json_data)

    # TODO: Parse the SECoP describe format
    # Extract modules, parameters, commands, etc.
    # Populate self.module_classes and self.node_classes
    raise NotImplementedError("Not yet implemented")
```

To implement this, you'll need to:
1. Parse the SECoP JSON describe structure
2. Extract module information (name, properties, commands)
3. Create ModuleClass and NodeClass instances
4. Map SECoP types to Python/ophyd types

## Testing

Run the tests:
```bash
pytest tests/test_gencode_refactor.py  # New unit tests
pytest tests/test_classgen.py          # Existing integration tests
```

## Dependencies

New dependency added to `pyproject.toml`:
- `jinja2`: Template engine for code generation
- `black`: Already in dev dependencies, now used for formatting

## Migration Notes

No migration needed! The refactored code is a drop-in replacement. Existing code will continue to work without changes.

If you want to take advantage of new features:
1. Install jinja2: `pip install jinja2`
2. Use the new dataclasses for more type-safe code generation
3. Customize the template for project-specific needs
