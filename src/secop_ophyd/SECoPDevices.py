import asyncio
import logging
import re
import time as ttime
import warnings
from abc import abstractmethod
from dataclasses import dataclass
from logging import Logger
from types import MethodType
from typing import Any, Dict, Iterator, Optional, Type

import bluesky.plan_stubs as bps
from bluesky.protocols import (
    Flyable,
    Locatable,
    Location,
    PartialEvent,
    Reading,
    Stoppable,
    Subscribable,
    Triggerable,
)
from frappy.datatypes import (
    ArrayOf,
    BLOBType,
    BoolType,
    CommandType,
    FloatRange,
    IntRange,
    ScaledInteger,
    StringType,
    StructOf,
    TupleOf,
)
from ophyd_async.core import (
    DEFAULT_TIMEOUT,
    AsyncStatus,
    Device,
    DeviceConnector,
    DeviceFiller,
    LazyMock,
    Signal,
    SignalR,
    SignalRW,
    SignalX,
    StandardReadable,
    StandardReadableFormat,
    observe_value,
)
from ophyd_async.core._utils import Callback

from secop_ophyd.AsyncFrappyClient import AsyncFrappyClient
from secop_ophyd.logs import setup_logging
from secop_ophyd.propertykeys import DATAINFO, EQUIPMENT_ID, INTERFACE_CLASSES
from secop_ophyd.SECoPSignal import (
    AttributeType,
    LocalBackend,
    SECoPBackend,
    SECoPXBackend,
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


def secop_enum_name_to_python(member_name: str) -> str:
    """Convert SECoP enum member name to Python identifier.

    Examples:
        'Low Energy' -> 'LOW_ENERGY'
        'high-power' -> 'HIGH_POWER'
        'Mode 1' -> 'MODE_1'

    :param member_name: Original SECoP enum member name
    :return: Python-compatible identifier in UPPER_CASE
    """
    # Replace spaces and hyphens with underscores, remove other special chars
    cleaned = re.sub(r"[\s-]+", "_", member_name)
    cleaned = re.sub(r"[^a-zA-Z0-9_]", "", cleaned)
    # Convert to uppercase
    cleaned = cleaned.upper()
    # Ensure it doesn't start with a digit
    if cleaned and cleaned[0].isdigit():
        cleaned = "_" + cleaned
    return cleaned


def format_assigned(device: StandardReadable, signal: SignalR) -> bool:
    if (
        signal.describe in device._describe_funcs
        or signal.describe in device._describe_config_funcs
    ):
        # Standard readable format already assigned
        return True

    return False


def is_read_signal(device: StandardReadable, signal: SignalR | SignalRW) -> bool:
    if signal.describe() in device._describe_funcs:
        return True

    return False


def is_config_signal(device: StandardReadable, signal: SignalR | SignalRW) -> bool:
    if signal.describe() in device._describe_config_funcs:
        return True

    return False


@dataclass(init=False)
class ParamPath:
    """Annotation for Parameter Signals, defines the path to the parameter
    in the secclient module dict"""

    module: str
    parameter: str

    def __init__(self, param_path: str) -> None:
        # Parse from delimited string
        parts = param_path.split(":")
        if len(parts) != 2:
            raise ValueError(f"Expected 'module:param', got '{param_path}'")
        self.module = parts[0].strip()
        self.parameter = parts[1].strip()

    def __repr__(self) -> str:
        """Return repr suitable for code generation in annotations."""
        return f'ParamPath("{self.module}:{self.parameter}")'


@dataclass(init=False)
class PropPath:
    """Annotation for Module Property Signals, defines the path to the property"""

    property: str

    # if module is None, property is assumed to be at node level,
    # otherwise at module level
    module: str | None = None

    def __init__(self, property_path: str) -> None:
        # Parse from delimited string
        parts = property_path.split(":")
        if len(parts) == 2:
            self.module = parts[0].strip()
            self.property = parts[1].strip()
        else:
            self.property = property_path.strip()
            self.module = None  # --> node level property

    def __repr__(self) -> str:
        """Return repr suitable for code generation in annotations."""
        if self.module is None:
            return f'PropPath("{self.property}")'
        return f'PropPath("{self.module}:{self.property}")'


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

        if SECoPDevice.clients.get(self.node_id) is None:
            raise RuntimeError(f"No AsyncFrappyClient for URI {sri} exists")

        self.client: AsyncFrappyClient = SECoPDevice.clients[self.node_id]

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
            )

        for backend, annotations in self.filler.create_signals_from_annotations():
            self.fill_backend_with_path(backend, annotations)

        list(self.filler.create_devices_from_annotations(filled=False))

        self.filler.check_created()

    def fill_backend_with_path(self, backend: SECoPBackend, annotations: list[Any]):
        unhandled = []
        while annotations:
            annotation = annotations.pop(0)

            if isinstance(annotation, StandardReadableFormat):
                backend.format = annotation

            elif isinstance(annotation, ParamPath):
                backend.attribute_type = AttributeType.PARAMETER

                backend._module_name = annotation.module
                backend._attribute_name = annotation.parameter
                backend._secclient = self.client
                backend.path_str = annotation.module + ":" + annotation.parameter

            elif isinstance(annotation, PropPath):
                backend.attribute_type = AttributeType.PROPERTY

                backend._module_name = annotation.module
                backend._attribute_name = annotation.property
                backend._secclient = self.client

                if annotation.module:
                    backend.path_str = annotation.module + ":" + annotation.property
                else:
                    backend.path_str = annotation.property
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


