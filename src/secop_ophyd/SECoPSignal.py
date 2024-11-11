import asyncio
from functools import wraps
from itertools import groupby
from typing import Any, Callable, Dict, Optional

import numpy as np
from bluesky.protocols import DataKey, Reading
from frappy.client import CacheItem
from frappy.datatypes import (
    ArrayOf,
    BLOBType,
    BoolType,
    DataType,
    FloatRange,
    IntRange,
    ScaledInteger,
    StringType,
)
from ophyd_async.core import SignalBackend, T

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


class LocalBackend(SignalBackend):
    """Class for the 'argument' and 'result' Signal backends of a SECoP_CMD_Device.
    These Signals act as a local cache for storing the command argument and result.

    """

    def __init__(
        self, path: Path, secop_dtype_obj: DataType, sig_datainfo: dict
    ) -> None:
        """Initialize SECoP_CMD_IO_Backend

        :param path: Path to the command in the secclient module dict
        :type path: Path
        :param SECoPdtype_obj: detailed SECoP datatype object for bidirectional
        conversion between JSON to and numpy arrays
        :type SECoPdtype_obj: DataType
        :param sig_datainfo: SECoP datainfo string of the value represented
        by the signal
        :type sig_datainfo: dict
        """
        self.SECoP_type_info: SECoPdtype = SECoPdtype(secop_dtype_obj)

        self.reading: SECoPReading = SECoPReading(
            secop_dt=self.SECoP_type_info, entry=None
        )

        # module:acessible Path for reading/writing (module,accessible)
        self.path: Path = path

        # Root datainfo or memberinfo for nested datatypes
        # TODO check if this is really needed
        self.datainfo: dict = sig_datainfo

        self.callback: Callable[[Reading, Any], None] | None = None

        self.SECoPdtype_obj: DataType = secop_dtype_obj

        self.describe_dict: dict

        self.source_name = self.path._module_name + ":" + self.path._accessible_name

        self.describe_dict = {}

        self.describe_dict["source"] = self.source("")

        self.describe_dict.update(self.SECoP_type_info.describe_dict)

        for property_name, prop_val in self.datainfo.items():
            if property_name == "type":
                property_name = "SECoP_dtype"
            self.describe_dict[property_name] = prop_val

    def source(self, name: str) -> str:
        return self.source_name

    async def connect(self):
        pass

    async def put(self, value: Any | None, wait=True, timeout=None):
        self.reading.set_reading(self.SECoP_type_info.val2secop(value))

        if self.callback is not None:
            self.callback(self.reading.get_reading(), self.reading.get_value())

    async def get_datakey(self, source: str) -> DataKey:
        """Metadata like source, dtype, shape, precision, units"""
        return self.describe_dict

    async def get_reading(self) -> Reading:
        return self.reading.get_reading()

    async def get_value(self) -> T:
        return self.reading.get_value()

    def set_callback(self, callback: Callable[[Reading, Any], None] | None) -> None:
        self.callback = callback


