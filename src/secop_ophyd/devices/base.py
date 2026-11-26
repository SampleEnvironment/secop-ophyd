"""The common base of all SECoP devices, and the annotations that bind
declared signals to the SEC node."""

import logging
import warnings
from abc import abstractmethod
from collections.abc import Sequence
from logging import Logger
from typing import Dict, cast

from ophyd_async.core import (
    DEFAULT_TIMEOUT,
    Device,
    LazyMock,
    Signal,
    SignalR,
    SignalRW,
    StandardReadable,
    StandardReadableFormat,
    walk_devices,
)

from secop_ophyd.AsyncFrappyClient import AsyncSecopClient
from secop_ophyd.devices.connector import SECoPDeviceConnector
from secop_ophyd.logs import setup_logging
from secop_ophyd.propertykeys import EQUIPMENT_ID
from secop_ophyd.signals.backend import AttributeType, SECoPBackend


def format_assigned(device: StandardReadable, signal: SignalR) -> bool:
    if (
        signal.describe in device._describe_funcs
        or signal.describe in device._describe_config_funcs
    ):
        # Standard readable format already assigned
        return True

    return False


def is_read_signal(device: StandardReadable, signal: SignalR | SignalRW) -> bool:
    if signal.describe in device._describe_funcs:
        return True

    return False


TILED_MAX_NAME_LENGTH = 63


def warn_on_long_device_names(devices: Dict[str, Device]) -> None:
    """Warn for every device whose name exceeds the tiled storage limit."""
    for dev in devices.values():
        if len(dev.name) > TILED_MAX_NAME_LENGTH:
            warnings.warn(
                f"Device name: '{dev.name}' is too long for tiled storage "
                f"({len(dev.name)}>{TILED_MAX_NAME_LENGTH} chars)"
            )


