import logging
import re
import time as ttime
import warnings
from abc import abstractmethod
from dataclasses import dataclass
from functools import cached_property
from logging import Logger
from typing import Any, Dict

import bluesky.plan_stubs as bps
from bluesky.protocols import (
    Reading,
    Stoppable,
    Subscribable,
    Triggerable,
)
from frappy.datatypes import CommandType
from ophyd_async.core import (
    DEFAULT_TIMEOUT,
    AsyncStatus,
    Command,
    Device,
    DeviceConnector,
    DeviceFiller,
    DeviceMock,
    LazyMock,
    MovableLogic,
    Signal,
    SignalR,
    SignalRW,
    StandardMovable,
    StandardReadable,
    StandardReadableFormat,
    TimeoutCalculator,
    TriggerableCommand,
    observe_value,
    wait_for_value,
)
from ophyd_async.core._utils import Callback

from secop_ophyd.AsyncFrappyClient import AsyncFrappyClient
from secop_ophyd.logs import setup_logging
from secop_ophyd.propertykeys import EQUIPMENT_ID, INTERFACE_CLASSES
from secop_ophyd.SECoPSignal import (
    AttributeType,
    SECoPBackend,
    SECoPCommandBackend,
)
from secop_ophyd.util import Path

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


IGNORED_PROPS = ["meaning", "_plotly"]


def clean_identifier(anystring):
    return str(re.sub(r"\W+|^(?=\d)", "_", anystring))


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
        backend._secclient = parent._client


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
        backend._secclient = parent._client


