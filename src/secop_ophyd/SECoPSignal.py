import asyncio
import warnings
from functools import wraps
from typing import Any, Callable, Dict, Optional

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
    StructOf,
    TupleOf,
)
from ophyd_async.core import Callback, SignalBackend, T

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


# max depth for datatypes supported by tiled/databroker
MAX_DEPTH = 1


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

        self.callback: Callback | None = None

        self.SECoPdtype_obj: DataType = secop_dtype_obj

        self.describe_dict: dict

        self.source_name = self.path._module_name + ":" + self.path._accessible_name

        self.describe_dict = {}

        self.describe_dict["source"] = self.source("", True)

        self.describe_dict.update(self.SECoP_type_info.get_datakey())

        for property_name, prop_val in self.datainfo.items():
            if property_name == "type":
                property_name = "SECoP_dtype"
            self.describe_dict[property_name] = prop_val

        super().__init__(datatype=self.SECoP_type_info.np_datatype)

    def source(self, name: str, read: bool) -> str:
        return self.source_name

    async def connect(self, timeout: float):
        pass

    async def put(self, value: Any | None, wait=True):
        self.reading.set_reading(self.SECoP_type_info.val2secop(value))

        if self.callback is not None:
            self.callback(self.reading.get_reading())

    async def get_datakey(self, source: str) -> DataKey:
        """Metadata like source, dtype, shape, precision, units"""
        return self.describe_dict

    async def get_reading(self) -> Reading:
        return self.reading.get_reading()

    async def get_value(self) -> T:
        return self.reading.get_value()

    async def get_setpoint(self) -> T:
        return await self.get_value()

    def set_callback(self, callback: Callback[T] | None) -> None:
        self.callback = callback  # type: ignore[assignment]


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
        super().__init__(datatype=None)

    def source(self, name: str, read: bool) -> str:
        return self.source_name

    async def connect(self, timeout: float):
        pass

    async def put(self, value: Any | None, wait=True):

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
            timeout=None,
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

        res["source"] = self.source("", True)

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

    def set_callback(self, callback: Callback[T] | None) -> None:
        pass

    async def get_setpoint(self) -> T:
        raise Exception(
            "Cannot read _x Signal, it has no value and is only"
            + " used to trigger Command execution"
        )


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

        self.SECoPdtype_str: str
        self.SECoPdtype_obj: DataType = self._param_description["datatype"]

        self.SECoP_type_info: SECoPdtype = SECoPdtype(self.SECoPdtype_obj)

        if self.SECoP_type_info.max_depth > MAX_DEPTH:
            warnings.warn(
                f"The datatype of parameter '{path._accessible_name}' has a maximum "
                f"depth of {self.SECoP_type_info.max_depth}. Tiled & Databroker only "
                f"support a Depth upto {MAX_DEPTH} "
                f"dtype_descr: {self.SECoP_type_info.dtype_descr}"
            )

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
        self.describe_dict.update(self.SECoP_type_info.get_datakey())

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

        super().__init__(datatype=self.SECoP_type_info.np_datatype)

    def source(self, name: str, read: bool) -> str:
        return self.source_name

    async def connect(self, timeout: float):
        pass

    async def put(self, value: Any | None, wait=True):
        # convert to frappy compatible Format
        secop_val = self.SECoP_type_info.val2secop(value)

        # frappy client has no ability to just send a secop message without
        # waiting for a reply
        await asyncio.wait_for(
            self._secclient.set_parameter(**self.get_param_path(), value=secop_val),
            timeout=None,
        )

    async def get_datakey(self, source: str) -> DataKey:
        """Metadata like source, dtype, shape, precision, units"""

        if self.SECoP_type_info._is_composite or isinstance(
            self.SECoPdtype_obj, ArrayOf
        ):
            # getlast cached value
            dataset = await self._secclient.get_parameter(
                **self.get_param_path(), trycache=True
            )

            # this ensures the datakey is updated to the latest cached value
            SECoPReading(entry=dataset, secop_dt=self.SECoP_type_info)
            self.describe_dict.update(self.SECoP_type_info.get_datakey())

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

    async def get_setpoint(self) -> T:
        return await self.get_value()

    def set_callback(self, callback: Callback[T] | None) -> None:
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
                async_callback(reading=data.get_reading()),
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
        self._prop_value = self._property_dict[self._prop_key]
        self.SECoPdtype_obj: DataType = secop_dtype_obj_from_json(self._prop_value)
        self.SECoP_type_info: SECoPdtype = SECoPdtype(self.SECoPdtype_obj)

        if self.SECoP_type_info.max_depth > MAX_DEPTH:
            warnings.warn(
                f"The datatype of parameter '{prop_key}' has a maximum"
                f"depth of {self.SECoP_type_info.max_depth}. Tiled & Databroker only"
                f"support a Depth upto {MAX_DEPTH}"
                f"dtype_descr: {self.SECoP_type_info.dtype_descr}"
            )

        # SECoP metadata is static and can only change when connection is reset
        self.describe_dict = {}
        self.source_name = prop_key
        self.describe_dict["source"] = self.source_name

        # add gathered keys from SECoPdtype:
        self.describe_dict.update(self.SECoP_type_info.get_datakey())

        self._secclient: AsyncFrappyClient = secclient
        # TODO full property path

        super().__init__(datatype=self.SECoP_type_info.np_datatype)

    def source(self, name: str, read: bool) -> str:
        return str(self.source_name)

    async def connect(self, timeout: float):
        """Connect to underlying hardware"""
        pass

    async def put(self, value: Optional[T], wait=True):
        """Put a value to the PV, if wait then wait for completion for up to timeout"""
        # Properties are readonly
        pass

    async def get_datakey(self, source: str) -> DataKey:
        """Metadata like source, dtype, shape, precision, units"""
        return self.describe_dict

    async def get_reading(self) -> Reading:
        dataset = CacheItem(
            value=self._prop_value, timestamp=self._secclient.conn_timestamp
        )

        sec_reading = SECoPReading(entry=dataset, secop_dt=self.SECoP_type_info)

        return sec_reading.get_reading()

    async def get_value(self) -> T:
        dataset: Reading = await self.get_reading()

        return dataset["value"]  # type: ignore

    async def get_setpoint(self) -> T:
        return await self.get_value()

    def set_callback(self, callback: Callback[T] | None) -> None:
        pass


def secop_dtype_obj_from_json(prop_val):
    if isinstance(prop_val, str):
        return StringType()

    if isinstance(prop_val, (int, float)):
        return FloatRange()

    if isinstance(prop_val, bool):
        return BoolType()

    if isinstance(prop_val, dict):  # SECoP Structs/tuples --> numpy ndarray
        members = {}
        for key, elem in prop_val.items():
            members[key] = secop_dtype_obj_from_json(elem)

        return StructOf(**members)

    if isinstance(prop_val, list):
        # empty list, cannot infer proper type
        if not prop_val:
            return ArrayOf(FloatRange())
        # check if all elements have same Type:
        if all(isinstance(elem, type(prop_val[0])) for elem in prop_val):
            members = secop_dtype_obj_from_json(prop_val[0])
            return ArrayOf(members)
        else:
            members = []  # type: ignore
            for elem in prop_val:
                members.append(secop_dtype_obj_from_json(elem))  # type: ignore
            return TupleOf(*members)

    raise Exception(
        f"""unsupported datatype in Property:  {str(prop_val.__class__.__name__)}\n
        propval: {prop_val}"""
    )
