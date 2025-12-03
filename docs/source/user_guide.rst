User Guide
==========

This guide covers common usage patterns and best practices for SECoP-Ophyd.

.. contents:: Table of Contents
   :local:
   :depth: 2

Device Connection
-----------------

Basic Connection
~~~~~~~~~~~~~~~~

Connect to a SECoP node using its IP address and port:

.. code-block:: python

    from ophyd_async.core import init_devices
    from secop_ophyd.SECoPDevices import SECoPNodeDevice

    with init_devices():
        device = SECoPNodeDevice('192.168.1.100:10767')

The device tree is automatically built from the node's descriptive data during the ``init_devices()`` context.

**Important:** The connection must be established within an ``init_devices()`` context manager, which handles the asynchronous connection setup.

Device Naming
~~~~~~~~~~~~~

By default, devices use the SECoP node's ``equipment_id`` property as their name. You can customize this:

.. code-block:: python

    # Default: uses equipment_id from SECoP node
    device = SECoPNodeDevice('192.168.1.100:10767')
    print(device.name)  # e.g., "CRYO-01"

    # Custom name
    device = SECoPNodeDevice('192.168.1.100:10767', name='my_cryostat')
    print(device.name)  # "my_cryostat"

    # With prefix (useful for multiple similar devices)
    device = SECoPNodeDevice(
        '192.168.1.100:10767',
        prefix='beamline_A_',
        name='cryo'
    )
    print(device.name)  # "beamline_A_cryo"

Connection Parameters
~~~~~~~~~~~~~~~~~~~~~

The :class:`~secop_ophyd.SECoPDevices.SECoPNodeDevice` constructor accepts the following parameters:

.. code-block:: python

    SECoPNodeDevice(
        sec_node_uri: str,           # Required: 'host:port' format
        prefix: str = "",            # Optional: prefix for device name
        name: str = "",              # Optional: custom device name
        loglevel: str = "INFO",      # Optional: logging level
        logdir: str | None = None    # Optional: log file directory
    )

**Parameters explained:**

- ``sec_node_uri`` (required): Connection string in ``'host:port'`` format

  - ``'localhost:10767'`` - Local SECoP node
  - ``'192.168.1.100:10767'`` - Network device
  - ``'device.example.com:10767'`` - Domain name

- ``prefix`` (optional): String prefix added to the device name. Useful when managing multiple devices.

- ``name`` (optional): Custom name for the device. If not provided, the device uses the SECoP node's ``equipment_id`` property.

- ``loglevel`` (optional): Control logging verbosity. Options: ``"DEBUG"``, ``"INFO"``, ``"WARNING"``, ``"ERROR"``, ``"CRITICAL"``

- ``logdir`` (optional): Directory path for storing log files. If ``None``, logs only to console.

**Examples:**

.. code-block:: python

    # Minimal connection
    device = SECoPNodeDevice('192.168.1.100:10767')

    # With custom name
    device = SECoPNodeDevice('192.168.1.100:10767', name='cryo_controller')

    # With prefix and logging to file
    device = SECoPNodeDevice(
        '192.168.1.100:10767',
        prefix='lab1_',
        loglevel='DEBUG',
        logdir='/var/log/secop'
    )

Logging Configuration
~~~~~~~~~~~~~~~~~~~~~

SECoP-Ophyd provides default logging for debugging and monitoring. Logs are written to rotating hourly log files (48 hours retention).

**Default Logging:**

.. code-block:: python

    # Logs written to .secop-ophyd/secop-ophyd.log (default)
    device = SECoPNodeDevice('localhost:10767')

    # Change log level
    device = SECoPNodeDevice('localhost:10767', loglevel="DEBUG")

**Custom Log Directory:**

.. code-block:: python

    # Write logs to custom directory
    device = SECoPNodeDevice(
        'localhost:10767',
        loglevel="DEBUG",
        logdir="/var/log/secop"
    )

    # Logs written to: /var/log/secop/secop-ophyd.log

**Log File Behavior:**

- Default location: ``.secop-ophyd/`` in current working directory
- Log files rotate hourly with 48-hour retention
- Multiple devices can share the same log file
- Logs use UTC timestamps for consistency

**Log Levels:**

- ``DEBUG``: Detailed protocol messages, useful for troubleshooting
- ``INFO``: General operational information (default)
- ``WARNING``: Warning messages only
- ``ERROR``: Error messages only
- ``CRITICAL``: Critical errors only

Device Structure
----------------

Understanding the Hierarchy
~~~~~~~~~~~~~~~~~~~~~~~~~~~

A SECoP-Ophyd device follows this structure:

.. code-block:: text

    SECoPNodeDevice (Node)
    ├── module_1 (Module)
    │   ├── parameter_a (Signal)
    │   ├── parameter_b (Signal)
    │   └── command_x (Method)
    └── module_2 (Module)
        ├── parameter_c (Signal)
        └── command_y (Method)

Example with a temperature controller:

.. code-block:: text

    device
    ├── temperature_controller
    │   ├── value          # Current temperature (readable)
    │   ├── target         # Target temperature (readable/settable)
    │   ├── ramp           # Ramp rate (readable/settable)
    │   ├── status         # Device status
    │   └── reset()        # Reset command
    └── pressure_sensor
        └── value          # Current pressure (readable)

Exploring Device Structure
~~~~~~~~~~~~~~~~~~~~~~~~~~~

