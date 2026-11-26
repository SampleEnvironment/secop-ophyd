import asyncio
import logging
import re
import time as ttime
import warnings
from abc import abstractmethod
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from functools import cached_property
from logging import Logger
from typing import Any, Dict, Generic, TypeVar, cast

import bluesky.plan_stubs as bps
import numpy as np
from bluesky.protocols import (
    Checkable,
    DataKey,
    EventPageCollectable,
    Locatable,
    Location,
    PartialEventPage,
    Pausable,
    Reading,
    Stoppable,
    Subscribable,
    Triggerable,
)
from frappy.datatypes import CommandType, DataType, StructOf, TupleOf
from frappy.errors import SECoPError
from ophyd_async.core import (
    DEFAULT_TIMEOUT,
    AsyncStatus,
    Command,
    Device,
    DeviceConnector,
    DeviceFiller,
    DeviceMock,
    FlyableLogic,
    InstantMovableMock,
    LazyMock,
    MovableLogic,
    Signal,
    SignalDatatypeT,
    SignalR,
    SignalRW,
    SignalW,
    StandardFlyable,
    StandardMovable,
    StandardReadable,
    StandardReadableFormat,
    TimeoutCalculator,
    TriggerableCommand,
    observe_value,
    set_mock_value,
    wait_for_value,
    walk_devices,
)
from ophyd_async.core._device import DEVICE_RESERVED_ATTRS
from ophyd_async.core._utils import Callback

from secop_ophyd.AsyncFrappyClient import AsyncSecopClient
from secop_ophyd.logs import setup_logging
from secop_ophyd.propertykeys import EQUIPMENT_ID, INTERFACE_CLASSES
from secop_ophyd.SECoPSignal import (
    AttributeType,
    SECoPBackend,
    SECoPCommandBackend,
    SECoPCommandConnector,
    describedict_to_datakey,
    module_property_signals,
)
from secop_ophyd.util import (
    MAX_DEPTH,
    CompositeKind,
    IncompatibleSECoPDatatype,
    Path,
    SECoPdtype,
    classify_datatype,
    get_composite_members,
    optional_struct_members,
)

# Predefined Status Codes
DISABLED = 0
IDLE = 100
STANDBY = 130
PREPARED = 150
WARN = 200
WARN_STANDBY = 230
WARN_PREPARED = 250
NSTABLE = 270  # not in SECoP standard (yet)
BUSY = 300
DISABLING = 310
INITIALIZING = 320
PREPARING = 340
STARTING = 360
RAMPING = 370
STABILIZING = 380
FINALIZING = 390
ERROR = 400
ERROR_STANDBY = 430
ERROR_PREPARED = 450
UNKNOWN = 401  # not in SECoP standard (yet)

# status code per standard status name, for status signals whose SECoP
# datatype is not known (mock mode)
STATUS_CODES = {
    "DISABLED": DISABLED,
    "IDLE": IDLE,
    "STANDBY": STANDBY,
    "PREPARED": PREPARED,
    "WARN": WARN,
    "WARN_STANDBY": WARN_STANDBY,
    "WARN_PREPARED": WARN_PREPARED,
    "NSTABLE": NSTABLE,
    "BUSY": BUSY,
    "DISABLING": DISABLING,
    "INITIALIZING": INITIALIZING,
    "PREPARING": PREPARING,
    "STARTING": STARTING,
    "RAMPING": RAMPING,
    "STABILIZING": STABILIZING,
    "FINALIZING": FINALIZING,
    "ERROR": ERROR,
    "ERROR_STANDBY": ERROR_STANDBY,
    "ERROR_PREPARED": ERROR_PREPARED,
    "UNKNOWN": UNKNOWN,
}


IGNORED_PROPS = ["_plotly"]

TILED_MAX_NAME_LENGTH = 63


def command_attr_name(command: str) -> str:
    """Attribute name of a SECoP command on its device: with a trailing
    underscore if the name is used by a bluesky protocol (e.g. 'prepare')."""
    return f"{command}_" if command in DEVICE_RESERVED_ATTRS else command


def clean_identifier(anystring):
    return str(re.sub(r"\W+|^(?=\d)", "_", anystring))


def warn_on_long_device_names(devices: Dict[str, Device]) -> None:
    """Warn for every device whose name exceeds the tiled storage limit."""
    for dev in devices.values():
        if len(dev.name) > TILED_MAX_NAME_LENGTH:
            warnings.warn(
                f"Device name: '{dev.name}' is too long for tiled storage "
                f"({len(dev.name)}>{TILED_MAX_NAME_LENGTH} chars)"
            )


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


def is_config_signal(device: StandardReadable, signal: SignalR | SignalRW) -> bool:
    if signal.describe in device._describe_config_funcs:
        return True

    return False


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