class SECoPDeviceConnector(DeviceConnector):

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

        if sri.count(":") == 2:
            self.module = sri.split(":")[2]
        elif sri.count(":") == 1:
            self.module = None
        else:
            raise RuntimeError(f"Invalid SECoP resource identifier: {sri}")

        if SECoPDevice._clients.get(self.node_id) is None:
            raise RuntimeError(f"No AsyncFrappyClient for URI {sri} exists")

        self.client: AsyncFrappyClient = SECoPDevice._clients[self.node_id]

    def set_module(self, module_name: str):
        if self.sri.count(":") != 1:
            raise RuntimeError(
                "Module can only be set if SRI does not already contain module"
            )
        self.module = module_name
        self.sri = self.sri + ":" + module_name

    def create_children_from_annotations(self, device: Device):
        if not hasattr(self, "filler"):
            self.filler = DeviceFiller(
                device=device,
                signal_backend_factory=SECoPBackend,
                device_connector_factory=lambda: SECoPDeviceConnector(
                    self.sri, self._auto_fill_signals, self.loglevel, self.logdir
                ),
                command_backend_factory=SECoPCommandBackend,
            )

        list(self.filler.create_signals_from_annotations())
        list(self.filler.create_devices_from_annotations(filled=False))
        list(self.filler.create_commands_from_annotations(filled=False))

        self.filler.check_created()

    def fill_backend_with_path(self, backend: SECoPBackend, annotations: list[Any]):
        unhandled = []
        while annotations:
            annotation = annotations.pop(0)

            if isinstance(annotation, StandardReadableFormat):
                backend.format = annotation

            else:
                unhandled.append(annotation)

        annotations.extend(unhandled)
        # These leftover annotations will now be handled by the iterator

    async def connect_mock(self, device: Device, mock: LazyMock):
        # Make 2 entries for each DeviceVector
        self.filler.create_device_vector_entries_to_mock(2)
        # Set the name of the device to name all children
        device.set_name(device.name)
        await super().connect_mock(device, mock)

    async def connect_real(self, device: Device, timeout: float, force_reconnect: bool):
        if not self.sri:
            raise RuntimeError(f"Could not connect to SEC node: {self.sri}")

        # Establish connection to SEC Node
        await self.client.connect(3)

        # Module Device: fill Parameters & Pproperties
        # (commands are done via annotated plans)
        if self.module:

            # Fill Parmeters
            parameter_dict = self.client.modules[self.module]["parameters"]
            # remove ignored signals
            parameters = [
                child
                for child in parameter_dict.keys()
                if child not in self.filler.ignored_signals
            ]

            # Dertermine children that are declared but not yet filled
            not_filled = {unfilled for unfilled, _ in device.children()}

            for param_name in parameters:
                if self._auto_fill_signals or param_name in not_filled:
                    signal_type = (
                        SignalR if parameter_dict[param_name]["readonly"] else SignalRW
                    )

                    backend = self.filler.fill_child_signal(param_name, signal_type)

                    from secop_ophyd.GenNodeCode import get_type_param

                    datatype = get_type_param(parameter_dict[param_name]["datatype"])
                    backend.init_parameter_from_introspection(
                        datatype=datatype,
                        path=self.module + ":" + param_name,
                        secclient=self.client,
                    )

            # Fill Properties
            module_property_dict = self.client.modules[self.module]["properties"]

            # remove ignored signals
            module_properties = [
                child
                for child in module_property_dict.keys()
                if child not in self.filler.ignored_signals
                and child not in IGNORED_PROPS
            ]

            for mod_property_name in module_properties:
                if self._auto_fill_signals or mod_property_name in not_filled:

                    # properties are always read only
                    backend = self.filler.fill_child_signal(mod_property_name, SignalR)

                    from secop_ophyd.GenNodeCode import get_type_prop

                    datatype = get_type_prop(module_property_dict[mod_property_name])

                    backend.init_property_from_introspection(
                        datatype=datatype,
                        path=self.module + ":" + mod_property_name,
                        secclient=self.client,
                    )

            # Fill Commands
            command_dict = self.client.modules[self.module]["commands"]

            # "stop" is skipped: SECoPMoveableDevice already implements
            # Stoppable.stop() (a real bound method) via StandardMovable, so
            # exposing a raw command device at the same bare name would
            # silently shadow it.
            commands = [
                c
                for c in command_dict.keys()
                if c not in self.filler.ignored_signals and c != "stop"
            ]

            wait_for_idle_fn = getattr(device, "wait_for_idle", None)

            for command_name in commands:
                if self._auto_fill_signals or command_name in not_filled:
                    cmd_datatype: CommandType = command_dict[command_name]["datatype"]
                    command_type = (
                        TriggerableCommand
                        if cmd_datatype.argument is None and cmd_datatype.result is None
                        else Command
                    )

                    backend = self.filler.fill_child_command(command_name, command_type)

                    cmd_path = Path(
                        parameter_name=command_name, module_name=self.module
                    )
                    backend.init_command_from_introspection(
                        cmd_datatype, cmd_path, self.client, wait_for_idle_fn
                    )

        # Node Device: fill child devices (modules)
        else:

            # Fill Module devices
            modules = self.client.modules

            not_filled = {unfilled for unfilled, _ in device.children()}

            for module_name in modules.keys():
                if self._auto_fill_signals or module_name in not_filled:
                    module_properties = modules[module_name]["properties"]
                    device_sub_class = class_from_interface(module_properties)

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

                    from secop_ophyd.GenNodeCode import get_type_prop

                    datatype = get_type_prop(node_property_dict[node_property_name])

                    backend.init_property_from_introspection(
                        datatype=datatype,
                        path=node_property_name,
                        secclient=self.client,
                    )

        self.filler.check_filled(f"{self.node_id}")

        # Set the name of the device to name all children
        device.set_name(device.name)
        await super().connect_real(device, timeout, force_reconnect)

        # All Signals and child devs should be filled and connected now, in the next
        # all signals and child devices need to be added to the according
        # StandardReadableFormat with the hierarchiy:
        # 1. Format given in Annotation
        #       --> these will already have been set by the DeviceFiller
        # 2. Module Interface Class definition (value, target,...)
        #       --> these are set at the end of a .connect() method of the according
        #           SECoPDevice subclass skipping any signals that have already been
        #           set by annotations (should emit warning if there is a conflict
        #           config vs read sig)
        # 3. & 4. Definition in Parameter property "_signal_format" + Defaults
        #   - _signal_format property + default CONFIG_SIGNAL for all other Signals
        #       --> these are set here at the end of SECoPDeviceConnector.connect_real()
        #           for all Signals that have not yet been set to a format
        #   - CHILD format for all child devices (SECoPDevice instances)
        #       --> these are set at the end of SECoPNodeDevice.connect() method
        #           the device tree has only a depth of 2 levels (Node -> Modules)
        #

        # device has to be standard readable for this to make sense
        if not isinstance(device, SECoPDevice):
            return

        # 2. Module Interface Class definition (value, target,...)
        await device._assign_interface_formats()

        # 3. & 4. Definition in Parameter property "_signal_format" + Defaults
        await device._assign_default_formats()