Without generated class files, explore the device interactively:

.. code-block:: python

    # List all modules
    print(device.component_names)

    # Access a module
    temp_module = getattr(device, 'temperature_controller')

    # List module's parameters
    print(temp_module.component_names)

Working with Signals
--------------------

Reading Signals
~~~~~~~~~~~~~~~

Read single values:

.. code-block:: python

    from bluesky.plan_stubs import rd

    def read_temperature():
        value = yield from rd(device.temperature.value)
        print(f"Temperature: {value}")
        return value

    RE(read_temperature())


Setting Signals
~~~~~~~~~~~~~~~

Use ``abs_set`` for absolute positioning:

.. code-block:: python

    from bluesky.plan_stubs import mv, abs_set

    # Simple set
    RE(abs_set(device.temperature.target, 300))

    # Set with wait=False (non-blocking)
    RE(abs_set(device.temperature.target, 300, wait=False))

    # Set multiple parameters
    RE(mv(
        device.temp1.target, 300,
        device.temp2.target, 350,
        device.pressure.target, 1000
    ))

Using SECoP Commands
--------------------

Command Basics
~~~~~~~~~~~~~~

SECoP commands are methods that return Bluesky plan generators:

.. code-block:: python

    # Call a command without arguments
    RE(device.module.reset())

    # Call a command with arguments
    RE(device.module.configure(mode='auto', setpoint=100))

All commands accept a ``wait_for_idle`` parameter:

.. code-block:: python

    # Return immediately (default)
    RE(device.module.command(arg=value, wait_for_idle=False))

    # Wait for device to return to IDLE state
    RE(device.module.command(arg=value, wait_for_idle=True))

Command Arguments
~~~~~~~~~~~~~~~~~

Commands can accept various data types:

.. code-block:: python

    # Simple types
    RE(device.motor.move(position=100.5))

    # Structured data (dicts)
    RE(device.controller.configure(
        arg={'param1': 'value', 'param2': 123, 'flag': True}
    ))

    # Arrays
    RE(device.controller.set_profile(points=[0, 10, 20, 30]))

Return Values
~~~~~~~~~~~~~

Capture command return values:

.. code-block:: python

    def get_status():
        result = yield from device.module.get_info()
        print(f"Device info: {result}")
        return result

    info = RE(get_status())

Status and Busy States
~~~~~~~~~~~~~~~~~~~~~~~

SECoP devices communicate their state through status codes. When a command
or parameter change triggers a long operation, the device enters a BUSY state:

.. code-block:: python

    def move_and_wait():
        # Start movement (device goes BUSY)
        yield from device.motor.move(position=100, wait_for_idle=True)
        # This line executes only after device returns to IDLE
        print("Movement complete!")

    RE(move_and_wait())


Class File Generation
---------------------

Why Generate Class Files?
~~~~~~~~~~~~~~~~~~~~~~~~~~

While SECoP-Ophyd works without them, class files provide:

- **IDE autocompletion** - See available modules and parameters
- **Type hints** - Catch errors before runtime
- **Documentation** - Understand device structure

Generating Class Files
~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

    # Connect to device
    with init_devices():
        device = SECoPNodeDevice('localhost:10800')

    # Generate class file in current directory
    device.class_from_instance()

    # Or specify output directory
    device.class_from_instance('/path/to/output')

Using Generated Classes
~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

    # Import generated classes
    from genNodeClass import MyDevice

    # Type-cast for IDE support
    device: MyDevice = device

    # Now you have autocompletion!
    device.temperature.  # IDE shows: value, target, ramp, status, etc.

.. warning::

   Regenerate class files whenever the static metadata of a SECoP node changes!
   THis can happen on every new connection to a SEC node.



Best Practices
--------------

Connection Management
~~~~~~~~~~~~~~~~~~~~~

Always use ``init_devices()`` context manager:

.. code-block:: python

    # Good
    with init_devices():
        device = SECoPNodeDevice('localhost:10800')
    # Device is properly connected and cleaned up

    # Bad - no cleanup
    device = SECoPNodeDevice('localhost:10800')

Avoid Hardcoded URIs
~~~~~~~~~~~~~~~~~~~~

Use configuration files or environment variables:

.. code-block:: python

    import os

    node_uri = os.environ.get('SECOP_NODE_URI', 'localhost:10800')
    device = SECoPNodeDevice(node_uri)

Wait Strategies
~~~~~~~~~~~~~~~

Choose appropriate wait strategies for commands:

.. code-block:: python

    # Quick commands - no wait needed
    RE(device.module.get_status(wait_for_idle=False))

    # Long operations - wait for completion
    RE(device.motor.move_to(100, wait_for_idle=True))



Advanced Topics
---------------

Mixed Device Ecosystems
~~~~~~~~~~~~~~~~~~~~~~~

Use SECoP devices alongside EPICS, Tango, or other protocols:

.. code-block:: python

    from ophyd import EpicsMotor
    from ophyd_async.tango import TangoDevice

    # Mix different device types
    with init_devices():
        secop_temp = SECoPNodeDevice('localhost:10800')
        epics_motor = EpicsMotor('XF:31IDA-OP{Tbl-Ax:X1}Mtr', name='motor')
        tango_detector = TangoDevice('sys/tg_test/1')

    # Use together in plans
    RE(scan([tango_detector], epics_motor, 0, 10, 11))
    RE(mv(secop_temp.target, 300))
