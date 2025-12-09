[![CI](https://github.com/SampleEnvironment/secop-ophyd/actions/workflows/code.yaml/badge.svg)](https://github.com/SampleEnvironment/secop-ophyd/actions/workflows/code.yaml)
[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](https://www.gnu.org/licenses/gpl-3.0)
[![PyPI](https://img.shields.io/pypi/v/secop-ophyd.svg)](https://pypi.org/project/secop-ophyd)

# SECoP-Ophyd

**SECoP-Ophyd** enables seamless integration of SECoP (Sample Environment Communication Protocol) devices into the  Bluesky experiment orchestration framework.

![secop-ophyd-arch](https://github.com/user-attachments/assets/cd82cfbe-68dc-4b3c-b872-5b1b7c7db82a)

SECoP-Ophyd acts as a bridge between SECoP-enabled hardware and Bluesky's ophyd layer. It uses [Frappy](https://github.com/SampleEnvironment/frappy) to communicate with SECoP nodes over TCP, automatically generating [ophyd-async](https://blueskyproject.io/ophyd-async/main/index.html) device objects from the node's descriptive data. These devices can then be used in [Bluesky plans](https://blueskyproject.io/bluesky/main/tutorial.html#the-run-engine-and-plans) just like any other ophyd device, enabling seamless integration with EPICS, Tango, and other control system backends.

For more information, see the [full documentation](https://sampleenvironment.github.io/secop-ophyd/).
