import asyncio
import collections.abc
from functools import wraps
from typing import Any, Callable, Dict, Optional

from bluesky.protocols import Descriptor, Reading
from frappy.client import CacheItem
from frappy.datatypes import (
    ArrayOf,
    BLOBType,
    BoolType,
    CommandType,
    DataType,
    FloatRange,
    IntRange,
    ScaledInteger,
    StringType,
)
from ophyd_async.core.signal_backend import SignalBackend
from ophyd_async.core.utils import T

from secop_ophyd.AsyncFrappyClient import AsyncFrappyClient
from secop_ophyd.util import Path, SECoPdtype, SECoPReading, deep_get

atomic_dtypes = (
    StringType,
    ScaledInteger,
    IntRange,
    FloatRange,
    BoolType,
    BLOBType,
    ArrayOf,
)


def get_read_str(value, timestamp):
    return {"value": value, "timestamp": timestamp}


def get_shape(datainfo):
    # print(datainfo)
    SECoPdtype = datainfo.get("type", None)

    if SECoPdtype.__eq__("array"):
        return [1, datainfo.get("maxlen", None)]
    elif SECoPdtype.__eq__("tuple"):
        memeberArr = datainfo.get("members", None)
        return [1, len(memeberArr)]
    else:
        return []


class SECoP_CMD_IO_Backend(SignalBackend):
    def __init__(
        self, path: Path, SECoPdtype_obj: DataType, sig_datainfo: dict
    ) -> None:
        self.SECoP_type_info: SECoPdtype = SECoPdtype(SECoPdtype_obj)

        self.reading: SECoPReading = SECoPReading(
            secop_dt=self.SECoP_type_info, entry=None
        )

        # module:acessible Path for reading/writing (module,accessible)
        self.path: Path = path

        # Root datainfo or memberinfo for nested datatypes
        self.datainfo: dict = sig_datainfo

        self.callback: Callable[[Reading, Any], None] | None = None

        self.SECoPdtype_obj: DataType = SECoPdtype_obj

        self.describe_dict: dict

        self.source = self.path._module_name + ":" + self.path._accessible_name

        self.describe_dict = {}

        self.describe_dict["source"] = self.source

        self.describe_dict.update(self.SECoP_type_info.describe_dict)

        for property_name, prop_val in self.datainfo.items():
            if property_name == "type":
                property_name = "SECoP_dtype"
            self.describe_dict[property_name] = prop_val

    async def connect(self):
        pass

    async def put(self, value: Any | None, wait=True, timeout=None):
        self.reading.set_reading(self.SECoP_type_info.Val2SECoP(value))

        if self.callback is not None:
            self.callback(self.reading.get_reading(), self.reading.get_value())

    async def get_descriptor(self) -> Descriptor:
        return self.describe_dict

    async def get_reading(self) -> Reading:
        return self.reading.get_reading()

    async def get_value(self) -> T:
        return self.reading.get_value()

    def set_callback(self, callback: Callable[[Reading, Any], None] | None) -> None:
        self.callback = callback


# TODO add return of Asyncstatus
class SECoP_CMD_X_Backend(SignalBackend):
    def __init__(
        self,
        path: Path,
        secclient: AsyncFrappyClient,
        frappy_datatype: CommandType,
        cmd_desc: dict,
        argument: SECoP_CMD_IO_Backend | None,
        result: SECoP_CMD_IO_Backend | None,
    ) -> None:
        self._secclient: AsyncFrappyClient = secclient

        # module:acessible Path for reading/writing (module,accessible)
        self.path: Path = path

        self._cmd_desc: dict = cmd_desc

        self.callback: Callable
        self.argument: SECoP_CMD_IO_Backend | None = argument
        self.result: SECoP_CMD_IO_Backend | None = result

        # Root datainfo or memberinfo for nested datatypes
        self.datainfo: dict = deep_get(self._cmd_desc["datainfo"], self.path._dev_path)

        self.frappy_datatype: CommandType = frappy_datatype

        self.source = self.path._module_name + ":" + self.path._accessible_name

    async def connect(self):
        pass

    async def put(self, value: Any | None, wait=True, timeout=None):
        if self.argument is None:
            argument = None
        else:
            argument = await self.argument.get_value()

        res, qualifiers = await asyncio.wait_for(
            fut=self._secclient.execCommand(
                module=self.path._module_name,
                command=self.path._accessible_name,
                argument=argument,
            ),
            timeout=timeout,
        )

        # write return Value to corresponding Backend

        if self.result is None:
            return
        else:
            val = self.result.SECoP_type_info.SECoP2Val(res)

            await self.result.put(val)

    async def get_descriptor(self) -> Descriptor:
        res = {}

        res["source"] = self.source

        # ophyd datatype (some SECoP datatypeshaveto be converted)
        # signalx has no datatype and is never read
        res["dtype"] = "None"

        # get shape from datainfo and SECoPtype

        res["shape"] = []  # type: ignore

        return res

    async def get_reading(self) -> Reading:
        raise Exception(
            "Cannot read _x Signal, it has no value and is only"
            + " used to trigger Command execution"
        )

    async def get_value(self) -> T:
        raise Exception(
            "Cannot read _x Signal, it has no value and is only"
            + " used to trigger Command execution"
        )

    def set_callback(self, callback: Callable[[Reading, Any], None] | None) -> None:
        pass