class SECoPDeviceConnector(DeviceConnector):
    """Connector for SECoP node and module devices.

    Children declared by class annotations are created when the device is
    constructed, as unfilled. On a real connect, the SEC node description fills
    them (and, with ``auto_fill_signals``, adds everything else the node
    describes); any annotated child the node does not provide is an error. In
    mock mode only the annotated children exist and nothing touches the
    network.

    Filling and format assignment run once per device, so repeated connects
    (including ``force_reconnect=True``) only reconnect.
    """

    sri: str
    module: str | None
    node_id: str
    _auto_fill_signals: bool

    def __init__(
        self,
        sri: str,
        auto_fill_signals: bool = True,
        loglevel=logging.INFO,
        logdir: str | None = None,
    ) -> None:

        self.sri = sri
        self.node_id = sri.split(":")[0] + ":" + sri.split(":")[1]
        self._auto_fill_signals = auto_fill_signals
        self.loglevel = loglevel
        self.logdir = logdir
        self._filled = False
        self._fill_error: Exception | None = None
        self._formats_assigned = False

        if sri.count(":") == 2:
            self.module = sri.split(":")[2]
        elif sri.count(":") == 1:
            self.module = None
        else:
            raise RuntimeError(f"Invalid SECoP resource identifier: {sri}")

        if SECoPDevice._clients.get(self.node_id) is None:
            raise RuntimeError(f"No AsyncSecopClient for URI {sri} exists")

        self.client: AsyncSecopClient = SECoPDevice._clients[self.node_id]

    def set_module(self, module_name: str):
        if self.module == module_name:
            return
        if self.module is not None:
            raise RuntimeError(
                f"Connector is already bound to module '{self.module}', "
                f"cannot rebind it to '{module_name}'"
            )
        self.module = module_name
        self.sri = self.sri + ":" + module_name

    def create_children_from_annotations(self, device: Device):
        if hasattr(self, "filler"):
            return

        self.filler = DeviceFiller(
            device=device,
            signal_backend_factory=SECoPBackend[Any],
            device_connector_factory=lambda: SECoPDeviceConnector(
                self.sri, self._auto_fill_signals, self.loglevel, self.logdir
            ),
            command_backend_factory=SECoPCommandBackend,
        )

        # created unfilled: connect_real() must find every annotated child in
        # the node description, check_filled() reports the ones it does not
        list(self.filler.create_signals_from_annotations(filled=False))
        list(self.filler.create_devices_from_annotations(filled=False))
        list(self.filler.create_commands_from_annotations(filled=False))

        self.filler.check_created()

        for attr_name, child in device.children():
            if isinstance(child, Command):
                child._connector = SECoPCommandConnector(child._connector.backend)
            elif isinstance(child, SECoPDevice):
                # an annotated module device knows its module from its
                # attribute name; needed for mock mode, where no node
                # description assigns it
                child.set_module(attr_name.rstrip("_"))

    async def connect_mock(self, device: Device, mock: LazyMock):
        # Set the name of the device to name all children
        device.set_name(device.name)
        await super().connect_mock(device, mock)
        await self._assign_formats(device)

    async def connect_real(self, device: Device, timeout: float, force_reconnect: bool):
        if not self.sri:
            raise RuntimeError(f"Could not connect to SEC node: {self.sri}")

        # Establish connection to SEC Node
        await self.client.connect(3)

        if self._fill_error is not None:
            # a failed fill leaves a half-built device; filling again would
            # only fail on the children the first attempt already created
            raise RuntimeError(
                f"{device.name}: building the device from the description of "
                f"{self.node_id} failed earlier ({self._fill_error}); create a "
                f"new device instance"
            ) from self._fill_error

        if not self._filled:
            try:
                if self.module:
                    await self._fill_module(device, timeout)
                else:
                    self._fill_node(device)

                self.filler.check_filled(f"{self.node_id}")
            except Exception as exc:
                self._fill_error = exc
                raise
            self._filled = True
            # a mock connect before only assigned formats to annotated signals
            self._formats_assigned = False

        # Set the name of the device to name all children
        device.set_name(device.name)
        await super().connect_real(device, timeout, force_reconnect)

        await self._assign_formats(device)

    async def _fill_module(self, device: Device, timeout: float):
        """Fill parameters, properties and commands of a module device from
        the node description."""
        assert self.module is not None

        if self.module not in self.client.modules:
            raise RuntimeError(
                f"Module '{self.module}' is not provided by SEC node {self.node_id} "
                f"(available modules: {sorted(self.client.modules)})"
            )

        # deferred import to avoid a circular import (GenNodeCode imports
        # from this module)
        from secop_ophyd.GenNodeCode import get_type_param

        # Fill Parmeters
        parameter_dict = self.client.modules[self.module]["parameters"]

        _check_drivable_class(
            device,
            self.module,
            parameter_dict,
            self.client.modules[self.module]["commands"],
        )

        # a struct/tuple target is only changed through set()
        set_only_target = isinstance(device, SECoPWritableDevice) and (
            "target" in _composite_target_params(parameter_dict)
        )

        # remove ignored signals
        parameters = [
            child
            for child in parameter_dict.keys()
            if child not in self.filler.ignored_signals
        ]

        # Dertermine children that are declared but not yet filled
        not_filled = {unfilled for unfilled, _ in device.children()}

        mandatory_parameters = getattr(device, "mandatory_parameters", [])

        # parameters that got at least one Signal (split members and/or a
        # monolithic signal)
        resolved: set[str] = set()

        for param_name in parameters:
            raw_datatype = parameter_dict[param_name]["datatype"]
            readonly = parameter_dict[param_name]["readonly"] or (
                set_only_target and param_name == "target"
            )
            kind = classify_datatype(raw_datatype)

            if kind is CompositeKind.DECOMPOSABLE:
                members = get_composite_members(raw_datatype)
                declared = param_name in not_filled or any(
                    f"{param_name}_{member_key}" in not_filled
                    for member_key, _ in members
                )
            else:
                declared = param_name in not_filled

            if not (self._auto_fill_signals or declared):
                continue

            if kind is CompositeKind.UNSUPPORTED:
                msg = (
                    f"Parameter '{param_name}' of module '{self.module}' has a "
                    f"datatype nesting depth of "
                    f"{SECoPdtype(raw_datatype).max_depth} (struct/tuple nested "
                    f"inside a struct/tuple/array). Tiled & Databroker only "
                    f"support flat struct/tuple parameters (depth <= "
                    f"{MAX_DEPTH}); no signal will be created for '{param_name}'."
                )
                if param_name in mandatory_parameters:
                    raise IncompatibleSECoPDatatype(msg)
                warnings.warn(msg)
                continue

            if kind is CompositeKind.DECOMPOSABLE:
                for member_key, member_dtype in members:
                    member_backend = self.filler.fill_child_signal(
                        f"{param_name}_{member_key}", SignalR
                    )
                    member_backend.init_member_from_introspection(
                        module_name=self.module,
                        parent_param=param_name,
                        member_key=member_key,
                        member_datatype=member_dtype,
                        secclient=self.client,
                    )

                if not readonly:
                    write_backend = self.filler.fill_child_signal(param_name, SignalW)

                    write_datatype = get_type_param(raw_datatype)
                    write_backend.init_parameter_from_introspection(
                        datatype=write_datatype,  # type: ignore[arg-type]
                        path=self.module + ":" + param_name,
                        secclient=self.client,
                    )

                resolved.add(param_name)
                continue

            # ATOMIC: single signal
            signal_type = SignalR if readonly else SignalRW

            backend = self.filler.fill_child_signal(param_name, signal_type)

            datatype = get_type_param(raw_datatype)
            backend.init_parameter_from_introspection(
                datatype=datatype,  # type: ignore[arg-type]
                path=self.module + ":" + param_name,
                secclient=self.client,
            )
            resolved.add(param_name)

        for param_name in mandatory_parameters:
            if param_name not in resolved:
                raise AttributeError(
                    f"Parameter '{param_name}' of module '{self.module}' has not "
                    f"been assigned, but is needed for the "
                    f"{type(device).__name__} interface class"
                )

        # Fill Properties
        property_signals = module_property_signals(
            self.client.modules[self.module]["properties"]
        )

        # remove ignored signals
        module_properties = [
            child
            for child in property_signals
            if child not in self.filler.ignored_signals and child not in IGNORED_PROPS
        ]

        for mod_property_name in module_properties:
            if self._auto_fill_signals or mod_property_name in not_filled:

                # properties are always read only
                backend = self.filler.fill_child_signal(mod_property_name, SignalR)

                datatype = get_type_param(property_signals[mod_property_name][0])

                backend.init_property_from_introspection(
                    datatype=datatype,  # type: ignore[arg-type]
                    path=self.module + ":" + mod_property_name,
                    secclient=self.client,
                )

        # Fill Commands
        command_dict = self.client.modules[self.module]["commands"]

        # "stop" is skipped: the Drivable and Acquisition device classes
        # already implement Stoppable.stop() (a real bound method), so
        # exposing a raw command device at the same bare name would
        # silently shadow it.
        commands = [
            c
            for c in command_dict.keys()
            if c not in self.filler.ignored_signals and c != "stop"
        ]

        wait_for_idle_fn = getattr(device, "wait_for_idle", None)

        for command_name in commands:
            # a command named like a bluesky protocol method (e.g. 'prepare')
            # gets a trailing underscore, as the DeviceFiller does for signals
            attr_name = command_attr_name(command_name)
            declared = attr_name in not_filled
            if self._auto_fill_signals or declared:
                cmd_datatype: CommandType = command_dict[command_name]["datatype"]
                command_type = (
                    TriggerableCommand
                    if cmd_datatype.argument is None and cmd_datatype.result is None
                    else Command
                )

                # a declared command is looked up by its name without the
                # underscore, a new one is created under its attribute name
                cmd_backend = self.filler.fill_child_command(
                    command_name if declared else attr_name, command_type
                )

                cmd_path = Path(parameter_name=command_name, module_name=self.module)
                cmd_backend.init_command_from_introspection(
                    cmd_datatype, cmd_path, self.client, wait_for_idle_fn
                )

    def _fill_node(self, device: Device):
        """Fill module devices and node properties from the node description."""
        from secop_ophyd.GenNodeCode import get_type_prop

        # Fill Module devices
        modules = self.client.modules

        not_filled = {unfilled for unfilled, _ in device.children()}

        for module_name in modules.keys():
            if self._auto_fill_signals or module_name in not_filled:
                module_properties = modules[module_name]["properties"]
                device_sub_class = class_from_interface(
                    module_properties, modules[module_name]["parameters"]
                )

                self.filler.fill_child_device(module_name, device_sub_class)

                mod_dev: SECoPDevice = getattr(device, module_name)
                mod_dev.set_module(module_name)

        # Fill Node properties
        node_property_dict = self.client.properties

        # remove ignored signals
        node_properties = [
            child
            for child in node_property_dict.keys()
            if child not in self.filler.ignored_signals
        ]

        for node_property_name in node_properties:
            if self._auto_fill_signals or node_property_name in not_filled:

                # properties are always read only
                backend = self.filler.fill_child_signal(node_property_name, SignalR)

                datatype = get_type_prop(node_property_dict[node_property_name])

                backend.init_property_from_introspection(
                    datatype=datatype,  # type: ignore[arg-type]
                    path=node_property_name,
                    secclient=self.client,
                )

    async def _assign_formats(self, device: Device):
        """Assign StandardReadableFormats to all signals, once.

        Priority:
        1. Format given in an annotation -- already applied by the DeviceFiller
           when the child was created
        2. Module interface class (value, target, ...), skipping signals that
           already have a format (with a warning on a config vs read conflict)
        3. ``_signal_format`` parameter property, falling back to
           CONFIG_SIGNAL; CHILD for the module devices of a node
        """
        if self._formats_assigned or not isinstance(device, SECoPDevice):
            return

        await device._assign_interface_formats()
        await device._assign_default_formats()
        self._formats_assigned = True


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
        from secop_ophyd.GenNodeCode import GenNodeCode

        description = (await self._client.request("describe"))[2]

        genCode = GenNodeCode(path=path_to_module, log=self._logger)

        genCode.from_json_describe(description)

        genCode.write_gen_node_class_file()


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