class SECoPCMDDevice(StandardReadable, Flyable, Triggerable):
    """
    Command devices that have Signals for command args, return values and a signal
    for triggering command execution (SignalX). They themselves are triggerable.

    Once the CMD Device is triggered, the command args are retrieved from the 'argument'
    Signal. The command message is sent to the SEC Node and the return value is written
    to 'result' signal.

    """

    def __init__(self, path: Path, secclient: AsyncFrappyClient):
        """Initialize the CMD Device

        :param path: Path to the command in the secclient module dict
        :type path: Path
        :param secclient: SECoP client providing communication to the SEC Node
        :type secclient: AsyncFrappyClient
        """
        dev_name: str = path.get_signal_name() + "_CMD"

        self._secclient: AsyncFrappyClient = secclient

        cmd_props = secclient.modules[path._module_name]["commands"][
            path._accessible_name
        ]  # noqa: E501
        cmd_datatype: CommandType = cmd_props["datatype"]
        datainfo = cmd_props[DATAINFO]

        self.description: str = cmd_props["description"]
        self.arg_dtype = cmd_datatype.argument
        self.res_dtype = cmd_datatype.result

        self.argument: SignalRW | None
        self.result: SignalR | None

        # result signals
        read = []
        # argument signals
        config = []

        self._start_time: float
        self.commandx: SignalX

        self.wait_idle: bool = False

        with self.add_children_as_readables(
            format=StandardReadableFormat.CONFIG_SIGNAL
        ):
            # Argument Signals (config Signals, can also be read)
            arg_path = path.append("argument")
            if self.arg_dtype is None:
                self.argument = None
            else:
                arg_backend = LocalBackend(
                    path=arg_path,
                    secop_dtype_obj=self.arg_dtype,
                    sig_datainfo=datainfo["argument"],
                )
                self.argument = SignalRW(arg_backend)
                config.append(self.argument)

            # Result Signals  (read Signals)
            res_path = path.append("result")

            if self.res_dtype is None:
                self.result = None
            else:
                res_backend = LocalBackend(
                    path=res_path,
                    secop_dtype_obj=self.res_dtype,
                    sig_datainfo=datainfo["result"],
                )
                self.result = SignalRW(res_backend)
                read.append(self.argument)

            argument = None
            result = None
            if isinstance(self.argument, SignalR):
                argument = self.argument._connector.backend

            if isinstance(self.result, SignalR):
                result = self.result._connector.backend

            # SignalX (signal that triggers execution of the Command)
            exec_backend = SECoPXBackend(
                path=path,
                secclient=secclient,
                argument=argument,  # type: ignore
                result=result,  # type: ignore
            )

        self.commandx = SignalX(exec_backend)

        super().__init__(name=dev_name)

    def trigger(self) -> AsyncStatus:
        """Triggers the SECoPCMDDevice and sends command message to SEC Node.
        Command argument is taken form 'argument' Signal, and return value is
        written in the 'return' Signal

        :return: A Status object, that is marked Done once the answer from the
        SEC Node is received
        :rtype: AsyncStatus
        """
        coro = asyncio.wait_for(fut=self._exec_cmd(), timeout=None)
        return AsyncStatus(awaitable=coro)

    def kickoff(self) -> AsyncStatus:
        # trigger execution of secop command, wait until Device is Busy

        self._start_time = ttime.time()
        coro = asyncio.wait_for(fut=asyncio.sleep(1), timeout=None)
        return AsyncStatus(coro)

    async def _exec_cmd(self):
        stat = self.commandx.trigger()

        await stat

    def complete(self) -> AsyncStatus:
        coro = asyncio.wait_for(fut=self._exec_cmd(), timeout=None)
        return AsyncStatus(awaitable=coro)

    def collect(self) -> Iterator[PartialEvent]:
        yield dict(
            time=self._start_time, timestamps={self.name: []}, data={self.name: []}
        )