class SECoP_Param_Backend(SignalBackend):
    def __init__(self, path: Path, secclient: AsyncFrappyClient) -> None:
        # secclient
        self._secclient: AsyncFrappyClient = secclient

        # module:acessible Path for reading/writing (module,accessible)
        self.path: Path = path

        self._param_description: dict = self._get_param_desc()

        # Root datainfo or memberinfo for nested datatypes
        self.datainfo: dict = deep_get(
            self._param_description["datainfo"], self.path.get_memberinfo_path()
        )

        self.readonly = self._param_description.get("readonly")

        self.datatype: str
        self.SECoPdtype_str: str
        self.SECoPdtype_obj: DataType = self._param_description["datatype"]

        self.SECoP_type_info: SECoPdtype = SECoPdtype(self.SECoPdtype_obj)

        self.describe_dict: dict = {}

        self.source = (
            secclient.uri
            + ":"
            + secclient.nodename
            + ":"
            + self.path._module_name
            + ":"
            + self.path._accessible_name
        )

        # SECoP metadata is static and can only change when connection is reset
        self.describe_dict = {}

        self.describe_dict["source"] = self.source

        # add gathered keys from SECoPdtype:
        self.describe_dict.update(self.SECoP_type_info.describe_dict)

        for property_name, prop_val in self._param_description.items():
            # skip datainfo (treated seperately)
            if property_name == "datainfo" or property_name == "datatype":
                continue
            self.describe_dict[property_name] = prop_val

        for property_name, prop_val in self.datainfo.items():
            if property_name == "type":
                property_name = "SECoP_dtype"
            self.describe_dict[property_name] = prop_val

    async def connect(self):
        pass

    async def put(self, value: Any | None, wait=True, timeout=None):
        # convert to frappy compatible Format
        secop_val = self.SECoP_type_info.Val2SECoP(value)

        await asyncio.wait_for(
            self._secclient.setParameter(**self.get_param_path(), value=secop_val),
            timeout=timeout,
        )

    async def get_descriptor(self) -> Descriptor:
        return self.describe_dict

    async def get_reading(self) -> Reading:
        dataset = await self._secclient.getParameter(
            **self.get_param_path(), trycache=False
        )

        sec_reading = SECoPReading(entry=dataset, secop_dt=self.SECoP_type_info)

        return sec_reading.get_reading()

    async def get_value(self) -> T:
        dataset: Reading = await self.get_reading()

        return dataset.value  # type: ignore

    def set_callback(self, callback: Callable[[Reading, Any], None] | None) -> None:
        def awaitify(sync_func):
            """Wrap a synchronous callable to allow ``await``'ing it"""

            @wraps(sync_func)
            async def async_func(*args, **kwargs):
                return sync_func(*args, **kwargs)

            return async_func

        def updateItem(module, parameter, entry: CacheItem):
            data = SECoPReading(secop_dt=self.SECoP_type_info, entry=entry)
            async_callback = awaitify(callback)

            asyncio.run_coroutine_threadsafe(
                async_callback(reading=data.get_reading(), value=data.get_value()),
                self._secclient.loop,
            )

        if callback is not None:
            self._secclient.register_callback(self.get_path_tuple(), updateItem)
        else:
            self._secclient.unregister_callback(self.get_path_tuple(), updateItem)

    def _get_param_desc(self) -> dict:
        return deep_get(self._secclient.modules, self.path.get_param_desc_path())

    def get_param_path(self):
        return self.path.get_param_path()

    def get_path_tuple(self):
        return self.path.get_path_tuple()


class PropertyBackend(SignalBackend):
    """read backend for a SECoP Properties"""

    def __init__(
        self, prop_key: str, propertyDict: Dict[str, T], secclient: AsyncFrappyClient
    ) -> None:
        # secclient

        self._property_dict = propertyDict
        self._prop_key = prop_key
        self._datatype = self._get_datatype()
        self._secclient: AsyncFrappyClient = secclient
        # TODO full property path
        self.source = prop_key

    def _get_datatype(self) -> str:
        prop_val = self._property_dict[self._prop_key]

        if isinstance(prop_val, str):
            return "string"
        if isinstance(prop_val, (int, float)):
            return "number"
        if isinstance(prop_val, collections.abc.Sequence):
            return "array"
        if isinstance(prop_val, bool):
            return "bool"

        raise Exception(
            "unsupported datatype in Node Property: " + str(prop_val.__class__.__name__)
        )

    async def connect(self):
        """Connect to underlying hardware"""
        pass

    async def put(self, value: Optional[T], wait=True, timeout=None):
        """Put a value to the PV, if wait then wait for completion for up to timeout"""
        # Properties are readonly
        pass

    async def get_descriptor(self) -> Descriptor:
        """Metadata like source, dtype, shape, precision, units"""
        description = {}

        description["source"] = str(self.source)
        description["dtype"] = self._get_datatype()
        description["shape"] = []  # type: ignore

        return description

    async def get_reading(self) -> Reading:
        """The current value, timestamp and severity"""
        return get_read_str(
            self._property_dict[self._prop_key],
            timestamp=self._secclient.conn_timestamp,
        )

    async def get_value(self) -> T:
        """The current value"""
        return self._property_dict[self._prop_key]

    def set_callback(self, callback: Callable[[Reading, Any], None] | None) -> None:
        pass


class ReadonlyError(Exception):
    """Raised, when Secop parameter is readonly, but was used to
    construct rw ophyd Signal"""

    pass

    def __init__(self, prefix, name, module_name, param_desc, secclient, kind) -> None:
        super().__init__(prefix, name, module_name, param_desc, secclient, kind)
        self.dtype = "string"


# TODO: Array: shape for now only for the first Dim, later maybe recursive??