def _set_mock_status_idle(device: Device) -> None:
    """Start a mocked module in the IDLE state, if its status enum has one
    (a mock enum signal otherwise starts at its first member)."""
    status_0 = getattr(device, "status_0", None)
    if not isinstance(status_0, SignalR):
        return

    datatype = status_0.datatype
    if datatype is str:
        set_mock_value(status_0, "IDLE")
    elif isinstance(datatype, type) and issubclass(datatype, Enum):
        idle = next((m for m in datatype if m.value == "IDLE"), None)
        if idle is not None:
            set_mock_value(status_0, idle)


class SECoPReadableMock(DeviceMock["SECoPReadableDevice"]):
    """Mock behaviour for SECoP Readable modules: start IDLE."""

    async def connect(self, device: "SECoPReadableDevice") -> None:
        _set_mock_status_idle(device)


class SECoPMovableMock(InstantMovableMock):
    """Mock behaviour for SECoP Drivable modules: start IDLE, and move the
    readback to the target instantly."""

    async def connect(  # type: ignore[override]
        self, device: "SECoPMoveableDevice"
    ) -> None:
        missing = [
            name
            for name in ("target", "value", "status_0")
            if not hasattr(device, name)
        ]
        if missing:
            raise AttributeError(
                f"{device.name}: a Drivable connected in mock mode needs annotated "
                f"{missing}: without a SEC node description only annotated "
                f"signals exist"
            )
        _set_mock_status_idle(device)
        await super().connect(device)


class SECoPWritableMock(DeviceMock["SECoPWritableDevice"]):
    """Mock behaviour for SECoP Writable modules, and for Drivable modules with
    a struct/tuple 'value' or 'target': start IDLE. `SECoPWritableDevice.set()`
    itself moves the mocked signals to the target instantly."""

    async def connect(self, device: "SECoPWritableDevice") -> None:
        missing = [
            param for param in ("target", "value") if not device._param_signals(param)
        ]
        if not hasattr(device, "status_0"):
            missing.append("status_0")
        if missing:
            raise AttributeError(
                f"{device.name}: a {type(device).__name__} connected in mock mode "
                f"needs annotated {missing}: without a SEC node description only "
                f"annotated signals exist"
            )
        _set_mock_status_idle(device)


class SECoPReadableDevice(SECoPDevice, Triggerable, Subscribable):
    """
    Standard readable SECoP device, corresponding to a SECoP module with the
    interface class "Readable"
    """

    hinted_signals: list[str] = ["value"]
    mandatory_parameters: list[str] = ["value", "status"]

    _mock_class: type[DeviceMock] = SECoPReadableMock

    def __init__(
        self,
        sri: str = "",  # SECoP resource identifier host:port:optional[module]
        name: str = "",
        connector: SECoPDeviceConnector | None = None,
        loglevel=logging.INFO,
        logdir: str | None = None,
    ):
        """Initializes the SECoPReadableDevice

        :param secclient: SECoP client providing communication to the SEC Node
        :type secclient: AsyncSecopClient
        :param module_name: Name of the SEC Node module that is represented by
            this device
        :type module_name: str
        """

        self.value: SignalR
        # status is SECoP's StatusType == TupleOf(EnumType, StringType), always
        # decomposed: status_0 is the status code, status_1 the status text
        self.status_0: SignalR

        super().__init__(
            sri=sri, name=name, connector=connector, loglevel=loglevel, logdir=logdir
        )

    async def _assign_interface_formats(self):
        value_signals = (
            [self.value] if hasattr(self, "value") else self._split_signals("value")
        )
        self._assign_hinted_format("value", value_signals)

        # TODO ensure status signals must be neither config nor read format

    async def wait_for_idle(self):
        """asynchronously waits until module is IDLE again. this is helpful,
        for running commands that are not done immediately
        """

        self._logger.info(f"Waiting for {self.name} to be IDLE")

        if self.status_0 is None:
            self._logger.error("Status Signal not initialized")
            raise Exception("status Signal not initialized")

        # force reading of fresh status from device
        await self.status_0.read(False)

        async for stat_name in observe_value(self.status_0):
            stat_code = _status_code(self.status_0, stat_name)
            # Module is in IDLE/WARN state
            if IDLE <= stat_code < BUSY:
                self._logger.info(f"Module {self.name} --> IDLE")
                break

            if hasattr(self, "_stopped"):
                # self.logger.info(f"Module {self.name} was stopped STOPPED")
                if self._stopped is True:
                    break

            # Error State or DISABLED
            if hasattr(self, "_success"):
                if stat_code >= ERROR or stat_code < IDLE:
                    self._logger.error(f"Module {self.name} --> ERROR/DISABLED")
                    self._success = False
                    break

    # TODO add timeout
    def observe_status_change(self, monitored_status_code: int):
        """Plan stub: block until the module's status code differs from
        ``monitored_status_code``."""

        async def switch_from_status_inner():
            async for stat_name in observe_value(self.status_0):
                if monitored_status_code != _status_code(self.status_0, stat_name):
                    break

        def switch_from_status_factory():
            return switch_from_status_inner()

        yield from bps.wait_for([switch_from_status_factory])

    def trigger(self) -> AsyncStatus:
        if self._mock is not None:
            # mock mode: no SEC node to read from
            return AsyncStatus(asyncio.sleep(0))
        self._logger.info(f"Triggering {self.name}: read fresh data from device")
        # get fresh reading of the value Parameter from the SEC Node
        return AsyncStatus(
            awaitable=self._client.get_parameter(self._module, "value", trycache=False)
        )

    def subscribe_reading(self, function: Callback[dict[str, Reading]]) -> None:
        """Subscribe to updates in the reading. Only supported for an atomic
        (non-composite) 'value' parameter."""
        if not hasattr(self, "value"):
            raise NotImplementedError(
                f"{self.name}: 'value' is a struct/tuple, split into the signals "
                f"{[sig.name for sig in self._split_signals('value')]}; subscribe "
                f"to those"
            )
        self.value.subscribe_reading(function)

    # Backwards-compatible alias (bluesky < 1.16 named this ``subscribe``)
    subscribe = subscribe_reading

    def clear_sub(self, function: Callback) -> None:
        """Remove a subscription."""
        if not hasattr(self, "value"):
            raise NotImplementedError(
                f"{self.name}: 'value' is a struct/tuple, split into the signals "
                f"{[sig.name for sig in self._split_signals('value')]}; "
                f"unsubscribe from those"
            )
        self.value.clear_sub(function=function)


