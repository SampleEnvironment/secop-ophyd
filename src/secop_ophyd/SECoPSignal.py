import asyncio
import warnings
from functools import wraps
from typing import Any, Callable

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
from ophyd_async.core import (
    Callback,
    SignalBackend,
    SignalDatatypeT,
    StandardReadableFormat,
    StrictEnum,
)

from secop_ophyd.AsyncFrappyClient import AsyncFrappyClient
from secop_ophyd.util import Path, SECoPDataKey, SECoPdtype, SECoPReading, deep_get

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


class AttributeType(StrictEnum):
    PARAMETER = "parameter"
    PROPERTY = "property"


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
        return describedict_to_datakey(self.describe_dict)

    async def get_reading(self) -> Reading[SignalDatatypeT]:
        return self.reading.get_reading()

    async def get_value(self) -> SignalDatatypeT:
        return self.reading.get_value()

    async def get_setpoint(self) -> SignalDatatypeT:
        return await self.get_value()

    def set_callback(self, callback: Callback[Reading[SignalDatatypeT]] | None) -> None:
        self.callback = callback  # type: ignore[assignment]


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

        return DataKey(shape=[], dtype="string", source=self.source("", True))

    async def get_reading(self) -> Reading[SignalDatatypeT]:
        raise Exception(
            "Cannot read _x Signal, it has no value and is only"
            + " used to trigger Command execution"
        )

    async def get_value(self) -> SignalDatatypeT:
        raise Exception(
            "Cannot read _x Signal, it has no value and is only"
            + " used to trigger Command execution"
        )

    def set_callback(self, callback: Callback[Reading[SignalDatatypeT]] | None) -> None:
        pass

    async def get_setpoint(self) -> SignalDatatypeT:
        raise Exception(
            "Cannot read _x Signal, it has no value and is only"
            + " used to trigger Command execution"
        )


