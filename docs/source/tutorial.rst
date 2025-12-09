Tutorial: Gas Dosing Demo
==========================

This tutorial demonstrates the full capabilities of SECoP-Ophyd integration using a simulated
gas dosing system. We'll walk through connecting to SECoP nodes, generating device classes,
and building complex experimental plans.

Demo Setup
----------

The demo uses Docker containers running SECoP nodes for a gas dosing system and reactor cell.
If you're using the `secop-sim <https://codebase.helmholtz.cloud/rock-it-secop/secop-sim>`_
demo repository, you can start the simulation with:

.. code-block:: bash

    make sim      # Start Docker containers with SEC nodes
    make frappy   # Start containers and Frappy GUI

The demo creates two SECoP nodes:

- **gas_dosing** on port 10801 - Controls mass flow controllers for different gases
- **reactor_cell** on port 10802 - Controls temperature regulation and monitoring

Setting Up the Run Engine
--------------------------

First, we set up the Bluesky Run Engine with standard callbacks:

.. code-block:: python

    from bluesky import RunEngine
    import bluesky.plan_stubs as bps
    from bluesky.plans import scan
    from bluesky.preprocessors import run_decorator
    from bluesky.callbacks.best_effort import BestEffortCallback
    from bluesky.utils import ProgressBarManager
    from bluesky.log import config_bluesky_logging

    from ophyd_async.core import init_devices
    from secop_ophyd.SECoPDevices import SECoPNodeDevice

    # Configure logging
    config_bluesky_logging(level='WARNING')

    # Create Run Engine
    RE = RunEngine({}, call_returns_result=True)

    # Add Best Effort Callback for live plotting and table display
    bec = BestEffortCallback()
    RE.subscribe(bec)

    # Add progress bar for long-running plans
    RE.waiting_hook = ProgressBarManager()
    RE.ignore_callback_exceptions = False

    # Add metadata to all runs
    RE.md["investigation_id"] = "demo_experiment_001"

Connecting to SECoP Nodes
--------------------------

The :class:`~secop_ophyd.SECoPDevices.SECoPNodeDevice` automatically builds the device tree
from the node's description upon connection:

.. code-block:: python

    with init_devices():
        gas_dosing = SECoPNodeDevice('localhost:10801', loglevel="DEBUG")
        reactor_cell = SECoPNodeDevice('localhost:10802', loglevel="INFO")

**Log Levels:**

- ``"DEBUG"``: Logs all messages sent/received by the Frappy client
- ``"INFO"``: Standard operational messages (default)
- ``"WARNING"``: Only warnings and errors
- ``"ERROR"``: Only errors

The ``init_devices()`` context manager is part of ophyd-async and ensures proper
connection and cleanup. Learn more about `device connection strategies
<https://blueskyproject.io/ophyd-async/main/explanations/device-connection-strategies.html>`_.

Generating Class Files for Type Hints
--------------------------------------

While SECoP-Ophyd devices work immediately after connection, generating class files provides:

- **Type hints** for IDE autocompletion
- **Documentation** of device structure
- **Better development experience** when writing plans

Generate class files with:

.. code-block:: python

    gas_dosing.class_from_instance()
    reactor_cell.class_from_instance()

This creates ``genNodeClass.py`` in your current working directory. You can specify a custom path:

.. code-block:: python

    gas_dosing.class_from_instance('/path/to/output/directory')

.. warning::

    Regenerate class files whenever the SECoP node structure changes (e.g., after
    firmware updates or configuration modifications).

Using the Generated Classes
----------------------------

Import the generated classes and type-cast your devices:

.. code-block:: python

    from genNodeClass import *

    # Type cast for IDE support
    gas_dosing: Gas_dosing = gas_dosing
    reactor_cell: Reactor_cell = reactor_cell

Now your IDE will provide autocompletion and type checking for all device attributes.

Basic Device Usage
------------------

SECoP-Ophyd devices work like any other ophyd-async device in Bluesky plans:

Setting Parameters
~~~~~~~~~~~~~~~~~~

.. code-block:: python

    # Set the temperature ramp rate to 50 K/min
    RE(bps.abs_set(reactor_cell.temperature_reg.ramp, 50))

Scanning
~~~~~~~~

.. code-block:: python

    # Scan sample temperature from 25 to 100°C in 10 steps
    RE(scan([reactor_cell.temperature_sam],
            reactor_cell.temperature_reg,
            25, 100, 10))

Reading Values
~~~~~~~~~~~~~~

.. code-block:: python

    from bluesky.plan_stubs import rd

    def read_temperature():
        temp = yield from rd(reactor_cell.temperature_sam.value)
        print(f"Current temperature: {temp}°C")
        return temp

    RE(read_temperature())

SECoP Commands as Bluesky Plans
--------------------------------

SECoP commands are special operations implemented on the SEC node. In SECoP-Ophyd,
they're exposed as instance methods that return Bluesky plans.

Understanding Command Signatures
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Commands can have arguments and return values. From the generated ``genNodeClass.py``,
you can see the signature:

.. code-block:: python

    @abstractmethod
    def test_cmd(self, arg: dict[str, Any], wait_for_idle: bool = False) -> int:
        """Testing with ophyd secop integration

        argument: StructOf(name=StringType(), id=IntRange(0, 1000), sort=BoolType())
        result: IntRange()
        """

This command:

- Takes a dict argument with 'name' (str), 'id' (int 0-1000), and 'sort' (bool)
- Returns an integer
- Optionally waits for the device to return to IDLE state

Using Commands
~~~~~~~~~~~~~~