def _remaining(deadline: float | None) -> float | None:
    """Seconds left until ``deadline`` (a ``time.monotonic()`` value)."""
    return None if deadline is None else max(0.0, deadline - ttime.monotonic())


def _is_failed(stat_code: int) -> bool:
    """ERROR or DISABLED"""
    return stat_code >= ERROR or stat_code < IDLE


async def _wait_while_busy(
    status: SignalR,
    module: str,
    timeout: float | None,
    secclient: AsyncSecopClient | None,
) -> int:
    """Wait until the module's status has left BUSY and return the status code
    it is in then.

    Call it only *after* the action that makes the module BUSY (a 'target'
    write, a 'go' command, ...) has been answered: the resting state is already
    "not busy", so waiting from before the action would end at once.

    ``secclient`` is None in mock mode, where there is no SEC node to ask.
    """
    deadline = None if timeout is None else ttime.monotonic() + timeout

    def _left_busy(stat_name) -> bool:
        stat_code = _status_code(status, stat_name)
        return not (BUSY <= stat_code < ERROR)

    # ask the SEC node for the status, so we don't wait on a stale, cached
    # value: the node may send the BUSY update after its reply to the action.
    # The reply updates the cache that 'status' reads from.
    if secclient is not None:
        async with asyncio.timeout(timeout):
            await secclient.get_parameter(module, "status", trycache=False)

    # wait_for_value's own timeout names the signal and its last value
    await wait_for_value(status, _left_busy, timeout=_remaining(deadline))

    return _status_code(status, await status.get_value())


async def _wait_for_move(
    status: SignalR,
    module: str,
    logger: Logger,
    new_position: Any,
    timeout: float | None,
    secclient: AsyncSecopClient | None,
) -> None:
    """Wait until a move started by writing 'target' has finished: the module's
    status has left BUSY. Raises if it ended in ERROR/DISABLED.

    ``secclient`` is None in mock mode, where there is no SEC node to ask.
    """
    stat_code = await _wait_while_busy(status, module, timeout, secclient)

    if _is_failed(stat_code):
        logger.error(f"Module {module} --> ERROR/DISABLED")
        raise RuntimeError(
            f"Move of {module} to {new_position} failed: module "
            "entered ERROR/DISABLED state"
        )

    logger.info(f"Reached target, module {module} --> IDLE")


@dataclass(frozen=True)
class AcquisitionGoal:
    """When an acquisition channel ends a cycle: the value given to
    `SECoPAcquisitionDevice.prepare`.

    :param goal: written to the 'goal' parameter: the cycle ends when 'value'
        reaches it. None leaves 'goal' unchanged.
    :param enable: written to the 'goal_enable' parameter: whether the goal
        ends the cycle at all. None leaves 'goal_enable' unchanged. A module
        with a 'goal' but no 'goal_enable' always has its goal active.
    """

    goal: Any = None
    enable: bool | None = None


class _ReadingBuffer:
    """The readings of one signal, kept in numpy arrays that double in size
    when full.

    Numeric (and bool) values of one shape go into one array, so buffering
    many updates neither allocates per update nor holds a Python object each.
    Once a value does not fit the array (another shape, or a type that cannot
    be cast to it without loss) and for all other values (strings, enums),
    the buffer holds a list instead.
    """

    def __init__(self, capacity: int = 64) -> None:
        self._capacity = capacity
        self._len = 0
        self._timestamps = np.empty(capacity)
        self._values: np.ndarray | None = None
        self._objects: list[Any] | None = None

    def __len__(self) -> int:
        return self._len

    @property
    def last_timestamp(self) -> float | None:
        return float(self._timestamps[self._len - 1]) if self._len else None

    def _grow(self) -> None:
        size = 2 * len(self._timestamps)
        timestamps = np.empty(size)
        timestamps[: self._len] = self._timestamps[: self._len]
        self._timestamps = timestamps
        if self._values is not None:
            values = np.empty((size, *self._values.shape[1:]), self._values.dtype)
            values[: self._len] = self._values[: self._len]
            self._values = values

    def append(self, value: Any, timestamp: float) -> None:
        if self._len == len(self._timestamps):
            self._grow()
        self._timestamps[self._len] = timestamp

        if self._objects is None:
            array = np.asarray(value)
            if self._values is None and self._len == 0 and array.dtype.kind in "biuf":
                self._values = np.empty(
                    (len(self._timestamps), *array.shape), array.dtype
                )
            if (
                self._values is not None
                and array.shape == self._values.shape[1:]
                and np.can_cast(array.dtype, self._values.dtype, "same_kind")
            ):
                self._values[self._len] = array
                self._len += 1
                return
            # does not fit (or never fitted) an array: keep a list from now on
            self._objects = (
                [] if self._values is None else list(self._values[: self._len])
            )
            self._values = None

        self._objects.append(value)
        self._len += 1

    def take(self, count: int) -> tuple[list[Any], list[float]]:
        """Remove the first ``count`` readings and return their values and
        timestamps."""
        count = min(count, self._len)
        timestamps = self._timestamps[:count].tolist()
        rest = self._len - count

        values: list[Any]
        if self._objects is not None:
            values = self._objects[:count]
            del self._objects[:count]
        elif self._values is not None:
            taken = self._values[:count]
            # scalars as Python numbers, arrays as one ndarray per reading
            values = taken.tolist() if taken.ndim == 1 else list(taken.copy())
            self._values[:rest] = self._values[count : self._len]
        else:
            values = []

        self._timestamps[:rest] = self._timestamps[count : self._len]
        self._len = rest
        return values, timestamps

    def clear(self) -> None:
        self.take(self._len)


