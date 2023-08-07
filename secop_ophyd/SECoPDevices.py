import asyncio
import re
import threading
import time
from typing import (
    Dict,
    Optional,
)

from bluesky.protocols import Movable, Stoppable, SyncOrAsync
from ophyd.v2.core import (
    AsyncStatus,
    Device,
    SignalR,
    SignalRW,
    SignalX,
    StandardReadable,
    T,
    observe_value,
)

from frappy.datatypes import CommandType, StructOf, TupleOf, ArrayOf, DataType
from secop_ophyd.AsyncFrappyClient import AsyncFrappyClient
from secop_ophyd.propertykeys import DATAINFO, EQUIPMENT_ID, INTERFACE_CLASSES
from secop_ophyd.SECoPSignal import (
    PropertyBackend,
    SECoP_CMD_IO_Backend,
    SECoP_CMD_X_Backend,
    SECoP_Param_Backend,
    atomic_dtypes,
)
from secop_ophyd.util import Path, deep_get

"""_summary_
    
    SECNode
    ||
    ||
    ||---dev1(readable)
    ||---dev2(drivable)
    ||---dev3(writable)
    ||---dev4(readable)
"""

## Predefined Status Codes
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


def class_from_interface(mod_properties: dict):
    for interface_class in mod_properties.get(INTERFACE_CLASSES):
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


class SECoPReadableDevice(StandardReadable):
    def __init__(self, secclient: AsyncFrappyClient, module_name: str):
        self._secclient = secclient
        self._module = module_name
        module_desc = secclient.modules[module_name]

        self.value: SignalR
        self.status_code: SignalR

        # list for config signals
        config = []
        # list for read signals
        read = []

        # generate Signals from Module Properties
        for property in module_desc["properties"]:
            propb = PropertyBackend(property, module_desc["properties"])
            setattr(self, property, SignalR(backend=propb))
            config.append(getattr(self, property))

        # generate Signals from Module parameters eiter r or rw
        for parameter, properties in module_desc["parameters"].items():
            # generate new root path
            param_path = Path(parameter_name=parameter, module_name=module_name)

            dtype:DataType = properties["datatype"]

            # sub devices for nested datatypes
            match dtype:
                case TupleOf():
                    setattr(
                        self, 
                        parameter + "_tuple",
                        SECoP_Tuple_Device(path=param_path, secclient=secclient),
                    )
                case StructOf():
                    setattr(
                        self,
                        parameter + "_struct",
                        SECoP_Struct_Device(path=param_path, secclient=secclient),
                    )
                case ArrayOf():
                    if isinstance(dtype.members,(StructOf,TupleOf)):
                        #TODO instanciate transposed array Device 
                        pass

            ## Normal types + (struct and tuple as JSON object Strings)
            paramb = SECoP_Param_Backend(path=param_path, secclient=secclient)

            # construct signal
            readonly = properties.get("readonly", None)
            if readonly is True:
                setattr(self, parameter, SignalR(paramb))
            elif readonly is False:
                setattr(self, parameter, SignalRW(paramb))
            else:
                raise Exception(
                    "Invalid SECoP Parameter, readonly property "
                    + "is mandatory, but was not found, or is not bool"
                )

            # add status code signal to root device
            if parameter == "status":
                self.status_code = SignalR(
                    SECoP_Param_Backend(path=param_path.append(0), secclient=secclient)
                )

            # In SECoP only the 'value' parameter is the primary read prameter, but
            # if the value is a SECoP-tuple all elements belonging to the tuple are
            # appended to the read list
            if parameter == "value":
                read.append(getattr(self, parameter))

            # target should only be set through the set method. And is not part of
            # config
            elif parameter != "target":
                config.append(getattr(self, parameter))

        # Initialize Command Devices
        for command, properties in module_desc["commands"].items():
            # generate new root path
            cmd_path = Path(parameter_name=command, module_name=module_name)
            setattr(
                self,
                command + "_dev",
                SECoP_CMD_Device(path=cmd_path, secclient=secclient),
            )

        # TODO Commands!!!
        self.set_readable_signals(read=read, config=config)

        super().__init__(name=module_name)

    def set_name(self, name: str = ""):
        # if name and not self._name:
        self._name = name
        for attr_name, attr in self.__dict__.items():
            # TODO: support lists and dicts of devices
            if isinstance(attr, Device):
                attr.set_name(f"{name}-{attr_name.rstrip('_')}")
                attr.parent = self

    async def wait_for_IDLE(self):
        async for current_stat in observe_value(self.status_code):
            stat_code = current_stat[0].value

            # Module is in IDLE/WARN state
            if IDLE <= stat_code < BUSY:
                break


