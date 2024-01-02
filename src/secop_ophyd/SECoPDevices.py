import asyncio
import re
import threading
import time as ttime
from typing import Dict, Iterator, Optional

from bluesky.protocols import (
    Descriptor,
    Flyable,
    Movable,
    PartialEvent,
    Stoppable,
    SyncOrAsync,
    Triggerable,
)
from frappy.client import Logger
from frappy.datatypes import CommandType
from ophyd_async.core.async_status import AsyncStatus
from ophyd_async.core.signal import SignalR, SignalRW, SignalX, observe_value
from ophyd_async.core.standard_readable import StandardReadable
from ophyd_async.core.utils import T

from secop_ophyd.AsyncFrappyClient import AsyncFrappyClient
from secop_ophyd.propertykeys import DATAINFO, EQUIPMENT_ID, INTERFACE_CLASSES
from secop_ophyd.SECoPSignal import (
    PropertyBackend,
    SECoP_CMD_IO_Backend,
    SECoP_CMD_X_Backend,
    SECoP_Param_Backend,
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


def clean_identifier(anystring):
    return str(re.sub(r"\W+|^(?=\d)", "_", anystring))


def class_from_interface(mod_properties: Dict[str, Dict[str, str]]):
    interface_classes: dict = mod_properties[INTERFACE_CLASSES]
    for interface_class in interface_classes:
        try:
            return IF_CLASSES[interface_class]
        except KeyError:
            continue
    raise Exception(
        "no compatible Interfaceclass found in: "
        + str(mod_properties.get(INTERFACE_CLASSES))
    )


def get_config_attrs(parameters):
    parameters_cfg = parameters.copy()
    parameters_cfg.pop("target", None)
    parameters_cfg.pop("value", None)
    return parameters_cfg


class SECoPBaseDevice(StandardReadable):
    def __init__(self, secclient: AsyncFrappyClient) -> None:
        if type(self) is SECoPBaseDevice:
            raise Exception("<SECoPBaseDevice> must be subclassed.")

        self._secclient: AsyncFrappyClient = secclient

        # list for config signals
        self._config: list = []

        # list for read signals
        self._read: list = []

        self.status: SignalR = None

    def _signal_from_parameter(self, path: Path, sig_name: str, readonly: str):
        # Normal types + (struct and tuple as JSON object Strings)
        paramb = SECoP_Param_Backend(path=path, secclient=self._secclient)

        # construct signal
        if readonly is True:
            setattr(self, sig_name, SignalR(paramb))
        elif readonly is False:
            setattr(self, sig_name, SignalRW(paramb))
        else:
            raise Exception(
                "Invalid SECoP Parameter, readonly property "
                + "is mandatory, but was not found, or is not bool"
            )

    async def wait_for_IDLE(self):
        """asynchronously waits until module is IDLE again. this is helpful,
        for running commands that are not done immediately
        """
        if self.status is None:
            raise Exception("status Signal not initialized")

        # force reading of fresh status from device
        await self.status.read(False)

        async def wait_for_idle():
            async for current_stat in observe_value(self.status):
                # status is has type Tuple and is therefore transported as
                # structured Numpy array ('f0':statuscode;'f1':status Message)

                stat_code = current_stat["f0"]

                # Module is in IDLE/WARN state
                if IDLE <= stat_code < BUSY:
                    break

        if not self._secclient.external:
            await wait_for_idle()
        else:
            fut = asyncio.run_coroutine_threadsafe(
                wait_for_idle(), self._secclient.loop
            )
            await asyncio.wrap_future(future=fut)


class SECoPReadableDevice(SECoPBaseDevice):
    """
    Standard readable SECoP device, corresponding to a SECoP module with the
    interface class "Readable"
    """

    def __init__(self, secclient: AsyncFrappyClient, module_name: str):
        """Initializes readable dev"""
        super().__init__(secclient=secclient)

        self._module = module_name
        module_desc = secclient.modules[module_name]

        # generate Signals from Module Properties
        for property in module_desc["properties"]:
            propb = PropertyBackend(property, module_desc["properties"], secclient)

            setattr(self, property, SignalR(backend=propb))
            self._config.append(getattr(self, property))

        # generate Signals from Module parameters eiter r or rw
        for parameter, properties in module_desc["parameters"].items():
            # generate new root path
            param_path = Path(parameter_name=parameter, module_name=module_name)

            # readonly propertyns to plans and plan stubs.
            readonly = properties.get("readonly", None)

            # Normal types + (struct and tuple as JSON object Strings)
            self._signal_from_parameter(
                path=param_path,
                sig_name=parameter,
                readonly=readonly,
            )

        # Initialize Command Devices
        for command, properties in module_desc["commands"].items():
            # generate new root path
            cmd_path = Path(parameter_name=command, module_name=module_name)
            setattr(
                self,
                command + "_CMD",
                SECoP_CMD_Device(path=cmd_path, secclient=secclient),
            )

        self.set_readable_signals(read=self._read, config=self._config)

        self.set_name(module_name)

    def _signal_from_parameter(self, path: Path, sig_name: str, readonly: str):
        super(SECoPReadableDevice, self)._signal_from_parameter(
            path=path, sig_name=sig_name, readonly=readonly
        )

        # In SECoP only the 'value' parameter is the primary read prameter, but
        # if the value is a SECoP-tuple all elements belonging to the tuple are
        # appended to the read list
        if path._accessible_name == "value":
            self._read.append(getattr(self, sig_name))

        # target should only be set through the set method. And is not part of
        # config
        elif path._accessible_name != "target":
            self._config.append(getattr(self, sig_name))


class SECoPWritableDevice(SECoPReadableDevice):
    """Fast settable device target"""

    pass


class SECoPMoveableDevice(SECoPWritableDevice, Movable, Stoppable):
    """
    Standard movable SECoP device, corresponding to a SECoP module with the
    interface class "Drivable"
    """

    def __init__(self, secclient: AsyncFrappyClient, module_name: str):
        super().__init__(secclient, module_name)
        self._success = True
        self._stopped = False

    def set(self, new_target, timeout: Optional[float] = None) -> AsyncStatus:
        coro = asyncio.wait_for(self._move(new_target), timeout=timeout)
        return AsyncStatus(coro)

    async def _move(self, new_target):
        self._success = True
        self._stopped = False
        await self.target.set(new_target, wait=False)

        # force reading of status from device
        await self.status.read(False)

        # observe status and wait until dvice is IDLE again
        async for current_stat in observe_value(self.status):
            stat_code = current_stat["f0"]

            if self._stopped is True:
                break

            # Error State or DISABLED
            if stat_code >= ERROR or stat_code < IDLE:
                self._success = False
                break

            # Module is in IDLE/WARN state
            if IDLE <= stat_code < BUSY:
                break

            # TODO other status transitions

        if not self._success:
            raise RuntimeError("Module was stopped")

    async def stop(self, success=True):
        self._success = success

        await self._secclient.execCommand(self._module, "stop")
        self._stopped = True


class SECoP_CMD_Device(StandardReadable, Flyable, Triggerable):
    """
    Command devices that have Signals for command args, return values and a signal
    for triggering command execution
    """

    def __init__(self, path: Path, secclient: AsyncFrappyClient):
        dev_name: str = path.get_signal_name() + "_CMD"

        self._secclient: AsyncFrappyClient = secclient

        cmd_props = secclient.modules[path._module_name]["commands"][
            path._accessible_name
        ]  # noqa: E501
        cmd_datatype: CommandType = cmd_props["datatype"]
        datainfo = cmd_props[DATAINFO]

        arg_dtype = cmd_datatype.argument
        res_dtype = cmd_datatype.result

        self.argument: SignalRW | None
        self.result: SignalR | None

        # result signals
        read = []
        # argument signals
        config = []

        self._start_time: float
        self.commandx: SignalX

        # Argument Signals (config Signals, can also be read)
        arg_path = path.append("argument")
        if arg_dtype is None:
            self.argument = None
        else:
            arg_backend = SECoP_CMD_IO_Backend(
                path=arg_path,
                SECoPdtype_obj=arg_dtype,
                sig_datainfo=datainfo["argument"],
            )
            self.argument = SignalRW(arg_backend)
            config.append(self.argument)

        # Result Signals  (read Signals)
        res_path = path.append("result")

        if res_dtype is None:
            self.result = None
        else:
            res_backend = SECoP_CMD_IO_Backend(
                path=res_path,
                SECoPdtype_obj=res_dtype,
                sig_datainfo=datainfo["result"],
            )
            self.result = SignalRW(res_backend)
            read.append(self.argument)

        # SignalX (signal that triggers execution of the Command)
        exec_backend = SECoP_CMD_X_Backend(
            path=path,
            secclient=secclient,
            frappy_datatype=cmd_datatype,
            cmd_desc=cmd_props,
            argument=None if self.argument is None else self.argument._backend,
            result=None if self.result is None else self.result._backend,
        )

        self.commandx = SignalX(exec_backend)

        self.set_readable_signals(read=read, config=config)

        super().__init__(name=dev_name)

    def kickoff(self) -> AsyncStatus:
        # trigger execution of secop command, wait until Device is Busy

        self._start_time = ttime.time()
        coro = asyncio.wait_for(fut=asyncio.sleep(1), timeout=None)
        return AsyncStatus(coro, watchers=None)

    async def _exec_cmd(self):
        stat = self.commandx.trigger()

        await stat

        await self.parent.wait_for_IDLE()

    def complete(self) -> AsyncStatus:
        coro = asyncio.wait_for(fut=self._exec_cmd(), timeout=None)
        return AsyncStatus(awaitable=coro, watchers=None)

    def collect(self) -> Iterator[PartialEvent]:
        yield dict(
            time=self._start_time, timestamps={self.name: []}, data={self.name: []}
        )

    async def describe_collect(self) -> SyncOrAsync[Dict[str, Dict[str, Descriptor]]]:
        return await self.describe()

    def trigger(self) -> AsyncStatus:
        coro = asyncio.wait_for(fut=self._exec_cmd(), timeout=None)
        return AsyncStatus(awaitable=coro, watchers=None)


class SECoP_Node_Device(StandardReadable):
    """
    Generates the root ophyd device from a Sec-node. Signals of this Device correspond
    to the Sec-node properties
    """

    def __init__(self, secclient: AsyncFrappyClient):
        """initializes the node device and generates all node signals and subdevices
        corresponding to the SECoP-modules of the secnode

        Args:
            secclient (AsyncFrappyClient): running Frappy client that is connected
            to a Sec-node
        """
        self._secclient: AsyncFrappyClient = secclient

        self.mod_devices: Dict[str, T] = {}

        # Name is set to sec-node equipment_id
        name = self._secclient.properties[EQUIPMENT_ID].replace(".", "-")

        config = []

        for property in self._secclient.properties:
            propb = PropertyBackend(property, self._secclient.properties, secclient)
            setattr(self, property, SignalR(backend=propb))
            config.append(getattr(self, property))

        for module, module_desc in self._secclient.modules.items():
            SECoPDeviceClass = class_from_interface(module_desc["properties"])

            setattr(self, module, SECoPDeviceClass(self._secclient, module))
            self.mod_devices[module] = getattr(self, module)

        self.set_readable_signals(config=config)

        # register secclient callbacks (these are useful if sec node description
        # changes after a reconnect)
        secclient.client.register_callback(
            None, self.descriptiveDataChange, self.nodeStateChange
        )

        super().__init__(name=name)

    @classmethod
    async def create(cls, host: str, port: str, loop, log=Logger):
        """async factory pattern to be able to have an async io constructor,
        since __init__ isnt allowed to be async.

        Args:
            host (str): hostname of Sec-node
            port (str): Sec-node port
            loop (_type_): asyncio eventloop, can either run in same thread or external
            thread. In conjunction with bluesky RE.loop should be used
            log (_type_, optional): Logging of AsyncFrappyClient. Defaults to Logger.


        Returns:
            SECoP_Node_Device: fully initializedand connected Sec-node device including
            all subdevices
        """
        # check if asyncio eventloop is running in the same thread
        if loop._thread_id == threading.current_thread().ident and loop.is_running():
            secclient = await AsyncFrappyClient.create(
                host=host, port=port, loop=loop, log=log
            )

        else:
            # Event loop is running in a different thread
            client_future = asyncio.run_coroutine_threadsafe(
                AsyncFrappyClient.create(host=host, port=port, loop=loop, log=log), loop
            )
            secclient = await asyncio.wrap_future(future=client_future)
            secclient.external = True

        return SECoP_Node_Device(secclient=secclient)

    async def disconnect(self):
        """shuts down secclient using asyncio, eventloop can be running in same or
        external thread
        """
        if (
            self._secclient.loop._thread_id == threading.current_thread().ident
            and self._secclient.loop.is_running()
        ):
            await self._secclient.disconnect(True)
        else:
            disconn_future = asyncio.run_coroutine_threadsafe(
                self._secclient.disconnect(True), self._secclient.loop
            )
            await asyncio.wrap_future(future=disconn_future)

    def disconnect_external(self):
        """shuts down secclient, eventloop mus be running in external thread"""
        if (
            self._secclient.loop._thread_id == threading.current_thread().ident
            and self._secclient.loop.is_running()
        ):
            raise Exception
        else:
            future = asyncio.run_coroutine_threadsafe(
                self._secclient.disconnect(True), self._secclient.loop
            )

            future.result(2)

    def descriptiveDataChange(self, module, description):
        """called when the description has changed

        this callback is called on the node with module=None
        and on every changed module with module==<module name>
        """

        self._secclient.conn_timestamp = ttime.time()

        if module is None:
            # Refresh signals that correspond to Node Properties
            config = []
            for property in self._secclient.properties:
                propb = PropertyBackend(
                    property, self._secclient.properties, self._secclient
                )

                setattr(self, property, SignalR(backend=propb))
                config.append(getattr(self, property))

            self.set_readable_signals(config=config)
        else:
            # Refresh changed modules
            module_desc = self._secclient.modules[module]
            SECoPDeviceClass = class_from_interface(module_desc["properties"])

            setattr(self, module, SECoPDeviceClass(self._secclient, module))

            # TODO what about removing Modules during disconn

    def nodeStateChange(self, online, state):
        """called when the state of the connection changes

        'online' is True when connected or reconnecting, False when disconnected
        or connecting 'state' is the connection state as a string
        """
        if state == "connected" and online is True:
            self._secclient.conn_timestamp = ttime.time()


IF_CLASSES = {
    "Drivable": SECoPMoveableDevice,
    "Writable": SECoPWritableDevice,
    "Readable": SECoPReadableDevice,
    "Module": SECoPReadableDevice,
    "Communicator": SECoPReadableDevice,
}


ALL_IF_CLASSES = set(IF_CLASSES.values())

# TODO
# FEATURES = {
#    'HasLimits': SecopHasLimits,
#    'HasOffset': SecopHasOffset,
# }