@dataclass
class SECoPAcquisitionLogic(FlyableLogic[AcquisitionGoal | None, None]):
    """Fly logic of a SECoP acquisition: ``prepare`` sets the goal and runs the
    module's 'prepare' command, ``kickoff`` sends 'go', and ``complete`` waits
    until the cycle has ended."""

    device: "SECoPAcquisitionDevice"

    async def on_prepare(self, value: AcquisitionGoal | None) -> None:
        await self.device._prepare_cycle(value)

    async def on_kickoff(self, ctx: None) -> None:
        await self.device._start_cycle(buffer=True)

    async def on_complete(self, ctx: None) -> None:
        await self.device._finish_cycle()

    async def on_stage(self) -> None:
        self.device._clear_buffers()

    async def on_unstage(self) -> None:
        await self.device._abort_cycle()


class SECoPAcquisitionDevice(
    SECoPReadableDevice,
    StandardFlyable[AcquisitionGoal | None, None],
    EventPageCollectable,
    Stoppable,
    Pausable,
):
    """
    SECoP device for a module with the interface class "Acquisition": a data
    acquisition with a single channel, in one module. The legacy interface
    class "Triggerable" maps here too.

    One acquisition cycle runs from the 'go' command until 'status' leaves
    BUSY. It can be run

    - step by step: ``trigger()`` runs one cycle, and ``read()`` then gives
      its result (the 'value' at the end of the cycle).
    - as a flyer: ``prepare(AcquisitionGoal(...) | None)`` sets 'goal' and
      'goal_enable' and runs the module's 'prepare' command (if it has one),
      ``kickoff()`` sends 'go' and ``complete()`` waits until the cycle has
      ended.

    ``collect_pages()`` hands over the data of the cycles run as a flyer, in a
    stream named after the device:

    - with a 'get_data' command: the result of 'get_data'.
    - without one: every update of 'value' the SEC node sent from
      ``kickoff()`` to ``complete()``, buffered here.

    ``stop()`` ends a running cycle. A RunEngine pause stops a cycle that
    ``trigger()`` or ``kickoff()`` started: on resume, the RunEngine runs the
    plan again from its last checkpoint, which starts a new cycle.
    """

    hinted_signals: list[str] = ["value"]

    go: TriggerableCommand

    def __init__(
        self,
        sri: str = "",  # SECoP resource identifier host:port:optional[module]
        name: str = "",
        connector: SECoPDeviceConnector | None = None,
        loglevel=logging.INFO,
        logdir: str | None = None,
    ):
        """Initialize SECoPAcquisitionDevice

        :param sri: SECoP resource identifier ``host:port:module``
        :param name: name of the device
        """

        # read by wait_for_idle()
        self._success = True
        self._stopped = False

        # a cycle started by trigger()/kickoff() is running
        self._acquiring = False
        # the result of 'get_data' was collected after its cycle ended
        self._data_collected = False

        # signal name -> buffered updates
        self._buffers: dict[str, _ReadingBuffer] = {}
        self._subscriptions: list[tuple[SignalR, Callback]] = []

        super().__init__(
            sri=sri, name=name, connector=connector, loglevel=loglevel, logdir=logdir
        )

    @cached_property
    def flyable_logic(self) -> SECoPAcquisitionLogic:
        return SECoPAcquisitionLogic(device=self)

    # --- SEC node access -------------------------------------------------

    @property
    def _secclient(self) -> AsyncSecopClient | None:
        """None in mock mode, where there is no SEC node to talk to."""
        return None if self._mock is not None else self._client

    def _has_command(self, command: str) -> bool:
        if self._secclient is None:
            # mock mode: commands are no-ops, as if the module had them all
            return True
        return command in self._client.modules[self._module]["commands"]

    @property
    def _get_data(self) -> Command | None:
        """The module's 'get_data' command, if it has one."""
        command = getattr(self, "get_data", None)
        return command if isinstance(command, Command) else None

    @property
    def _has_get_data(self) -> bool:
        return self._get_data is not None

    async def _exec(self, command: str) -> None:
        if self._secclient is not None:
            await self._secclient.exec_command(self._module, command)

    async def _wait_while_busy(self, action: str) -> None:
        """Wait until the module has left BUSY, raise if it is in ERROR or
        DISABLED then."""
        stat_code = await _wait_while_busy(
            self.status_0, cast(str, self._module), None, self._secclient
        )
        if _is_failed(stat_code):
            self._success = False
            text = ""
            if isinstance(status_1 := getattr(self, "status_1", None), SignalR):
                text = f": {await status_1.get_value()}"
            self._logger.error(f"Module {self.name} --> ERROR/DISABLED{text}")
            raise RuntimeError(
                f"{action} of {self.name} failed: module entered ERROR/DISABLED "
                f"state{text}"
            )

    # --- acquisition cycle -----------------------------------------------

    async def _prepare_cycle(self, value: AcquisitionGoal | None) -> None:
        if value is not None:
            if value.goal is not None:
                goal = getattr(self, "goal", None)
                if not isinstance(goal, SignalW):
                    raise ValueError(f"{self.name} has no writable 'goal' parameter")
                await goal.set(value.goal)

            if value.enable is not None:
                goal_enable = getattr(self, "goal_enable", None)
                if isinstance(goal_enable, SignalW):
                    await goal_enable.set(value.enable)
                elif not value.enable:
                    raise ValueError(
                        f"{self.name} has no writable 'goal_enable' parameter: "
                        f"its goal is always active"
                    )

        if self._has_command("prepare"):
            self._logger.info(f"Preparing {self.name}")
            await self._exec("prepare")
            await self._wait_while_busy("Preparing")

    async def _start_cycle(self, buffer: bool) -> None:
        """Send 'go'. With ``buffer``, buffer the updates of 'value' from now
        on (unless the module has 'get_data')."""
        self._logger.info(f"Starting an acquisition cycle of {self.name}")
        self._success = True
        self._stopped = False
        self._data_collected = False
        if buffer and not self._has_get_data:
            self._start_buffering()
        self._acquiring = True
        try:
            await self.go.execute()
        except BaseException:
            self._acquiring = False
            self._stop_buffering()
            raise

    async def _finish_cycle(self) -> None:
        """Wait until the cycle has ended, then fetch the final 'value'."""
        try:
            await self._wait_while_busy("Acquisition")
            # the update of the final value may only arrive after the status
            # update: ask for it, so read() and the buffer have the result
            if self._secclient is not None:
                await self._secclient.get_parameter(
                    self._module, "value", trycache=False
                )
            self._logger.info(f"Acquisition cycle of {self.name} done")
        finally:
            self._acquiring = False
            self._stop_buffering()

    async def _abort_cycle(self) -> None:
        """Stop a cycle that trigger()/kickoff() started and is still
        running."""
        self._stop_buffering()
        if self._acquiring:
            await self.stop()

    def trigger(self) -> AsyncStatus:
        """Run one acquisition cycle: send 'go' and wait until it has ended."""
        return AsyncStatus(self._trigger())

    async def _trigger(self) -> None:
        await self._start_cycle(buffer=False)
        await self._finish_cycle()

    async def stop(self, *, success: bool = False) -> None:
        """End a running cycle with the module's 'stop' command."""
        self._stopped = True
        if not self._has_command("stop"):
            self._logger.warning(f"{self.name} has no 'stop' command")
            return
        self._logger.info(f"Stopping {self.name}")
        await self._exec("stop")

    async def pause(self) -> None:
        """RunEngine pause: stop a cycle that trigger()/kickoff() started. The
        RunEngine resumes by running the plan again from its last checkpoint,
        which starts a new cycle, so a cycle held over the pause would not be
        continued but mix data from before and after the pause."""
        if self._acquiring:
            await self.stop()

    async def resume(self) -> None:
        """RunEngine resume: nothing to do, the plan starts a new cycle."""

    # --- buffering and collecting ----------------------------------------

    def _collected_signals(self) -> list[SignalR]:
        """'value', or its members if it is a struct/tuple: buffered and
        collected together, one row per update."""
        if isinstance(value := getattr(self, "value", None), SignalR):
            return [value]
        return self._split_signals("value")

    def _start_buffering(self) -> None:
        self._stop_buffering()
        for signal in self._collected_signals():
            buffer = self._buffers.setdefault(signal.name, _ReadingBuffer())
            # the reading given at once on subscribing is from before the cycle
            subscribed = [False]

            def on_reading(
                reading: dict[str, Reading],
                buffer: _ReadingBuffer = buffer,
                subscribed: list[bool] = subscribed,
            ) -> None:
                if not subscribed[0]:
                    return
                for update in reading.values():
                    last = buffer.last_timestamp
                    # the reply to an explicit read can repeat an update
                    if last is None or update["timestamp"] > last:
                        buffer.append(update["value"], update["timestamp"])

            signal.subscribe_reading(on_reading)
            subscribed[0] = True
            self._subscriptions.append((signal, on_reading))

    def _stop_buffering(self) -> None:
        for signal, callback in self._subscriptions:
            signal.clear_sub(callback)
        self._subscriptions.clear()

    def _clear_buffers(self) -> None:
        for buffer in self._buffers.values():
            buffer.clear()

    def _get_data_key(self) -> str:
        return f"{self.name}-get_data"

    async def describe_collect(self) -> dict[str, dict[str, DataKey]]:
        """One stream, named after the device: the result of 'get_data', or
        the updates of 'value'."""
        data_keys: dict[str, DataKey] = {}
        if (get_data := self._get_data) is not None:
            data_keys[self._get_data_key()] = await self._get_data_datakey(get_data)
        else:
            for signal in self._collected_signals():
                data_keys.update(await signal.describe())
        return {self.name: data_keys}

    async def _get_data_datakey(self, get_data: Command) -> DataKey:
        backend = getattr(get_data._connector, "_init_backend", None)
        res_type: SECoPdtype | None = getattr(backend, "_res_type", None)
        if self._secclient is None or res_type is None:
            # mock mode: the result datatype is not known
            return DataKey(dtype="array", shape=[None], source=self._get_data_key())
        # the shape of an array result is only known from a result
        result, _ = await self._secclient.exec_command(self._module, "get_data")
        res_type.update_dtype(result)
        source = (
            f"{self._secclient.uri}:{self._secclient.nodename}:{self._module}:get_data"
        )
        return describedict_to_datakey({**res_type.get_datakey(), "source": source})

    async def collect_pages(self) -> AsyncIterator[PartialEventPage]:
        """The data collected since the last call, as one event page."""
        if (get_data := self._get_data) is not None:
            if self._data_collected:
                # the result of this cycle was already handed over
                return
            # collected before its cycle ended: collect it again later
            self._data_collected = not self._acquiring
            result = np.asarray(await get_data.execute())
            now = ttime.time()
            key = self._get_data_key()
            yield PartialEventPage(
                data={key: [result]}, timestamps={key: [now]}, time=[now]
            )
            return

        buffers = [
            self._buffers.get(signal.name) for signal in self._collected_signals()
        ]
        # the members of a struct/tuple 'value' all update together
        count = min((len(buffer) if buffer else 0) for buffer in buffers)
        if count == 0:
            return
        data: dict[str, list[Any]] = {}
        timestamps: dict[str, list[Any]] = {}
        time: list[float] = []
        for signal, buffer in zip(self._collected_signals(), buffers, strict=True):
            assert buffer is not None
            data[signal.name], timestamps[signal.name] = buffer.take(count)
            time = time or timestamps[signal.name]
        yield PartialEventPage(data=data, timestamps=timestamps, time=time)