class SECoP_Tuple_Device(StandardReadable):
    def __init__(self, path: Path, secclient: AsyncFrappyClient):
        self._path = path
        self._secclient: AsyncFrappyClient = secclient

        dev_name: str = path.get_signal_name() + "_tuple"

        props = secclient.modules[path._module_name]["parameters"][
            path._accessible_name
        ]  # noqa: E501

        datainfo = props[DATAINFO]

        # list for read signals
        read = []

        for ix, member_info in enumerate(
            deep_get(datainfo, path.get_memberinfo_path() + ["members"])
        ):  # noqa: E501
            # new path object for tuple member
            tuplemember_path = path.append(ix)
            attr_name = tuplemember_path.get_signal_name()

            match member_info["type"]:
                case "tuple":
                    setattr(
                        self,
                        attr_name + "_tuple",
                        SECoP_Tuple_Device(path=tuplemember_path, secclient=secclient),
                    )
                case "struct":
                    setattr(
                        self,
                        attr_name + "_struct",
                        SECoP_Struct_Device(path=tuplemember_path, secclient=secclient),
                    )

                # atomic datatypes & arrays
                case _:
                    tparamb = SECoP_Param_Backend(
                        path=tuplemember_path, secclient=secclient
                    )

                    # construct signal
                    readonly = props.get("readonly", None)

                    if readonly is True:
                        setattr(self, attr_name, SignalR(tparamb))
                    elif readonly is False:
                        setattr(self, attr_name, SignalRW(tparamb))
                    else:
                        raise Exception(
                            "Invalid SECoP Parameter, readonly property"
                            + " is mandatory, but was not found, or is not bool"
                        )

                    read.append(getattr(self, attr_name))

            self.set_readable_signals(read=read)

        super().__init__(dev_name)


class SECoPWritableDevice(SECoPReadableDevice, Movable):
    """Fast settable device target"""

    pass


class SECoPMoveableDevice(SECoPWritableDevice, Movable, Stoppable):
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
        async for current_stat in observe_value(self.status_code):
            stat_code = current_stat[0].value

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

    async def stop(self, success=True) -> SyncOrAsync[None]:
        self._success = success

        await self._secclient.execCommand(self._module, "stop")
        self._stopped = True


class SECoP_Struct_Device(StandardReadable):
    def __init__(self, path: Path, secclient: AsyncFrappyClient):
        dev_name: str = path.get_signal_name() + "_struct"

        self._secclient: AsyncFrappyClient = secclient

        props = secclient.modules[path._module_name]["parameters"][
            path._accessible_name
        ]  # noqa: E501

        datainfo = props[DATAINFO]

        # list for read signals
        read = []

        for member_name, member_info in deep_get(
            datainfo, path.get_memberinfo_path() + ["members"]
        ).items():  # noqa: E501
            # new path object for tuple member
            struct_member_path = path.append(member_name)
            attr_name = struct_member_path.get_signal_name()

            match member_info["type"]:
                case "tuple":
                    setattr(
                        self,
                        attr_name + "_tuple",
                        SECoP_Tuple_Device(
                            path=struct_member_path, secclient=secclient
                        ),
                    )
                case "struct":
                    setattr(
                        self,
                        attr_name + "_struct",
                        SECoP_Struct_Device(
                            path=struct_member_path, secclient=secclient
                        ),
                    )

                # atomic datatypes & arrays
                case _:
                    struct_param_backend = SECoP_Param_Backend(
                        path=struct_member_path, secclient=secclient
                    )

                    # construct signal
                    readonly = props.get("readonly", None)

                    if readonly is True:
                        setattr(self, attr_name, SignalR(struct_param_backend))
                    elif readonly is False:
                        setattr(self, attr_name, SignalRW(struct_param_backend))
                    else:
                        raise Exception(
                            "Invalid SECoP Parameter, readonly property "
                            + "is mandatory, but was not found, or is not bool"
                        )

                    read.append(getattr(self, member_name))

            self.set_readable_signals(read=read)

        super().__init__(name=dev_name)