.. code-block:: python

    def run_command_example():
        # Command with wait_for_idle=False (returns immediately)
        result = yield from gas_dosing.massflow_contr1.test_cmd(
            arg={'name': 'test', 'id': 245, 'sort': True},
            wait_for_idle=False
        )
        print(f"Command returned: {result}")

    RE(run_command_example())

The ``wait_for_idle`` Parameter
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

SECoP commands should return quickly. If an operation takes time, the module
communicates this via a BUSY state. Use ``wait_for_idle=True`` to wait for
completion:

.. code-block:: python

    def wait_for_completion():
        # This will wait until the device returns to IDLE
        result = yield from gas_dosing.massflow_contr1.test_cmd(
            arg={'name': 'test', 'id': 245, 'sort': True},
            wait_for_idle=True
        )
        print("Operation completed!")

    RE(wait_for_completion())

Error Handling
~~~~~~~~~~~~~~

Commands validate their arguments and raise errors for invalid inputs:

.. code-block:: python

    try:
        # This will fail due to invalid name
        RE(gas_dosing.massflow_contr1.test_cmd(
            arg={'name': 'bad_name', 'id': 245, 'sort': True},
            wait_for_idle=True
        ))
    except Exception as e:
        print(f"Command failed: {e}")

Advanced Example: Catalysis Experiment
---------------------------------------

This example demonstrates a fictional catalysis experiment with temperature ramping
and gas switching.

Defining Gas Mixtures
~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

    # Gas flows in ml/min for each mass flow controller
    # Format: (MFC1, MFC2, MFC3) = (H2, He, CO)
    gas_flows = {
        "N2": (30, 0, 0),
        "H2": (0, 15, 0),
        "CO": (0, 0, 5),
        "Off": (0, 0, 0)
    }

Gas Setting Plan
~~~~~~~~~~~~~~~~

.. code-block:: python

    def set_gas(mfc_tuple):
        """Set mass flow controllers to specified values."""
        yield from bps.mv(
            gas_dosing.massflow_contr1, mfc_tuple[0],
            gas_dosing.massflow_contr2, mfc_tuple[1],
            gas_dosing.massflow_contr3, mfc_tuple[2],
            group='mfc_setting'
        )
        yield from bps.wait(group='mfc_setting', timeout=10)
        print(f"Gas set to {mfc_tuple}")

Temperature Ramping with Data Collection
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

    def ramp(gas_sel, temp_setpoint, temp_ramp, interval=10):
        """
        Ramp to target temperature while continuously acquiring data.

        Parameters
        ----------
        gas_sel : str
            Gas selection ('N2', 'H2', 'CO', 'Off')
        temp_setpoint : float
            Target temperature in Kelvin
        temp_ramp : float
            Ramp rate in K/min
        interval : int
            Seconds between data acquisitions
        """
        # Set ramp rate
        yield from bps.abs_set(reactor_cell.temperature_reg.ramp, float(temp_ramp), wait=True)

        # Set gas atmosphere
        print(f"Setting gas to {gas_sel}")
        yield from set_gas(gas_flows[gas_sel])

        # Start temperature change
        print(f"Ramping to {temp_setpoint} K at {temp_ramp} K/min")
        from bluesky.utils import Msg
        complete_status = yield Msg('set', reactor_cell.temperature_reg, temp_setpoint)

        # Acquire data while ramping
        while not complete_status.done:
            yield from bps.one_shot([
                reactor_cell.temperature_reg,
                reactor_cell.temperature_sam
            ])
            yield from bps.checkpoint()
            yield from bps.sleep(interval)

        # Confirm target reached
        temp = yield from rd(reactor_cell.temperature_reg.value)
        print(f"Temperature achieved: {temp} K")

Dwell Plan
~~~~~~~~~~

.. code-block:: python

    def dwell(gas_sel, dwell_time, interval=10):
        """
        Hold at current temperature with specified gas for given time.

        Parameters
        ----------
        gas_sel : str
            Gas selection ('N2', 'H2', 'CO', 'Off')
        dwell_time : int
            Dwell time in seconds
        interval : int
            Seconds between data acquisitions
        """
        from ophyd.status import Status

        # Set gas
        print(f"Setting gas to {gas_sel}")
        yield from set_gas(gas_flows[gas_sel])

        # Dwell with continuous acquisition
        dwell_status = Status(timeout=dwell_time)
        while not dwell_status.done:
            yield from bps.one_shot([
                reactor_cell.temperature_reg,
                reactor_cell.temperature_sam
            ])
            yield from bps.checkpoint()
            yield from bps.sleep(interval)

Complete Catalysis Experiment
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

    def catalysis_experiment():
        """Complete catalysis experiment with multiple ramps and dwells."""

        md = {
            'sample': 'sample_5',
            'operator': 'HZB',
            'experiment_type': 'catalysis'
        }

        @run_decorator(md=md)
        def inner_plan():
            # N2 purge at low temperature
            yield from ramp(gas_sel='N2', temp_setpoint=308.15, temp_ramp=60)
            yield from dwell(gas_sel='N2', dwell_time=60)

            # H2 reduction at elevated temperature
            yield from ramp(gas_sel='H2', temp_setpoint=398.15, temp_ramp=60)
            yield from dwell(gas_sel='H2', dwell_time=60)

            # CO exposure while cooling
            yield from ramp(gas_sel='CO', temp_setpoint=308.15, temp_ramp=60)
            yield from dwell(gas_sel='Off', dwell_time=60)

        return (yield from inner_plan())

    # Execute the experiment
    RE(catalysis_experiment())