class SECoPBackend(SignalBackend[SignalDatatypeT]):
    """Unified backend for SECoP Parameters and Properties.


    This allows a single backend type to be used in signal_backend_factory,
    with deferred initialization based on annotation metadata.
    """

    format: StandardReadableFormat
    attribute_type: str | None
    _module_name: str | None
    _attribute_name: str | None  # parameter or property name
    _secclient: AsyncFrappyClient
    path_str: str
    SECoPdtype_obj: DataType
    SECoP_type_info: SECoPdtype
    describe_dict: dict

    def __init__(
        self,
        datatype: type[SignalDatatypeT] | None,
        path: str | None = None,
        attribute_type: str | None = None,
        secclient: AsyncFrappyClient | None = None,
    ):
        """Initialize backend (supports deferred initialization).

        Args:
            datatype: Optional datatype for the signal
            path: Optional path for immediate initialization (module:param or prop_key)
            secclient: Optional SECoP client for immediate initialization
        """
        self._module_name = None
        self._attribute_name = None

        self.attribute_type = attribute_type

        if secclient:
            self._secclient = secclient

        self.path_str = path or ""

        if path and secclient:

            if path.count(":") == 0:
                self._module_name = None
                self._attribute_name = path
            else:
                self._module_name, self._attribute_name = path.split(":", maxsplit=1)

        super().__init__(datatype)

    def init_parameter_from_introspection(
        self,
        datatype: type[SignalDatatypeT],
        path: str,
        secclient: AsyncFrappyClient,
    ):
        if self.attribute_type is not None:

            if secclient != self._secclient:
                raise RuntimeError(
                    "Backend already initialized with a different SECoP client, cannot "
                    "re-initialize"
                )

            if self.attribute_type != AttributeType.PARAMETER:
                raise RuntimeError(
                    f"Backend already initialized as {self.attribute_type}, "
                    f"cannot re-initialize as PARAMETER"
                )

        self.attribute_type = AttributeType.PARAMETER

        module_name, parameter_name = path.split(":", maxsplit=1)

        self._module_name = module_name
        self._attribute_name = parameter_name
        self._secclient = secclient

        self.datatype = datatype

        self.path_str = path

    def init_property_from_introspection(
        self, datatype: type[SignalDatatypeT], path: str, secclient: AsyncFrappyClient
    ):
        if self.attribute_type is not None:

            if secclient != self._secclient:
                raise RuntimeError(
                    "Backend already initialized with a different SECoP client, cannot "
                    "re-initialize"
                )

            if self.attribute_type != AttributeType.PROPERTY:
                raise RuntimeError(
                    f"Backend already initialized as {self.attribute_type}, cannot "
                    f"re-initialize as PROPERTY"
                )

        self.attribute_type = AttributeType.PROPERTY
        if path.count(":") == 0:
            module_name = None
            property_name = path
        else:
            module_name, property_name = path.split(":", maxsplit=1)

        self._module_name = module_name
        self._attribute_name = property_name

        self._secclient = secclient
        self.datatype = datatype

        self.path_str = path

    def source(self, name: str, read: bool) -> str:
        return self._secclient.host + ":" + self._secclient.port + ":" + self.path_str

    async def connect(self, timeout: float):
        """Connect and initialize backend (handles both parameters and properties)."""
        await self._secclient.connect()

        match self.attribute_type:
            case AttributeType.PROPERTY:
                await self._init_property()
            case AttributeType.PARAMETER:
                await self._init_parameter()

    async def _init_parameter(self):
        """Initialize as a parameter signal."""
        self._param_description: dict = self._get_param_desc()

        if not hasattr(self, "format"):
            match self._param_description.get("_signal_format", None):
                case "HINTED_SIGNAL":
                    self.format = StandardReadableFormat.HINTED_SIGNAL
                case "HINTED_UNCACHED_SIGNAL":
                    self.format = StandardReadableFormat.HINTED_UNCACHED_SIGNAL
                case "UNCACHED_SIGNAL":
                    self.format = StandardReadableFormat.UNCACHED_SIGNAL
                case _:
                    self.format = StandardReadableFormat.CONFIG_SIGNAL

        # Root datainfo or memberinfo for nested datatypes
        self.datainfo: dict = self._param_description["datainfo"]
        self.readonly = self._param_description.get("readonly")
        self.SECoPdtype_obj = self._param_description["datatype"]
        self.SECoP_type_info = SECoPdtype(self.SECoPdtype_obj)

        if self.SECoP_type_info.max_depth > MAX_DEPTH:
            warnings.warn(
                f"The datatype of parameter '{self._attribute_name}' has a maximum "
                f"depth of {self.SECoP_type_info.max_depth}. Tiled & Databroker only "
                f"support a Depth upto {MAX_DEPTH} "
                f"dtype_descr: {self.SECoP_type_info.dtype_descr}"
            )

        self.source_name = (
            self._secclient.uri
            + ":"
            + self._secclient.nodename
            + ":"
            + self._module_name
            + ":"
            + self._attribute_name
        )

        self.describe_dict = {}
        self.describe_dict["source"] = self.source_name
        self.describe_dict.update(self.SECoP_type_info.get_datakey())

        for property_name, prop_val in self._param_description.items():
            if property_name in ("datainfo", "datatype"):
                continue
            self.describe_dict[property_name] = prop_val

        for property_name, prop_val in self.datainfo.items():
            if property_name == "type":
                property_name = "SECoP_dtype"
            if property_name == "unit":
                property_name = "units"
            self.describe_dict[property_name] = prop_val

        self.datatype = self.SECoP_type_info.np_datatype

    async def _init_property(self):
        """Initialize as a property signal."""

        if self._module_name:
            module_desc = self._secclient.modules[self._module_name]
            self._property_dict = module_desc["properties"]
        else:
            self._property_dict = self._secclient.properties

        self._prop_value = self._property_dict[self._attribute_name]
        self.SECoPdtype_obj = secop_dtype_obj_from_json(self._prop_value)
        self.SECoP_type_info = SECoPdtype(self.SECoPdtype_obj)

        if self.SECoP_type_info.max_depth > MAX_DEPTH:
            warnings.warn(
                f"The datatype of property '{self._attribute_name}' has a maximum "
                f"depth of {self.SECoP_type_info.max_depth}. Tiled & Databroker only "
                f"support a Depth upto {MAX_DEPTH} "
                f"dtype_descr: {self.SECoP_type_info.dtype_descr}"
            )

        self.describe_dict = {}
        self.describe_dict["source"] = self.path_str
        self.describe_dict.update(self.SECoP_type_info.get_datakey())

        # Properties are always readonly
        self.format = StandardReadableFormat.CONFIG_SIGNAL
        self.readonly = True
        self.datatype = self.SECoP_type_info.np_datatype

    async def put(self, value: Any | None, wait=True):
        """Put a value to the parameter. Properties are readonly."""

        if self.attribute_type == AttributeType.PROPERTY:
            # Properties are readonly
            raise RuntimeError(
                f"Cannot set property '{self._attribute_name}', properties are readonly"
            )

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
        if self.attribute_type == AttributeType.PROPERTY:
            # Properties have static metadata
            return describedict_to_datakey(self.describe_dict)

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

        return describedict_to_datakey(self.describe_dict)

    async def get_reading(self) -> Reading[SignalDatatypeT]:
        """Get reading, handling both parameters and properties."""
        if self.attribute_type == AttributeType.PROPERTY:
            # Properties have static values
            dataset = CacheItem(
                value=self._prop_value, timestamp=self._secclient.conn_timestamp
            )
            sec_reading = SECoPReading(entry=dataset, secop_dt=self.SECoP_type_info)
            return sec_reading.get_reading()

        else:
            # Parameters are fetched from SECoP
            dataset = await self._secclient.get_parameter(
                **self.get_param_path(), trycache=True
            )
            sec_reading = SECoPReading(entry=dataset, secop_dt=self.SECoP_type_info)
            return sec_reading.get_reading()

    async def get_value(self) -> SignalDatatypeT:
        dataset: Reading = await self.get_reading()
        return dataset["value"]  # type: ignore

    async def get_setpoint(self) -> SignalDatatypeT:
        return await self.get_value()

    def set_callback(self, callback: Callback[Reading[SignalDatatypeT]] | None) -> None:
        if self.attribute_type == AttributeType.PROPERTY:
            # Properties are static, no callbacks
            return

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
        return deep_get(
            self._secclient.modules,
            [self._module_name, "parameters", self._attribute_name],
        )

    def get_param_path(self):
        return {"module": self._module_name, "parameter": self._attribute_name}

    def get_path_tuple(self):
        return (self._module_name, self._attribute_name)


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


def describedict_to_datakey(describe_dict: dict) -> SECoPDataKey:
    """Convert a DataKey to a SECoPDataKey"""
    datakey = SECoPDataKey(
        dtype=describe_dict["dtype"],
        shape=describe_dict["shape"],
        source=describe_dict["source"],
        SECOP_datainfo=describe_dict["SECOP_datainfo"],
    )

    if "units" in describe_dict:
        datakey["units"] = describe_dict["units"]

    if "dtype_str" in describe_dict:
        datakey["dtype_str"] = describe_dict["dtype_str"]

    if "dtype_descr" in describe_dict:
        datakey["dtype_descr"] = describe_dict["dtype_descr"]

    if "dtype_numpy" in describe_dict:
        datakey["dtype_numpy"] = describe_dict["dtype_numpy"]

    return datakey