class SECoP_CMD_Device(StandardReadable):
    def __init__(self, path: Path, secclient: AsyncFrappyClient):
        dev_name: str = path.get_signal_name() + "_cmd"

        self._secclient: AsyncFrappyClient = secclient

        cmd_props = secclient.modules[path._module_name]["commands"][
            path._accessible_name
        ]  # noqa: E501
        cmd_datatype: CommandType = cmd_props["datatype"]
        datainfo = cmd_props[DATAINFO]

        arg_dtype = cmd_datatype.argument
        res_dtype = cmd_datatype.result

        # Argument Signals (config Signals, can also be read)
        arg_path = path.append("argument")
        arguments = {}
        result = {}

        # result signals
        read = []
        # argument signals
        config = []

        if isinstance(arg_dtype, StructOf):
            for signame, sig_desc in datainfo["argument"]["members"].items():
                arg_backend = SECoP_CMD_IO_Backend(
                    path=arg_path.append(signame),
                    frappy_datatype=arg_dtype.members.get(signame),
                    sig_datainfo=sig_desc,
                )

                arguments[signame] = arg_backend

                signame = signame + "_arg"
                setattr(self, signame, SignalRW(arg_backend))
                config.append(getattr(self, signame))

        elif isinstance(arg_dtype, TupleOf):
            raise NotImplementedError

        elif isinstance(arg_dtype, atomic_dtypes):
            arg_backend = SECoP_CMD_IO_Backend(
                path=arg_path,
                frappy_datatype=arg_dtype,
                sig_datainfo=datainfo["argument"],
            )
            signame = path._accessible_name + "_arg"
            setattr(self, signame, SignalRW(arg_backend))
            config.append(getattr(self, signame))
            arguments[signame] = arg_backend

        # Result Signals  (read Signals)
        res_path = path.append("result")

        if isinstance(res_dtype, StructOf):
            for signame, sig_desc in datainfo["result"]["members"].items():
                res_backend = SECoP_CMD_IO_Backend(
                    path=res_path.append(signame),
                    frappy_datatype=res_dtype.members.get(signame),
                    sig_datainfo=sig_desc,
                )
                result[signame] = res_backend

                signame = signame + "_res"
                setattr(self, signame, SignalR(res_backend))
                read.append(getattr(self, signame))

        elif isinstance(res_dtype, TupleOf):
            raise NotImplementedError

        elif isinstance(res_dtype, atomic_dtypes):
            res_backend = SECoP_CMD_IO_Backend(
                path=res_path,
                frappy_datatype=res_dtype,
                sig_datainfo=datainfo["result"],
            )
            signame = path._accessible_name + "_res"
            setattr(self, signame, SignalR(res_backend))
            read.append(getattr(self, signame))
            result[signame] = res_backend

        # SignalX (signal that triggers execution of the Command)
        exec_backend = SECoP_CMD_X_Backend(
            path=path,
            secclient=secclient,
            frappy_datatype=cmd_datatype,
            cmd_desc=cmd_props,
            arguments=arguments,
            result=result,
        )

        setattr(self, path._accessible_name + "_x", SignalX(exec_backend))

        self.set_readable_signals(read=read, config=config)

        super().__init__(name=dev_name)

class SECoP_ArrayOf_XDevice(StandardReadable):
    pass


class SECoP_Node_Device(StandardReadable):
    def __init__(self, secclient: AsyncFrappyClient):
        self._secclient: AsyncFrappyClient = secclient

        self.modules: Dict[str, T] = self._secclient.modules
        self.Devices: Dict[str, T] = {}

        # Name is set to sec-node equipment_id
        name = self._secclient.properties[EQUIPMENT_ID].replace(".", "-")

        config = []

        for property in self._secclient.properties:
            propb = PropertyBackend(property, self._secclient.properties)
            setattr(self, property, SignalR(backend=propb))
            config.append(getattr(self, property))

        for module, module_desc in self._secclient.modules.items():
            SECoPDeviceClass = class_from_interface(module_desc["properties"])

            setattr(self, module, SECoPDeviceClass(self._secclient, module))

        self.set_readable_signals(config=config)

        super().__init__(name=name)

    @classmethod
    async def create(cls, host: str, port: str, loop):
        # check if asyncio eventloop is running in the same thread
        if loop._thread_id == threading.current_thread().ident and loop.is_running():
            secclient = await AsyncFrappyClient.create(host=host, port=port, loop=loop)
            return SECoP_Node_Device(secclient=secclient)
        else:
            raise Exception

    @classmethod
    def create_external_loop(cls, host: str, port: str, loop):
        if loop._thread_id == threading.current_thread().ident and loop.is_running():
            raise Exception
        else:
            # Event loop is running in a different thread
            future = asyncio.run_coroutine_threadsafe(
                AsyncFrappyClient.create(host=host, port=port, loop=loop), loop
            )

            # TODO checking if connect fails

            # TODO find better solution than sleep
            time.sleep(2)
            return SECoP_Node_Device(future.result(10))

    async def disconnect(self):
        if (
            self._secclient.loop._thread_id == threading.current_thread().ident
            and self._secclient.loop.is_running()
        ):
            await self._secclient.disconnect(True)
        else:
            raise Exception

    def disconnect_external(self):
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