class SECoPDevice(StandardReadable):

    _clients: Dict[str, AsyncFrappyClient] = {}

    _node_id: str
    _sri: str
    _host: str
    _port: str
    _module: str | None
    _mod_prop_devices: Dict[str, SignalR]
    _param_devices: Dict[str, Any]
    _logger: Logger

    hinted_signals: list[str] = []

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
        self._mod_prop_devices = {}
        self._param_devices = {}
        self._node_id = sri.split(":")[0] + ":" + sri.split(":")[1]

        self._logger = setup_logging(
            name=f"frappy:{self._host}:{self._port}",
            level=loglevel,
            log_dir=logdir,
        )

        self._module = None
        if len(sri.split(":")) > 2:
            self._module = sri.split(":")[2]

        if SECoPDevice._clients.get(self._node_id) is None:
            SECoPDevice._clients[self._node_id] = AsyncFrappyClient(
                host=self._host, port=self._port, log=self._logger
            )

        connector = connector or SECoPDeviceConnector(sri=sri)

        self._client: AsyncFrappyClient = SECoPDevice._clients[self._node_id]

        super().__init__(name=name, connector=connector)

    def set_module(self, module_name: str):
        if self._module is not None:
            raise RuntimeError("Module can only be set if it was not already set")

        self._module = module_name
        self._sri = self._sri + ":" + module_name

        self._connector.set_module(module_name)

    async def connect(
        self,
        mock: bool | LazyMock = False,
        timeout: float = DEFAULT_TIMEOUT,
        force_reconnect: bool = False,
    ):
        if not self._client.online or force_reconnect:
            # Establish connection to SEC Node
            await self._client.connect(3)

        await super().connect(mock, timeout, force_reconnect)

        if self._module is None:
            # set device name from equipment id property
            self.set_name(self._client.properties[EQUIPMENT_ID].replace(".", "-"))
        else:
            self.set_name(self._module)

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

            backend = child._connector.backend

            if not isinstance(backend, SECoPBackend):
                continue

            param_name = backend.path_str.split(":")[-1]
            if param_name == "status":
                # status signals should not be assigned a format,
                # but a SignalR children (this can be removed once tiled can
                # hanlde composite dtypes)
                continue

            # child is a Signal with SECoPParamBackend

            # check if signal already has a format assigned
            signalr_device = assert_device_is_signalr(child)

            if format_assigned(self, signalr_device):
                # format already assigned by annotation or module IF class
                continue

            match backend.format:
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

        super().__init__(sri=sec_node_uri, name=name, loglevel=loglevel, logdir=logdir)

    async def connect(self, mock=False, timeout=DEFAULT_TIMEOUT, force_reconnect=False):
        await super().connect(mock, timeout, force_reconnect)

        moddevs = []
        for _, moddev in self.children():
            if isinstance(moddev, SECoPDevice):
                moddevs.append(moddev)

        self.add_readables(moddevs, StandardReadableFormat.CHILD)

        # register secclient callbacks (these are useful if sec node description
        # changes after a reconnect)
        self._client.register_callback(
            None, self.descriptiveDataChange, self.nodeStateChange
        )

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
        # Node device has no specific interface class formats
        pass

    def class_from_instance(self, path_to_module: str | None = None):
        from secop_ophyd.GenNodeCode import GenNodeCode

        description = self._client.client.request("describe")[2]

        # parse genClass file if already present
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