class SECoPDevice(StandardReadable):

    _clients: Dict[str, AsyncSecopClient] = {}
    _connector: SECoPDeviceConnector

    _node_id: str
    _sri: str
    _host: str
    _port: str
    _module: str | None
    _logger: Logger

    hinted_signals: list[str] = []
    mandatory_parameters: list[str] = []

    def __init__(
        self,
        sri: str = "",  # SECoP resource identifier host:port:optional[module]
        name: str = "",
        connector: SECoPDeviceConnector | None = None,
        loglevel=logging.INFO,
        logdir: str | None = None,
    ) -> None:

        if connector and sri:
            raise RuntimeError("Provide either sri or connector, not both")

        if connector:
            sri = connector.sri
            loglevel = connector.loglevel
            logdir = connector.logdir

        self._sri = sri
        self._host = sri.split(":")[0]
        self._port = sri.split(":")[1]
        self._node_id = sri.split(":")[0] + ":" + sri.split(":")[1]

        # an explicit name is never replaced; otherwise the first real connect
        # names the device (node: equipment id, standalone module: module name)
        self._name_given = bool(name)
        self._auto_named = False

        self._logger = setup_logging(
            name=f"frappy:{self._host}:{self._port}",
            level=loglevel,
            log_dir=logdir,
        )

        # role of an acquisition channel on its controller
        self._acquisition_role: str | None = None

        self._module = None
        if len(sri.split(":")) > 2:
            self._module = sri.split(":")[2]

        if SECoPDevice._clients.get(self._node_id) is None:
            SECoPDevice._clients[self._node_id] = AsyncSecopClient(
                host=self._host, port=self._port, log=self._logger
            )

        connector = connector or SECoPDeviceConnector(sri=sri)

        self._client: AsyncSecopClient = SECoPDevice._clients[self._node_id]

        super().__init__(name=name, connector=connector)

    def set_module(self, module_name: str):
        if self._module == module_name:
            return
        if self._module is not None:
            raise RuntimeError(
                f"Device is already bound to module '{self._module}', "
                f"cannot rebind it to '{module_name}'"
            )

        self._module = module_name
        self._sri = self._sri + ":" + module_name

        self._connector.set_module(module_name)

    async def connect(
        self,
        mock: bool | LazyMock = False,
        timeout: float = DEFAULT_TIMEOUT,
        force_reconnect: bool = False,
    ):
        # the connector opens the client connection in connect_real(); in mock
        # mode nothing touches the network
        await super().connect(mock, timeout, force_reconnect)

        if not self._name_given and not self._auto_named:
            self._auto_name(mock=bool(mock))

        warn_on_long_device_names(walk_devices(self))

    def _auto_name(self, mock: bool) -> None:
        if self._module is not None:
            # a module inside a node device is named by its parent
            if self.parent is None:
                self.set_name(self._module)
                self._auto_named = True
        elif not mock and EQUIPMENT_ID in self._client.properties:
            self.set_name(self._client.properties[EQUIPMENT_ID].replace(".", "-"))
            self._auto_named = True

    @abstractmethod
    async def _assign_interface_formats(self):
        """Assign signal formats specific to this device's interface class.
        Subclasses override this to assign formats before default fallback."""

    async def _assign_default_formats(self):
        config_signals = []
        hinted_signals = []
        uncached_signals = []
        hinted_uncached_signals = []

        def assert_device_is_signalr(device: Device) -> SignalR:
            if not isinstance(device, SignalR):
                raise TypeError(f"{device} is not a SignalR")
            return device

        for _, child in self.children():

            if not isinstance(child, Signal):
                continue

            # _init_backend: in mock mode `backend` is the MockSignalBackend
            backend = child._connector._init_backend

            if not isinstance(backend, SECoPBackend):
                continue

            if not isinstance(child, SignalR):
                # write-only whole-struct/tuple SignalW: cannot be given a
                # read/config format, only its split SignalR members can
                continue

            # child is a Signal with a SECoPBackend (parameter, property, or
            # struct/tuple member)

            # check if signal already has a format assigned
            signalr_device = assert_device_is_signalr(child)

            if format_assigned(self, signalr_device):
                # format already assigned by annotation or module IF class
                continue

            # no format without a node description (mock mode)
            match getattr(backend, "format", StandardReadableFormat.CONFIG_SIGNAL):
                case StandardReadableFormat.CHILD:
                    raise RuntimeError("Signal cannot have CHILD format")
                case StandardReadableFormat.CONFIG_SIGNAL:
                    config_signals.append(signalr_device)
                case StandardReadableFormat.HINTED_SIGNAL:
                    hinted_signals.append(signalr_device)
                case StandardReadableFormat.UNCACHED_SIGNAL:
                    uncached_signals.append(signalr_device)
                case StandardReadableFormat.HINTED_UNCACHED_SIGNAL:
                    hinted_uncached_signals.append(signalr_device)

        # add signals to device in the order of their priority
        self.add_readables(config_signals, StandardReadableFormat.CONFIG_SIGNAL)

        self.add_readables(hinted_signals, StandardReadableFormat.HINTED_SIGNAL)

        self.add_readables(uncached_signals, StandardReadableFormat.UNCACHED_SIGNAL)

        self.add_readables(
            hinted_uncached_signals, StandardReadableFormat.HINTED_UNCACHED_SIGNAL
        )

    def _split_members(self, parent_param: str) -> dict[str, SignalR]:
        """Return the split member Signals of a decomposed composite parameter
        by member key: the struct member name, or the tuple index as a string
        (empty dict if `parent_param` is atomic, i.e. was not decomposed)."""
        result: dict[str, SignalR] = {}
        for attr_name, child in self.children():
            if not isinstance(child, SignalR):
                continue
            backend = child._connector._init_backend
            if (
                not isinstance(backend, SECoPBackend)
                or backend.attribute_type != AttributeType.MEMBER
            ):
                continue
            # without a node description (mock mode) the parent and the
            # member key are only known from the member's name
            parent = getattr(backend, "_parent_param", None)
            if parent == parent_param:
                result[backend._member_key] = child
            elif parent is None and attr_name.startswith(f"{parent_param}_"):
                result[attr_name.removeprefix(f"{parent_param}_")] = child
        return result

    def _split_signals(self, parent_param: str) -> list[SignalR]:
        """Return the split member Signals for a decomposed composite
        parameter (empty list if `parent_param` is atomic, i.e. was not
        decomposed)."""
        return list(self._split_members(parent_param).values())

    def _assign_hinted_format(
        self, param_name: str, signals: Sequence[SignalR]
    ) -> None:
        """Assign HINTED_SIGNAL to whichever of `signals` (either `[self.value]`/
        `[self.target]` for an atomic parameter, or its `_split_signals(...)`
        for a decomposed one) don't already have a format assigned; warn about
        any that do but aren't a read format."""
        unassigned = []
        for signal in signals:
            if format_assigned(self, signal):
                if not is_read_signal(self, signal):
                    warnings.warn(
                        f"Signal '{signal.name}' of device {self.name} has format "
                        f"assigned that is not compatible with {param_name}'s "
                        "interface class role"
                    )
            else:
                unassigned.append(signal)

        if unassigned:
            self.add_readables(unassigned, StandardReadableFormat.HINTED_SIGNAL)