# The interface class "Triggerable" was the working name of "Acquisition"
SECoPTriggerableDevice = SECoPAcquisitionDevice


@dataclass
class SECoPMovableLogic(MovableLogic[Any]):
    """Move logic for a SECoP "Drivable" module.

    A move is considered complete once the module's status parameter
    leaves BUSY and enters the IDLE range, rather than when readback
    equals setpoint.
    """

    # bound to status_0 (the status code Signal, resolved to its enum member
    # name, e.g. "RAMPING") -- status is SECoP's StatusType ==
    # TupleOf(EnumType, StringType), always decomposed
    status: SignalR
    secclient: AsyncSecopClient
    module: str
    logger: Logger

    async def stop(self) -> None:
        if self.setpoint._mock is not None:
            # mock mode: no SEC node to send 'stop' to
            return
        self.logger.info(f"Stopping {self.module}")
        await self.secclient.exec_command(self.module, "stop")

    async def move(self, new_position: Any, timeout: TimeoutCalculator) -> None:
        self.logger.info(f"Moving {self.module} to {new_position}")

        await self.setpoint.set(new_position)

        await _wait_for_move(
            self.status,
            self.module,
            self.logger,
            new_position,
            timeout(),
            None if self.setpoint._mock is not None else self.secclient,
        )


class SECoPWritableBase(SECoPReadableDevice):
    """Common base of all device classes for SECoP modules with a 'target':
    the interface classes "Writable" and "Drivable"."""

    hinted_signals: list[str] = ["target", "value"]
    mandatory_parameters: list[str] = SECoPReadableDevice.mandatory_parameters + [
        "target"
    ]

    def _param_signals(self, param: str) -> list[SignalR]:
        """The readable Signal of an atomic parameter, or the split member
        Signals of a decomposed struct/tuple parameter."""
        signal = getattr(self, param, None)
        if isinstance(signal, SignalR):
            return [signal]
        return self._split_signals(param)

    async def _assign_interface_formats(self):
        await super()._assign_interface_formats()

        self._assign_hinted_format("target", self._param_signals("target"))


class SECoPDrivableBase(SECoPWritableBase):
    """Common base of the device classes for SECoP modules with the interface
    class "Drivable": `SECoPMoveableDevice` and `SECoPCompositeMoveableDevice`.
    Which of the two a module gets depends on the datatypes of its 'target'
    and 'value' parameters, see `class_from_interface`."""


