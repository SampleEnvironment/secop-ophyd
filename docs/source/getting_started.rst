Getting Started
===============

Installation
------------

Install secop-ophyd using pip:

.. code-block:: bash

    pip install secop-ophyd

For development, clone the repository and install with development dependencies:

.. code-block:: bash

    git clone https://github.com/SampleEnvironment/secop-ophyd.git
    cd secop-ophyd
    pip install -e ".[dev]"

Prerequisites
-------------

SECoP-Ophyd integrates SECoP (Sample Environment Communication Protocol) devices with the Bluesky
experiment orchestration framework. Before using this package, you should have:

- A SECoP node (hardware device or simulation) accessible over the network
- Basic familiarity with `Bluesky <https://blueskyproject.io/>`_ and `Ophyd-async <https://blueskyproject.io/ophyd-async/>`_
- Python 3.10 or later

Quick Start
-----------

Here's a minimal example to get you started:

.. code-block:: python

    from bluesky import RunEngine
    from ophyd_async.core import init_devices
    from secop_ophyd.SECoPDevices import SECoPNodeDevice

    # Create a run engine
    RE = RunEngine({})

    # Connect to a SECoP node at localhost:10800
    with init_devices():
        node = SECoPNodeDevice('localhost:10800')

    # The device tree is now automatically built from the node description

Key Concepts
------------

SECoP Nodes and Modules
~~~~~~~~~~~~~~~~~~~~~~~

A **SECoP node** represents a complete hardware device or service. Each node contains one or more
**modules**, which are individual functional units (e.g., a temperature controller, a pressure sensor).

SECoPNodeDevice
~~~~~~~~~~~~~~~

The :class:`~secop_ophyd.SECoPDevices.SECoPNodeDevice` class is the main entry point for creating
ophyd-async devices from SECoP nodes. It:

- Connects to a SECoP node via IP address and port
- Automatically builds the device tree from the node's description
- Creates ophyd-async signals and devices for all parameters and modules
- Exposes SECoP commands as Bluesky plan methods

Dynamic Device Generation
~~~~~~~~~~~~~~~~~~~~~~~~~~

Unlike most other ophyd devices that must be statically declared, SECoP-Ophyd devices are **dynamically
generated** at connection time. This means:

- No manual device class definition is needed
- The device structure matches the structure defined in the SEC node
- Changes to the SECoP node are automatically reflected

However, for better development experience with type hints and autocompletion, you can generate
static class files (see :doc:`tutorial`).

Next Steps
----------

- Follow the :doc:`tutorial` for a complete walkthrough
- Read the :doc:`user_guide` for detailed usage patterns
- Check the :doc:`reference` for API documentation