class SECoPCommunicatorDevice(SECoPDevice):

    hinted_signals: list[str] = []

    def __init__(
        self,
        sri: str = "",  # SECoP resource identifier host:port:optional[module]
        name: str = "",
        connector: SECoPDeviceConnector | None = None,
        loglevel=logging.INFO,
        logdir: str | None = None,
    ) -> None:
        super().__init__(
            sri=sri, name=name, connector=connector, loglevel=loglevel, logdir=logdir
        )

    async def _assign_interface_formats(self):
        # Communicator has no specific interface class formats
        pass


class ParameterType:
    """Annotation for Parameter Signals, defines the path to the parameter
    in the secclient module dict"""

    def __repr__(self) -> str:
        """Return repr suitable for code generation in annotations."""
        return "ParamT()"

    def __call__(self, parent: Device, child: Device):
        if not isinstance(child, Signal):
            return

        backend = child._connector.backend

        if not isinstance(backend, SECoPBackend):
            return

        backend.attribute_type = AttributeType.PARAMETER
        # annotations only appear on SECoPDevice subclasses
        backend._secclient = cast("SECoPDevice", parent)._client


class ParameterMemberType:
    """Annotation for a split struct/tuple member Signal (one field/index of
    a decomposed composite parameter). Distinct from `ParameterType` so
    annotation-scanning doesn't mark it AttributeType.PARAMETER -- it must
    stay unset until SECoPDeviceConnector.connect_real() calls
    init_member_from_introspection(), which needs to see it as MEMBER (or
    unset), not PARAMETER.

    ``parent`` and ``key`` name the parameter and the member (struct member
    name, or tuple index as a string). Without a node description (mock mode)
    they are the only way to tell e.g. the tuple index 0 of 'target' from the
    member 0 of a parameter 'target_limits'; without them, the member is
    found from its attribute name."""

    def __init__(self, parent: str | None = None, key: str | None = None) -> None:
        if (parent is None) != (key is None):
            raise ValueError("give both 'parent' and 'key', or neither")
        self.parent = parent
        self.key = key

    def __repr__(self) -> str:
        """Return repr suitable for code generation in annotations."""
        if self.parent is None:
            return "ParamMemberT()"
        return f"ParamMemberT({self.parent!r}, {self.key!r})"

    def __call__(self, parent: Device, child: Device):
        if not isinstance(child, Signal):
            return

        backend = child._connector.backend

        if not isinstance(backend, SECoPBackend):
            return

        backend.attribute_type = AttributeType.MEMBER
        if self.parent is not None and self.key is not None:
            # overwritten with the same values by init_member_from_introspection()
            backend._parent_param = self.parent
            backend._member_key = self.key
        # annotations only appear on SECoPDevice subclasses
        backend._secclient = cast("SECoPDevice", parent)._client


class PropertyType:
    """Annotation for Module Property Signals, defines the path to the property"""

    def __repr__(self) -> str:
        """Return repr suitable for code generation in annotations."""
        return "PropT()"

    def __call__(self, parent: Device, child: Device):
        if not isinstance(child, Signal):
            return

        backend = child._connector.backend

        if not isinstance(backend, SECoPBackend):
            return

        backend.attribute_type = AttributeType.PROPERTY
        # annotations only appear on SECoPDevice subclasses
        backend._secclient = cast("SECoPDevice", parent)._client