class SECoPDevice(StandardReadable):

    clients: Dict[str, AsyncFrappyClient] = {}
    node_id: str
    sri: str
    host: str
    port: str
    module: str | None
    mod_prop_devices: Dict[str, SignalR]
    param_devices: Dict[str, Any]
    logger: Logger

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

        self.sri = sri
        self.host = sri.split(":")[0]
        self.port = sri.split(":")[1]
        self.mod_prop_devices = {}
        self.param_devices = {}
        self.node_id = sri.split(":")[0] + ":" + sri.split(":")[1]

        self.logger = setup_logging(
            name=f"frappy:{self.host}:{self.port}",
            level=loglevel,
            log_dir=logdir,
        )

        self.module = None
        if len(sri.split(":")) > 2:
            self.module = sri.split(":")[2]

        if SECoPDevice.clients.get(self.node_id) is None:
            SECoPDevice.clients[self.node_id] = AsyncFrappyClient(
                host=self.host, port=self.port, log=self.logger
            )

        connector = connector or SECoPDeviceConnector(sri=sri)

        self._client: AsyncFrappyClient = SECoPDevice.clients[self.node_id]

        super().__init__(name=name, connector=connector)

    def set_module(self, module_name: str):
        if self.module is not None:
            raise RuntimeError("Module can only be set if it was not already set")

        self.module = module_name
        self.sri = self.sri + ":" + module_name

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

        if self.module:
            module_desc = self._client.modules[self.module]

            # Initialize Command Devices
            for command, _ in module_desc["commands"].items():
                # generate new root path
                cmd_path = Path(parameter_name=command, module_name=self.module)
                cmd_dev_name = command + "_CMD"
                setattr(
                    self,
                    cmd_dev_name,
                    SECoPCMDDevice(path=cmd_path, secclient=self._client),
                )

                cmd_dev: SECoPCMDDevice = getattr(self, cmd_dev_name)
                # Add Bluesky Plan Methods

                # Stop is already an ophyd native operation
                if command == "stop":
                    continue

                cmd_plan = self.generate_cmd_plan(
                    cmd_dev, cmd_dev.arg_dtype, cmd_dev.res_dtype
                )

                setattr(self, command, MethodType(cmd_plan, self))

        await super().connect(mock, timeout, force_reconnect)

        if self.module is None:
            # set device name from equipment id property
            self.set_name(self._client.properties[EQUIPMENT_ID].replace(".", "-"))
        else:
            self.set_name(self.module)

    def generate_cmd_plan(
        self,
        cmd_dev: SECoPCMDDevice,
        argument_type: Type | None = None,
        return_type: Type | None = None,
    ):

        def command_plan_no_arg(self, wait_for_idle: bool = False):
            # Trigger the Command device, meaning that the command gets sent to the
            # SEC Node
            yield from bps.trigger(cmd_dev, wait=True)

            if wait_for_idle:

                def wait_for_idle_factory():
                    return self.wait_for_idle()

                yield from bps.wait_for([wait_for_idle_factory])

            if (
                return_type is not None
                and isinstance(cmd_dev.result, SignalR)
                and isinstance(cmd_dev.result._connector.backend, LocalBackend)
            ):

                return cmd_dev.result._connector.backend.reading.get_value()

        def command_plan(self, arg, wait_for_idle: bool = False):
            # TODO  Type checking

            if arg is not None:
                yield from bps.abs_set(cmd_dev.argument, arg)

            # Trigger the Command device, meaning that the command gets sent to the
            # SEC Node
            yield from bps.trigger(cmd_dev, wait=True)

            if wait_for_idle:

                def wait_for_idle_factory():
                    return self.wait_for_idle()

                yield from bps.wait_for([wait_for_idle_factory])

            if (
                return_type is not None
                and isinstance(cmd_dev.result, SignalR)
                and isinstance(cmd_dev.result._connector.backend, LocalBackend)
            ):

                return cmd_dev.result._connector.backend.reading.get_value()

        cmd_meth = command_plan_no_arg if argument_type is None else command_plan

        anno_dict = cmd_meth.__annotations__

        dtype_mapping = {
            StructOf: dict[str, Any],
            ArrayOf: list[Any],
            TupleOf: tuple[Any],
            BLOBType: str,
            BoolType: bool,
            FloatRange: float,
            IntRange: int,
            ScaledInteger: int,
            StringType: str,
        }

        if return_type is not None:
            anno_dict["return"] = dtype_mapping[return_type.__class__]
        if argument_type is not None:
            anno_dict["arg"] = dtype_mapping[argument_type.__class__]

        return cmd_meth

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
        genCode = GenNodeCode(path=path_to_module, log=self.logger)

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

        self.logger.info(f"Waiting for {self.name} to be IDLE")

        if self.status is None:
            self.logger.error("Status Signal not initialized")
            raise Exception("status Signal not initialized")

        # force reading of fresh status from device
        await self.status.read(False)

        async for current_stat in observe_value(self.status):
            # status is has type Tuple and is therefore transported as
            # structured Numpy array ('f0':statuscode;'f1':status Message)

            stat_code = current_stat["f0"]

            # Module is in IDLE/WARN state
            if IDLE <= stat_code < BUSY:
                self.logger.info(f"Module {self.name} --> IDLE")
                break

            if hasattr(self, "_stopped"):
                # self.logger.info(f"Module {self.name} was stopped STOPPED")
                if self._stopped is True:
                    break

            # Error State or DISABLED
            if hasattr(self, "_success"):
                if stat_code >= ERROR or stat_code < IDLE:
                    self.logger.error(f"Module {self.name} --> ERROR/DISABLED")
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
        self.logger.info(f"Triggering {self.name}: read fresh data from device")
        # get fresh reading of the value Parameter from the SEC Node
        return AsyncStatus(
            awaitable=self._client.get_parameter(self.module, "value", trycache=False)
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

        self.go_CMD: SECoPCMDDevice

        self._success = True
        self._stopped = False

        super().__init__(
            sri=sri, name=name, connector=connector, loglevel=loglevel, logdir=logdir
        )


class SECoPWritableDevice(SECoPReadableDevice):
    hinted_signals: list[str] = ["target", "value"]

    pass


class SECoPMoveableDevice(SECoPReadableDevice, Locatable, Stoppable):
    """
    Standard movable SECoP device, corresponding to a SECoP module with the
    interface class "Drivable"
    """

    hinted_signals: list[str] = ["target", "value"]

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

        self._success = True
        self._stopped = False

    async def connect(self, mock=False, timeout=DEFAULT_TIMEOUT, force_reconnect=False):

        await super().connect(mock, timeout, force_reconnect)

        if not hasattr(self, "target"):
            raise AttributeError(
                "Attribute 'target' has not been assigned, "
                + "but is needed for 'Drivable' interface class!"
            )

    def set(self, new_target, timeout: Optional[float] = None) -> AsyncStatus:
        """Sends new target to SEC Nonde and waits until module is IDLE again

        :param new_target: new taget/setpoint for module
        :type new_target: _type_
        :param timeout: timeout for set operation, defaults to None
        :type timeout: Optional[float], optional
        :return: Asyncstatus that gets set to Done once module is IDLE again
        :rtype: AsyncStatus
        """
        coro = asyncio.wait_for(self._move(new_target), timeout=timeout)
        return AsyncStatus(coro)

    async def _move(self, new_target):
        self._success = True
        self._stopped = False

        await self.target.set(new_target, wait=False)
        self.logger.info(f"Moving {self.name} to {new_target}")

        # force reading of status from device
        await self.status.read(False)

        # observe status and wait until dvice is IDLE again
        async for current_stat in observe_value(self.status):
            stat_code = current_stat["f0"]

            if self._stopped is True:
                break

            # Error State or DISABLED
            if stat_code >= ERROR or stat_code < IDLE:
                self.logger.error(f"Module {self.name} --> ERROR/DISABLED")
                self._success = False
                break

            # Module is in IDLE/WARN state
            if IDLE <= stat_code < BUSY:
                self.logger.info(f"Reached Target Module {self.name} --> IDLE")
                break

            # TODO other status transitions

        if not self._success:
            raise RuntimeError("Module was stopped")

    async def stop(self, success=True):
        """Calls stop command on the SEC Node module

        :param success:
            True: device is stopped as planned
            False: something has gone wrong
            (defaults to True)
        :type success: bool, optional
        """
        self._success = success

        if not success:
            self.logger.info(f"Stopping {self.name} success={success}")
            await self._client.exec_command(self.module, "stop")
            self._stopped = True

    async def locate(self) -> Location:
        # return current location of the device (setpoint and readback).
        # Only locally cached values are returned
        setpoint = await self._client.get_parameter(self.module, "target", True)
        readback = await self._client.get_parameter(self.module, "value", True)

        location: Location = {
            "setpoint": setpoint.value,
            "readback": readback.value,
        }
        return location

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