class SECoPReadableDevice(SECoPDevice, Triggerable, Subscribable):
    """
    Standard readable SECoP device, corresponding to a SECoP module with the
    interface class "Readable"
    """

    hinted_signals: list[str] = ["value"]

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
        :type secclient: AsyncFrappyClient
        :param module_name: Name of the SEC Node module that is represented by
            this device
        :type module_name: str
        """

        self.value: SignalR
        self.status: SignalR

        super().__init__(
            sri=sri, name=name, connector=connector, loglevel=loglevel, logdir=logdir
        )

    async def connect(self, mock=False, timeout=DEFAULT_TIMEOUT, force_reconnect=False):
        await super().connect(mock, timeout, force_reconnect)

        if not hasattr(self, "value"):
            raise AttributeError(
                "Attribute 'value' has not been assigned,"
                + "but is needed for Readable interface class"
            )

        if not hasattr(self, "status"):
            raise AttributeError(
                "Attribute 'status' has not been assigned,"
                + "but is needed for Readable interface class"
            )

    async def _assign_interface_formats(self):

        if format_assigned(self, self.value):
            if not is_read_signal(self, self.value):
                warnings.warn(
                    f"Signal 'value' of device {self.name} has format assigned "
                    + "that is not compatible with Readable interface class"
                )
        else:
            self.add_readables([self.value], StandardReadableFormat.HINTED_SIGNAL)

        # TODO ensure status signal must be neither config nor read format

    async def wait_for_idle(self):
        """asynchronously waits until module is IDLE again. this is helpful,
        for running commands that are not done immediately
        """

        self._logger.info(f"Waiting for {self.name} to be IDLE")

        if self.status is None:
            self._logger.error("Status Signal not initialized")
            raise Exception("status Signal not initialized")

        # force reading of fresh status from device
        await self.status.read(False)

        async for current_stat in observe_value(self.status):
            # status is has type Tuple and is therefore transported as
            # structured Numpy array ('f0':statuscode;'f1':status Message)

            stat_code = current_stat["f0"]

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
        async def switch_from_status_inner():
            async for current_stat in observe_value(self.status):
                # status is has type Tuple and is therefore transported as
                # structured Numpy array ('f0':statuscode;'f1':status Message)

                stat_code = current_stat["f0"]

                if monitored_status_code != stat_code:
                    break

        def switch_from_status_factory():
            return switch_from_status_inner()

        yield from bps.wait_for([switch_from_status_factory])

    def trigger(self) -> AsyncStatus:
        self._logger.info(f"Triggering {self.name}: read fresh data from device")
        # get fresh reading of the value Parameter from the SEC Node
        return AsyncStatus(
            awaitable=self._client.get_parameter(self._module, "value", trycache=False)
        )

    def subscribe(self, function: Callback[dict[str, Reading]]) -> None:
        """Subscribe to updates in the reading"""
        self.value.subscribe(function=function)

    def clear_sub(self, function: Callback) -> None:
        """Remove a subscription."""
        self.value.clear_sub(function=function)


class SECoPTriggerableDevice(SECoPReadableDevice, Stoppable):
    """
    Standard triggerable SECoP device, corresponding to a SECoP module with the0s
    interface class "Triggerable"
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
        """Initialize SECoPTriggerableDevice

        :param secclient: SECoP client providing communication to the SEC Node
        :type secclient: AsyncFrappyClient
        :param module_name: ame of the SEC Node module that is represented by
            this device
        :type module_name: str
        """

        self._success = True
        self._stopped = False

        super().__init__(
            sri=sri, name=name, connector=connector, loglevel=loglevel, logdir=logdir
        )


class SECoPWritableDevice(SECoPReadableDevice):
    hinted_signals: list[str] = ["target", "value"]

    pass