class SECoPMoveableDevice(SECoPDrivableBase, StandardMovable[SignalDatatypeT]):
    """
    Standard movable SECoP device, corresponding to a SECoP module with the
    interface class "Drivable" whose 'target' and 'value' are atomic (scalar
    or array). A Drivable with a struct/tuple 'target' or 'value' is a
    `SECoPCompositeMoveableDevice`.
    """

    _mock_class: type[DeviceMock] = SECoPMovableMock

    def __init__(
        self,
        sri: str = "",  # SECoP resource identifier host:port:optional[module]
        name: str = "",
        connector: SECoPDeviceConnector | None = None,
        loglevel=logging.INFO,
        logdir: str | None = None,
    ):
        """Initialize SECoPMovableDevice

        :param secclient: SECoP client providing communication to the SEC Node
        :type secclient: AsyncSecopClient
        :param module_name: ame of the SEC Node module that is represented by
            this device
        :type module_name: str
        """

        self.target: SignalRW

        super().__init__(
            sri=sri, name=name, connector=connector, loglevel=loglevel, logdir=logdir
        )

    @cached_property
    def movable_logic(self) -> MovableLogic:
        if self._module is None:
            raise RuntimeError(f"{self.name}: device is not bound to a SECoP module")

        return SECoPMovableLogic(
            setpoint=self.target,
            readback=self.value,
            status=self.status_0,
            secclient=self._client,
            module=self._module,
            logger=self._logger,
        )

    def set_name(self, name: str, *, child_name_separator: str | None = None) -> None:
        # set_name() can run several times before movable_logic is actually
        # resolvable: once before connect() (e.g. init_devices() naming devices
        # up front), and again mid-connect whenever a sibling/parent signal is
        # filled in (DeviceFiller.fill_child_signal() -> _set_device_child()
        # triggers a renaming cascade down the whole tree) -- including a
        # rename of *this* device as a not-yet-connected child, triggered by
        # a *sibling* module being filled into the parent node, well before
        # this device's own connect_real() has run at all. StandardMovable's
        # set_name() needs '_module' (set by set_module(), early in the
        # parent node's connect_real()) and 'target', 'value' and 'status_0'
        # (only created once this device's own connect_real() fills its
        # signals, in the order the node describes them) to resolve
        # movable_logic, so skip it and fall back to plain Device.set_name()
        # until all of them are present; a later call does the real renaming.
        if self._module is None or not all(
            hasattr(self, signal) for signal in ("target", "value", "status_0")
        ):
            Device.set_name(self, name, child_name_separator=child_name_separator)
            return
        super().set_name(name, child_name_separator=child_name_separator)


def _secop_to_python(datatype: DataType, value: Any) -> Any:
    """Convert a value as delivered by the SECoP client into the form the
    Signals deliver it in. A struct becomes a dict and a tuple a tuple, with
    each member converted like the value of its split member Signal."""
    if isinstance(datatype, StructOf):
        return {
            key: _secop_to_python(member, value[key])
            for key, member in datatype.members.items()
            if key in value
        }

    if isinstance(datatype, TupleOf):
        return tuple(
            _secop_to_python(member, member_value)
            for member, member_value in zip(datatype.members, value, strict=True)
        )

    secop_dtype = SECoPdtype(datatype)
    secop_dtype.update_dtype(value)
    return secop_dtype.secop2val(value)


def _python_to_secop(
    datatype: DataType, value: Any, optional: Sequence[str] = ()
) -> Any:
    """Validate a value given to set() against the SECoP datatype and return
    it in the form the SECoP client expects. ``optional`` are the struct
    members that may be left out."""
    if isinstance(datatype, StructOf):
        if not isinstance(value, Mapping):
            raise TypeError(
                f"expected a mapping with the members {list(datatype.members)}"
            )
        # frappy's client-side StructOf accepts any member being left out, and
        # drops members whose value is None
        missing = [
            key
            for key in datatype.members
            if value.get(key) is None and key not in optional
        ]
        if missing:
            raise ValueError(f"missing struct members {missing}")
        # validate(), not __call__(): only validate() checks the limits
        return datatype.validate(dict(value))

    if isinstance(datatype, TupleOf):
        if isinstance(value, np.ndarray) and value.ndim == 1:
            value = value.tolist()
        if isinstance(value, (str, bytes, Mapping)) or not isinstance(value, Sequence):
            raise TypeError(f"expected a sequence of {len(datatype.members)} members")
        return datatype.validate(tuple(value))

    return datatype.validate(value)


T = TypeVar("T")


class SECoPWritableDevice(SECoPWritableBase, Locatable[T], Checkable[T], Generic[T]):
    """
    Settable SECoP device, corresponding to a SECoP module with the interface
    class "Writable": a module with a 'target' but without a 'stop' command.

    ``set()`` writes 'target' and waits until the module is not BUSY. For a
    module whose value follows the target at once that is immediately; a
    Writable that needs time to reach its target reports BUSY meanwhile. The
    module must report BUSY by the time it answers the target write, or
    ``set()`` ends at once: SECoP has no other sign that a move has started.
    ``locate()`` returns the setpoint ('target') and readback ('value').

    'target' and 'value' may be of any SECoP datatype. For a struct or tuple,
    the position is one value in ``set()`` and ``locate()``, but several data
    keys in ``read()`` and ``describe()``, because an event can only hold
    scalars and arrays:

    * ``set()`` takes the whole target: a dict for a struct, a tuple for a
      tuple. ``locate()`` returns setpoint and readback in the same form.
    * ``read()`` has one key per member, from the read-only Signals
      ``target_<member>`` and ``value_<member>`` (``_0``, ``_1``, ... for a
      tuple). ``locate()["readback"]["x"]`` is the value of ``value_x``.

    ``set()`` is the only way to change a struct/tuple target: there is no
    writable 'target' Signal for it. An atomic 'target' is also a ``SignalRW``,
    whose ``set()`` only writes the parameter, without waiting.

    The type parameter is the type of the target, e.g. ``float``, or a
    ``TypedDict`` in a generated device class.
    """

    _mock_class: type[DeviceMock] = SECoPWritableMock

    def _description(self, param: str) -> dict[str, Any]:
        if self._module is None:
            raise RuntimeError(f"{self.name}: device is not bound to a SECoP module")
        return self._client.modules[self._module]["parameters"][param]

    def _datatype(self, param: str) -> DataType:
        return self._description(param)["datatype"]

    def _target_to_secop(self, value: T) -> Any:
        target = self._description("target")
        try:
            return _python_to_secop(
                target["datatype"],
                value,
                optional_struct_members(target.get("datainfo", {})),
            )
        except (SECoPError, TypeError, ValueError) as exc:
            raise ValueError(
                f"{self.name}: {value!r} is not a valid target: {exc}"
            ) from exc

    async def check_value(self, value: T) -> None:
        """Check that ``value`` is a valid target, without moving. In mock mode
        there is no node description to check against, so every value passes."""
        if self._mock is None:
            self._target_to_secop(value)

    @AsyncStatus.wrap
    async def set(self, value: T, timeout: float | None = None) -> None:
        """Set the target and wait until the module is not BUSY."""
        await self._move(value, timeout)

    async def _move(self, value: T, timeout: float | None) -> None:
        if self._mock is not None:
            self._mock_move(value)
            return

        assert self._module is not None
        secop_value = self._target_to_secop(value)

        self._logger.info(f"Moving {self._module} to {value}")
        # the timeout covers writing the target as well as waiting
        deadline = None if timeout is None else ttime.monotonic() + timeout
        try:
            async with asyncio.timeout(timeout):
                await self._client.set_parameter(self._module, "target", secop_value)
        except TimeoutError as exc:
            raise TimeoutError(
                f"{self.name}: writing the target {value!r} took longer than "
                f"{timeout} s"
            ) from exc
        await _wait_for_move(
            self.status_0,
            self._module,
            self._logger,
            value,
            _remaining(deadline),
            self._client,
        )

    def _mock_move(self, value: Any) -> None:
        """Arrive instantly: set the mocked 'target' and 'value' Signals to
        ``value``, as far as they have the same form as 'target'."""
        if isinstance(value, np.ndarray) and value.ndim == 1:
            # accepted as a tuple target in real mode too
            value = value.tolist()
        composite_target = not isinstance(getattr(self, "target", None), SignalR)
        for param in ("target", "value"):
            signal = getattr(self, param, None)
            if isinstance(signal, SignalR):
                # an atomic parameter: takes the target only if that is atomic
                # too (a struct target says nothing about a scalar value)
                if not composite_target:
                    set_mock_value(signal, value)
                continue

            for key, signal in self._split_members(param).items():
                if isinstance(value, Mapping):
                    if key in value:
                        set_mock_value(signal, value[key])
                elif isinstance(value, (tuple, list)):
                    if key.isdigit() and int(key) < len(value):
                        set_mock_value(signal, value[int(key)])

    async def _mock_position(self, param: str) -> Any:
        signal = getattr(self, param, None)
        if isinstance(signal, SignalR):
            return await signal.get_value()

        members = self._split_members(param)
        values = await asyncio.gather(*(sig.get_value() for sig in members.values()))
        position = dict(zip(members, values, strict=True))
        if all(key.isdigit() for key in position):
            return tuple(position[key] for key in sorted(position, key=int))
        return position

    async def locate(self) -> Location[T]:
        """Return the current setpoint ('target') and readback ('value')."""
        if self._mock is not None:
            setpoint, readback = await asyncio.gather(
                self._mock_position("target"), self._mock_position("value")
            )
            return Location(setpoint=setpoint, readback=readback)

        assert self._module is not None
        # no single Signal holds a whole struct/tuple: read both parameters
        # straight from the SEC node
        target, value = await asyncio.gather(
            self._client.get_parameter(self._module, "target", trycache=True),
            self._client.get_parameter(self._module, "value", trycache=True),
        )
        for entry in (target, value):
            if entry.readerror is not None:
                raise entry.readerror

        return Location(
            setpoint=_secop_to_python(self._datatype("target"), target.value),
            readback=_secop_to_python(self._datatype("value"), value.value),
        )