# TODO add return of Asyncstatus
class SECoPXBackend(SignalBackend):
    """
    Signal backend for SignalX of a SECoP_CMD_Device, that handles command execution

    """

    def __init__(
        self,
        path: Path,
        secclient: AsyncFrappyClient,
        argument: LocalBackend | None,
        result: LocalBackend | None,
    ) -> None:
        """Initializes SECoP_CMD_X_Backend

        :param path: Path to the command in the secclient module dict
        :type path: Path
        :param secclient: SECoP client providing communication to the SEC Node
        :type secclient: AsyncFrappyClient
        :param argument: Refence to Argument Signal
        :type argument: SECoP_CMD_IO_Backend | None
        :param result: Reference to Result Signal
        :type result: SECoP_CMD_IO_Backend | None
        """

        self._secclient: AsyncFrappyClient = secclient

        # module:acessible Path for reading/writing (module,accessible)
        self.path: Path = path

        self.callback: Callable
        self.argument: LocalBackend | None = argument
        self.result: LocalBackend | None = result

        self.source_name = self.path._module_name + ":" + self.path._accessible_name

    def source(self, name: str) -> str:
        return self.source_name

    async def connect(self):
        pass

    async def put(self, value: Any | None, wait=True, timeout=None):

        if self.argument is None:
            argument = None
        else:
            argument = await self.argument.get_value()

        res, qualifiers = await asyncio.wait_for(
            fut=self._secclient.exec_command(
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
            val = self.result.SECoP_type_info.secop2val(res)

            await self.result.put(val)

    async def get_datakey(self, source: str) -> DataKey:
        """Metadata like source, dtype, shape, precision, units"""
        res = {}

        res["source"] = self.source("")

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


class SECoPParamBackend(SignalBackend):
    """Standard backend for a Signal that represents SECoP Parameter"""

    def __init__(self, path: Path, secclient: AsyncFrappyClient) -> None:
        """_summary_

        :param path: Path to the parameter in the secclient module dict
        :type path: Path
        :param secclient: SECoP client providing communication to the SEC Node
        :type secclient: AsyncFrappyClient
        """
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

        self.source_name = (
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

        self.describe_dict["source"] = self.source_name

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
            if property_name == "unit":
                property_name = "units"
            self.describe_dict[property_name] = prop_val

    def source(self, name: str) -> str:
        return self.source_name

    async def connect(self):
        pass

    async def put(self, value: Any | None, wait=True, timeout=None):
        # convert to frappy compatible Format
        secop_val = self.SECoP_type_info.val2secop(value)

        await asyncio.wait_for(
            self._secclient.set_parameter(**self.get_param_path(), value=secop_val),
            timeout=timeout,
        )

    async def get_datakey(self, source: str) -> DataKey:
        """Metadata like source, dtype, shape, precision, units"""
        return self.describe_dict

    async def get_reading(self) -> Reading:
        dataset = await self._secclient.get_parameter(
            **self.get_param_path(), trycache=False
        )

        sec_reading = SECoPReading(entry=dataset, secop_dt=self.SECoP_type_info)

        return sec_reading.get_reading()

    async def get_value(self) -> T:
        dataset: Reading = await self.get_reading()

        return dataset["value"]  # type: ignore

    def set_callback(self, callback: Callable[[Reading, Any], None] | None) -> None:
        def awaitify(sync_func):
            """Wrap a synchronous callable to allow ``await``'ing it"""

            @wraps(sync_func)
            async def async_func(*args, **kwargs):
                return sync_func(*args, **kwargs)

            return async_func

        def updateItem(module, parameter, entry: CacheItem):  # noqa: N802
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

    def get_unit(self):
        return self.describe_dict.get("units", None)

    def is_number(self) -> bool:
        if (
            self.describe_dict["dtype"] == "number"
            or self.describe_dict["dtype"] == "integer"
        ):
            return True

        return False


class PropertyBackend(SignalBackend):
    """Readonly backend for static SECoP Properties of Nodes/Modules"""

    def __init__(
        self, prop_key: str, property_dict: Dict[str, T], secclient: AsyncFrappyClient
    ) -> None:
        """Initializes PropertyBackend

        :param prop_key: Name of Property
        :type prop_key: str
        :param propertyDict: Dicitonary containing all properties of Node/Module
        :type propertyDict: Dict[str, T]
        :param secclient: SECoP client providing communication to the SEC Node
        :type secclient: AsyncFrappyClient
        """
        # secclient

        self._property_dict = property_dict
        self._prop_key = prop_key
        self._datatype = self._get_datatype()
        self._secclient: AsyncFrappyClient = secclient
        # TODO full property path
        self.source_name = prop_key
        self.shape: list = []

    def source(self, name: str) -> str:
        return str(self.source_name)

    def _get_datatype(self) -> str:
        prop_val = self._property_dict[self._prop_key]

        if isinstance(prop_val, str):
            return "string"
        if isinstance(prop_val, (int, float)):
            return "number"
        if isinstance(prop_val, bool):
            return "bool"
        if isinstance(
            prop_val, (dict, tuple)
        ):  # SECoP Structs/tuples --> numpy ndarray
            return "array"
        if isinstance(prop_val, list):
            self.shape = [len(prop_val)]
            if prop_val.__len__ == 0:
                # empty list
                return "number"

            # checkk if list contains multiple types --> SECoP tuple
            if len(list(groupby(prop_val, lambda i: type(i)))) != 1:
                return "array"

            if isinstance(prop_val[0], str):
                return "string"
            if isinstance(prop_val[0], (int, float)):
                return "number"
            if isinstance(prop_val[0], bool):
                return "bool"

        if isinstance(prop_val, dict):
            return "array"

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

    async def get_datakey(self, source: str) -> DataKey:
        """Metadata like source, dtype, shape, precision, units"""
        description = {}

        description["source"] = self.source("")
        description["dtype"] = self._get_datatype()
        description["shape"] = self.shape  # type: ignore

        if self._prop_key == "meaning":
            description["dtype_descr"] = get_meaning_np_dtype(
                self._property_dict[self._prop_key]
            )  # type: ignore

        return description

    async def get_reading(self) -> Reading:
        """The current value, timestamp and severity"""

        return {
            "value": await self.get_value(),
            "timestamp": self._secclient.conn_timestamp,
        }

    async def get_value(self) -> T:
        """The current value"""

        if self._get_datatype() == "array":
            return np.array(self._property_dict[self._prop_key])

        return self._property_dict[self._prop_key]

    def set_callback(self, callback: Callable[[Reading, Any], None] | None) -> None:
        pass


def get_meaning_np_dtype(meaning: dict[str, Any]) -> list:
    dtype_descr = []
    for key in meaning.keys():
        match key:
            case "function":
                dtype_descr.append(("function", "U50"))
            case "belongs_to":
                dtype_descr.append(("belongs_to", "U50"))
            case "importance":
                dtype_descr.append(("importance", "i4"))
            case "key":
                dtype_descr.append(("key", "U100"))
            case "link":
                dtype_descr.append(("link", "U200"))

    return dtype_descr
