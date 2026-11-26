"""The connector that builds a device from the SEC node description, and
helpers that read the description and name the children."""

import logging
import re
import warnings
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from frappy.datatypes import CommandType
from ophyd_async.core import (
    Command,
    Device,
    DeviceConnector,
    DeviceFiller,
    LazyMock,
    SignalR,
    SignalRW,
    SignalW,
    TriggerableCommand,
)
from ophyd_async.core._device import DEVICE_RESERVED_ATTRS

from secop_ophyd.AsyncFrappyClient import AsyncSecopClient
from secop_ophyd.datatypes.composite import (
    MAX_DEPTH,
    CompositeKind,
    IncompatibleSECoPDatatype,
    classify_datatype,
    get_composite_members,
)
from secop_ophyd.datatypes.properties import get_type_prop, module_property_signals
from secop_ophyd.datatypes.secop_dtype import SECoPdtype, get_type_param
from secop_ophyd.propertykeys import ACQUISITION_CHANNELS, INTERFACE_CLASSES
from secop_ophyd.signals.backend import SECoPBackend
from secop_ophyd.signals.command import SECoPCommandBackend, SECoPCommandConnector
from secop_ophyd.util import Path

# The device classes import this module: the connector imports them where it
# uses them, at connect time.
if TYPE_CHECKING:
    from secop_ophyd.devices.acquisition.control import SECoPAcquisitionControllerDevice
    from secop_ophyd.devices.base import SECoPDevice


def clean_identifier(anystring):
    return str(re.sub(r"\W+|^(?=\d)", "_", anystring))


def command_attr_name(command: str) -> str:
    """Attribute name of a SECoP command on its device: with a trailing
    underscore if the name is used by a bluesky protocol (e.g. 'prepare')."""
    return f"{command}_" if command in DEVICE_RESERVED_ATTRS else command


def channel_attr_name(role: str, device: Device, declared: set[str]) -> str:
    """Attribute name of the channel with the role ``role`` on its controller
    ``device``: the role as a Python identifier, with trailing underscores if
    it is used by a bluesky protocol or another attribute of the controller.
    ``declared`` are the annotated (not yet filled) children of ``device``."""
    name = clean_identifier(role)
    if name in declared:
        return name
    attr_name = name
    while attr_name in DEVICE_RESERVED_ATTRS or hasattr(device, attr_name):
        attr_name += "_"
        if attr_name in declared:
            break
    if attr_name != name:
        warnings.warn(
            f"Channel role '{role}' of {device.name} is also an attribute of the "
            f"controller: its channel device is '{attr_name}'"
        )
    return attr_name


IGNORED_PROPS = ["_plotly"]


def acquisition_channels(module_properties: Mapping[str, Any]) -> dict[str, str]:
    """Role -> module name of the channels of an acquisition controller, from
    its module properties (empty for any other module)."""
    if "AcquisitionController" not in module_properties.get(INTERFACE_CLASSES, []):
        return {}
    return dict(module_properties.get(ACQUISITION_CHANNELS) or {})


def controller_channel_modules(modules: Mapping[str, Any]) -> set[str]:
    """The modules of a node that are channels of an acquisition controller."""
    return {
        channel
        for module in modules.values()
        for channel in acquisition_channels(module["properties"]).values()
    }


def _composite_target_params(parameters: Mapping[str, Any]) -> list[str]:
    """Which of 'target' and 'value' are struct/tuple parameters."""
    return [
        param
        for param in ("target", "value")
        if param in parameters
        and classify_datatype(parameters[param]["datatype"])
        is CompositeKind.DECOMPOSABLE
    ]


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

        from secop_ophyd.devices.base import SECoPDevice

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
        from secop_ophyd.devices.base import SECoPDevice

        if hasattr(self, "filler"):
            return

        self.filler = DeviceFiller(
            device=device,
            signal_backend_factory=SECoPBackend[Any],
            # child devices are modules of the node (the modules of a node, the
            # channels of an acquisition controller)
            device_connector_factory=lambda: SECoPDeviceConnector(
                self.node_id, self._auto_fill_signals, self.loglevel, self.logdir
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
            elif isinstance(child, SECoPDevice) and child._module is None:
                # an annotated module device knows its module from its
                # attribute name; needed for mock mode, where no node
                # description assigns it. A channel of an acquisition
                # controller got its module from its annotation.
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

        from secop_ophyd.devices.acquisition.control import (
            SECoPAcquisitionControllerDevice,
        )
        from secop_ophyd.devices.drivable import _check_drivable_class
        from secop_ophyd.devices.writable import SECoPWritableDevice

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

        if isinstance(device, SECoPAcquisitionControllerDevice):
            self._fill_channels(device, not_filled)

    def _fill_channels(
        self, device: "SECoPAcquisitionControllerDevice", not_filled: set[str]
    ) -> None:
        """Fill the channels of an acquisition controller as its child
        devices, named by their role."""
        from secop_ophyd.devices.node import class_from_interface

        assert self.module is not None
        roles = acquisition_channels(self.client.modules[self.module]["properties"])

        for role, channel_module in roles.items():
            if channel_module not in self.client.modules:
                raise RuntimeError(
                    f"Channel '{channel_module}' (role '{role}') of controller "
                    f"'{self.module}' is not provided by SEC node {self.node_id}"
                )
            attr_name = channel_attr_name(role, device, not_filled)
            declared = attr_name in not_filled
            if not (self._auto_fill_signals or declared):
                continue
            module = self.client.modules[channel_module]
            channel_class = class_from_interface(
                module["properties"], module["parameters"]
            )
            # a declared channel is looked up by its name without trailing
            # underscores, a new one is created under its attribute name
            self.filler.fill_child_device(
                attr_name.rstrip("_") if declared else attr_name, channel_class
            )
            channel: SECoPDevice = getattr(device, attr_name)
            channel.set_module(channel_module)
            channel._acquisition_role = role

    def _fill_node(self, device: Device):
        """Fill module devices and node properties from the node description."""
        from secop_ophyd.devices.node import class_from_interface

        # Fill Module devices
        modules = self.client.modules

        not_filled = {unfilled for unfilled, _ in device.children()}

        # the channels of acquisition controllers are child devices of their
        # controller, not module devices of the node
        channel_modules = controller_channel_modules(modules)

        for module_name in modules.keys():
            if module_name in channel_modules:
                continue
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
        from secop_ophyd.devices.base import SECoPDevice

        if self._formats_assigned or not isinstance(device, SECoPDevice):
            return

        await device._assign_interface_formats()
        await device._assign_default_formats()
        self._formats_assigned = True