class SECoPCompositeMoveableDevice(
    SECoPDrivableBase, SECoPWritableDevice[T], Stoppable
):
    """
    Movable SECoP device, corresponding to a SECoP module with the interface
    class "Drivable" whose 'target' and/or 'value' is a struct or tuple.

    Behaves like `SECoPWritableDevice` for a struct/tuple, and can be stopped:
    ``set()`` takes the whole target as a dict or tuple and is the only way to
    change it, ``locate()`` returns setpoint and readback in the same form,
    and ``read()`` has one data key per member. A move reports no progress
    updates.
    """

    def __init__(
        self,
        sri: str = "",  # SECoP resource identifier host:port:optional[module]
        name: str = "",
        connector: SECoPDeviceConnector | None = None,
        loglevel=logging.INFO,
        logdir: str | None = None,
    ):
        # None: not stopped; else the 'success' argument of the last stop()
        self._stop_success: bool | None = None
        self._move_task: asyncio.Task | None = None

        super().__init__(
            sri=sri, name=name, connector=connector, loglevel=loglevel, logdir=logdir
        )

    @AsyncStatus.wrap
    async def set(self, value: T, timeout: float | None = None) -> None:
        """Move to the given target and wait until the module has left BUSY."""
        self._stop_success = None
        self._move_task = asyncio.create_task(self._move(value, timeout))
        try:
            await self._move_task
        except asyncio.CancelledError:
            # cancelled by stop(): how the move ends is decided below.
            # Otherwise (e.g. the plan was aborted) pass the cancellation on.
            if self._stop_success is None:
                raise
        finally:
            self._move_task = None

        stop_success, self._stop_success = self._stop_success, None
        if stop_success is False:
            raise RuntimeError(f"Device {self.name} was stopped.")

    async def stop(self, success: bool = False) -> None:
        """Stop the module and end a running ``set()``: successfully if
        ``success``, else with a RuntimeError."""
        self._stop_success = success
        if self._mock is None:
            self._logger.info(f"Stopping {self._module}")
            try:
                await self._client.exec_command(self._module, "stop")
            except Exception:
                # not stopped: a running set() ends as it would have
                self._stop_success = None
                raise
        if self._move_task is not None:
            self._move_task.cancel()


def _composite_target_params(parameters: Mapping[str, Any]) -> list[str]:
    """Which of 'target' and 'value' are struct/tuple parameters."""
    return [
        param
        for param in ("target", "value")
        if param in parameters
        and classify_datatype(parameters[param]["datatype"])
        is CompositeKind.DECOMPOSABLE
    ]


def _check_drivable_class(
    device: Device,
    module: str,
    parameters: Mapping[str, Any],
    commands: Mapping[str, Any],
) -> None:
    """A Drivable's device class depends on the datatypes of 'target' and
    'value', which a directly instantiated device cannot know in advance."""
    composite = _composite_target_params(parameters)

    if isinstance(device, SECoPCompositeMoveableDevice) and "stop" not in commands:
        raise TypeError(
            f"Module '{module}' has no 'stop' command, which "
            f"{SECoPCompositeMoveableDevice.__name__} needs: use "
            f"{SECoPWritableDevice.__name__} for this module"
        )

    if isinstance(device, SECoPMoveableDevice) and composite:
        raise TypeError(
            f"{composite} of module '{module}' is a struct/tuple, which "
            f"{SECoPMoveableDevice.__name__} does not support: use "
            f"{SECoPCompositeMoveableDevice.__name__} for this module "
            f"(regenerate the device classes if this one was generated)"
        )

    if (
        isinstance(device, SECoPWritableDevice)
        and "target" in composite
        and "target" in dict(device.children())
    ):
        # declared by a device class generated before struct/tuple targets
        # became set()-only
        raise TypeError(
            f"'target' of module '{module}' is a struct/tuple and only changed "
            f"through set(), but {type(device).__name__} declares a 'target' "
            f"signal: regenerate the device classes"
        )

    if isinstance(device, SECoPCompositeMoveableDevice) and not composite:
        raise TypeError(
            f"Neither 'target' nor 'value' of module '{module}' is a "
            f"struct/tuple: use {SECoPMoveableDevice.__name__} for this module"
        )


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


def _status_code(status_sig: SignalR, status: str) -> int:
    secop_dtype = getattr(status_sig._connector._init_backend, "SECoPdtype_obj", None)
    if secop_dtype is not None:
        return secop_dtype.export_value(status)

    # no node description (mock mode): only standard status names are known
    status_name = getattr(status, "value", status)
    if status_name not in STATUS_CODES:
        raise ValueError(
            f"Unknown status '{status_name}' of {status_sig.name}: without a SEC "
            f"node description only the standard SECoP status names are known"
        )
    return STATUS_CODES[status_name]


IF_CLASSES = {
    "Acquisition": SECoPAcquisitionDevice,
    "Triggerable": SECoPAcquisitionDevice,
    "Drivable": SECoPMoveableDevice,
    "Writable": SECoPWritableDevice,
    "Readable": SECoPReadableDevice,
    "Communicator": SECoPCommunicatorDevice,
}