@dataclass
class SECoPMovableLogic(MovableLogic[Any]):
    """Move logic for a SECoP "Drivable" module.

    A move is considered complete once the module's status parameter
    leaves BUSY and enters the IDLE range, rather than when readback
    equals setpoint.
    """

    status: SignalR
    secclient: AsyncFrappyClient
    module: str
    logger: Logger

    async def stop(self) -> None:
        self.logger.info(f"Stopping {self.module}")
        await self.secclient.exec_command(self.module, "stop")

    async def move(self, new_position: Any, timeout: TimeoutCalculator) -> None:
        # status has type Tuple, transported as a structured numpy array
        # ('f0': statuscode, 'f1': status message)
        def _left_busy(current_stat) -> bool:
            stat_code = current_stat["f0"]
            return not (BUSY <= stat_code < ERROR)

        self.logger.info(f"Moving {self.module} to {new_position}")

        # set 'target' first, *then* start watching 'status' -- the resting
        # (pre-move) state is already "not busy", so watching from before the
        # set would match immediately instead of waiting for a real move
        await self.setpoint.set(new_position)

        # force a fresh read so we don't wait on a stale, pre-move cached value
        await self.status.read(False)

        await wait_for_value(self.status, _left_busy, timeout=timeout())

        stat_code = (await self.status.get_value())["f0"]
        if stat_code >= ERROR or stat_code < IDLE:
            self.logger.error(f"Module {self.module} --> ERROR/DISABLED")
            raise RuntimeError(
                f"Move of {self.module} to {new_position} failed: module "
                "entered ERROR/DISABLED state"
            )

        self.logger.info(f"Reached target, module {self.module} --> IDLE")


class SECoPMoveableDevice(SECoPReadableDevice, StandardMovable[Any]):
    """
    Standard movable SECoP device, corresponding to a SECoP module with the
    interface class "Drivable"
    """

    hinted_signals: list[str] = ["target", "value"]

    # StandardMovable is @default_mock_class(InstantMovableMock), which would
    # otherwise also install a mock put-callback on 'target' on top of this
    # project's own SECoPBackend mock machinery when connecting with mock=True.
    _mock_class = DeviceMock

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
        :type secclient: AsyncFrappyClient
        :param module_name: ame of the SEC Node module that is represented by
            this device
        :type module_name: str
        """

        self.target: SignalRW

        super().__init__(
            sri=sri, name=name, connector=connector, loglevel=loglevel, logdir=logdir
        )

    async def connect(self, mock=False, timeout=DEFAULT_TIMEOUT, force_reconnect=False):

        await super().connect(mock, timeout, force_reconnect)

        if not hasattr(self, "target"):
            raise AttributeError(
                "Attribute 'target' has not been assigned, "
                + "but is needed for 'Drivable' interface class!"
            )

    @cached_property
    def movable_logic(self) -> MovableLogic:
        if self._module is None:
            raise RuntimeError

        return SECoPMovableLogic(
            setpoint=self.target,
            readback=self.value,
            status=self.status,
            secclient=self._client,
            module=self._module,
            logger=self._logger,
        )

    def set_name(self, name: str, *, child_name_separator: str | None = None) -> None:
        # set_name() can run several times before movable_logic is actually
        # resolvable: once before connect() (e.g. init_devices() naming devices
        # up front), and again mid-connect whenever a sibling/parent signal is
        # filled in (DeviceFiller.fill_child_signal() -> _set_device_child()
        # triggers a renaming cascade down the whole tree). StandardMovable's
        # set_name() needs both '_module' (set by set_module(), early in the
        # parent node's connect_real()) and 'target' (only created once this
        # device's own connect_real() fills its signals) to resolve
        # movable_logic, so skip it and fall back to plain Device.set_name()
        # until both are present; the later call does the real renaming.
        if self._module is None or not hasattr(self, "target"):
            Device.set_name(self, name, child_name_separator=child_name_separator)
            return
        super().set_name(name, child_name_separator=child_name_separator)

    async def _assign_interface_formats(self):
        await super()._assign_interface_formats()

        if format_assigned(self, self.target):
            if not is_read_signal(self, self.target):
                warnings.warn(
                    f"Signal 'target' of device {self.name} has format assigned "
                    + "that is not compatible with Movable interface class"
                )
        else:
            self.add_readables([self.target], StandardReadableFormat.HINTED_SIGNAL)


def class_from_interface(mod_properties: dict):
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

    return ophyd_class


IF_CLASSES = {
    "Triggerable": SECoPTriggerableDevice,
    "Drivable": SECoPMoveableDevice,
    "Writable": SECoPWritableDevice,
    "Readable": SECoPReadableDevice,
    "Communicator": SECoPCommunicatorDevice,
}
