"""The device of a whole SEC node, and the device class of each module, from
its interface classes."""

import logging
import time as ttime
from collections.abc import Mapping
from typing import Any

from ophyd_async.core import DEFAULT_TIMEOUT, StandardReadableFormat

from secop_ophyd.devices.acquisition.channel import SECoPAcquisitionChannelDevice
from secop_ophyd.devices.acquisition.control import (
    SECoPAcquisitionControllerDevice,
    SECoPAcquisitionDevice,
)
from secop_ophyd.devices.base import SECoPCommunicatorDevice, SECoPDevice
from secop_ophyd.devices.connector import _composite_target_params
from secop_ophyd.devices.drivable import (
    SECoPCompositeMoveableDevice,
    SECoPMoveableDevice,
)
from secop_ophyd.devices.readable import SECoPReadableDevice
from secop_ophyd.devices.writable import SECoPWritableDevice
from secop_ophyd.propertykeys import INTERFACE_CLASSES


class SECoPNodeDevice(SECoPDevice):

    hinted_signals: list[str] = []

    def __init__(
        self,
        sec_node_uri: str = "",  # SECoP resource identifier host:port:optional[module]
        name: str = "",
        loglevel=logging.INFO,
        logdir: str | None = None,
    ):
        # ensure sec_node_uri only contains host:port
        if sec_node_uri.count(":") != 1:
            raise RuntimeError(
                f"SECoPNodeDevice SRI must only contain host:port {sec_node_uri}"
            )

        self._callbacks_registered = False

        super().__init__(sri=sec_node_uri, name=name, loglevel=loglevel, logdir=logdir)

    async def connect(self, mock=False, timeout=DEFAULT_TIMEOUT, force_reconnect=False):
        await super().connect(mock, timeout, force_reconnect)

        if not mock and not self._callbacks_registered:
            # register secclient callbacks (these are useful if sec node
            # description changes after a reconnect)
            self._client.register_callback(
                None, self.descriptiveDataChange, self.nodeStateChange
            )
            self._callbacks_registered = True

    def descriptiveDataChange(self, module, description):  # noqa: N802
        raise RuntimeError(
            "The descriptive data has changed upon reconnect. Descriptive data changes"
            "are not supported: reinstantiate device"
        )

    def nodeStateChange(self, online, state):  # noqa: N802
        """called when the state of the connection changes

        'online' is True when connected or reconnecting, False when disconnected
        or connecting 'state' is the connection state as a string
        """
        if state == "connected" and online is True:
            self._client.conn_timestamp = ttime.time()

    async def _assign_interface_formats(self):
        # skip modules registered by an earlier (mock) connect
        moddevs = [
            dev
            for _, dev in self.children()
            if isinstance(dev, SECoPDevice) and dev.read not in self._read_funcs
        ]
        self.add_readables(moddevs, StandardReadableFormat.CHILD)

    async def class_from_instance(self, path_to_module: str | None = None):
        """Generate an annotated device class for this SEC node and write it
        to its own file, named after the generated node class, inside
        ``path_to_module`` (default: ``./secop_ophyd_devs/``). Any existing
        file of that name is overwritten."""
        # the code generator imports the device classes
        from secop_ophyd.codegen.generator import GenNodeCode

        description = (await self._client.request("describe"))[2]

        gen_code = GenNodeCode(path=path_to_module, log=self._logger)

        gen_code.from_json_describe(description)

        gen_code.write_gen_node_class_file()


IF_CLASSES = {
    "Acquisition": SECoPAcquisitionDevice,
    "AcquisitionController": SECoPAcquisitionControllerDevice,
    "AcquisitionChannel": SECoPAcquisitionChannelDevice,
    "Triggerable": SECoPAcquisitionDevice,
    "Drivable": SECoPMoveableDevice,
    "Writable": SECoPWritableDevice,
    "Readable": SECoPReadableDevice,
    "Communicator": SECoPCommunicatorDevice,
}


def class_from_interface(
    mod_properties: dict, parameters: Mapping[str, Any] | None = None
):
    """Device class for a module, from its interface classes. With the
    module's ``parameters`` given, a Drivable whose 'target' or 'value' is a
    struct/tuple gets `SECoPCompositeMoveableDevice`."""
    ophyd_class = None

    # infer highest level IF class
    module_interface_classes: dict = mod_properties[INTERFACE_CLASSES]
    for interface_class in IF_CLASSES.keys():
        if interface_class in module_interface_classes:
            ophyd_class = IF_CLASSES[interface_class]
            break

    # No predefined IF class was a match --> use base class (loose collection of
    # accessibles)
    if ophyd_class is None:
        ophyd_class = SECoPDevice  # type: ignore

    if (
        ophyd_class is SECoPMoveableDevice
        and parameters is not None
        and _composite_target_params(parameters)
    ):
        ophyd_class = SECoPCompositeMoveableDevice  # type: ignore

    return ophyd_class
