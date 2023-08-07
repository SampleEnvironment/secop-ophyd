from secop_ophyd.AsyncFrappyClient import AsyncFrappyClient, SECoPReading
from secop_ophyd.util import deep_get, Path
from typing import Any, Dict, Optional

from ophyd.v2.core import T, SignalBackend
from bluesky.protocols import Reading, Descriptor

from frappy.datatypes import (
    StructOf,
    TupleOf,
    CommandType,
    DataType,
    IntRange,
    FloatRange,
    StringType,
    BLOBType,
    BoolType,
    ScaledInteger,
    ArrayOf,
)


import time
from frappy.client import CacheItem
import collections.abc
import asyncio
import copy

from functools import wraps


from typing import Callable

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
        self, path: Path, frappy_datatype: DataType, sig_datainfo: dict
    ) -> None:
        self.reading: SECoPReading = SECoPReading(None)
        self.value = None

        # module:acessible Path for reading/writing (module,accessible)
        self.path: Path = path

        # Root datainfo or memberinfo for nested datatypes
        self.datainfo: dict = sig_datainfo

        self.callback: Callable = None

        self.frappy_datatype: DataType = frappy_datatype
        self.datatype: str
        self.SECoPdtype: str

        self._set_dtype()

        self.source = self.path._module_name + ":" + self.path._accessible_name

    async def connect(self):
        pass

    async def put(self, value: Any | None, wait=True, timeout=None):
        if isinstance(self.frappy_datatype, (StructOf, TupleOf)):
            self.value = self.frappy_datatype.from_string(value)
        else:
            self.value = self.frappy_datatype.import_value(value)

        self.reading.set_reading(value)

        if self.callback is not None:
            self.callback(self.reading.get_reading(), self.reading.get_value())

    async def get_descriptor(self) -> Descriptor:
        res = {}

        res["source"] = self.source

        # ophyd datatype (some SECoP datatypeshaveto be converted)
        res["dtype"] = self.datatype

        # get shape from datainfo and SECoPtype

        # TODO if array is ragged only first dimension is used otherwise parse the array
        if self.datainfo["type"] == "array":
            res["shape"] = [1, self.datainfo.get("maxlen", None)]
        else:
            res["shape"] = []

        for property_name, prop_val in self.datainfo.items():
            if property_name == "type":
                property_name = "SECoPtype"
            res[property_name] = prop_val

        return res

    async def get_reading(self) -> Reading:
        return self.reading.get_reading()

    async def get_value(self) -> T:
        return self.reading.get_value()

    def set_callback(self, callback: Callable[[Reading, Any], None] | None) -> None:
        self.callback = callback

    def _set_dtype(self) -> None:
        self.SECoPdtype = self.datainfo["type"]
        self.datatype = SECOP2DTYPE.get(self.SECoPdtype, None)


class SECoP_CMD_X_Backend(SignalBackend):
    def __init__(
        self,
        path: Path,
        secclient: AsyncFrappyClient,
        frappy_datatype: CommandType,
        cmd_desc: dict,
        arguments: dict[SECoP_CMD_IO_Backend],
        result: dict[SECoP_CMD_IO_Backend],
    ) -> None:
        self._secclient: AsyncFrappyClient = secclient

        # module:acessible Path for reading/writing (module,accessible)
        self.path: Path = path

        self._cmd_desc: dict = cmd_desc

        self.callback: Callable = None

        self.arguments: dict = arguments
        self.result: dict = result

        # Root datainfo or memberinfo for nested datatypes
        self.datainfo: dict = deep_get(self._cmd_desc["datainfo"], self.path._dev_path)

        self.frappy_datatype: CommandType = frappy_datatype

        self.source = self.path._module_name + ":" + self.path._accessible_name

    async def connect(self):
        pass

    async def put(self, value: Any | None, wait=True, timeout=None):
        argument = None

        arg_datatype = self.frappy_datatype.argument

        # Three different cases:

        # StructOf()
        if isinstance(arg_datatype, StructOf):
            argument = {
                signame: await sig.get_value()
                for (signame, sig) in self.arguments.items()
            }

        # TupleOf()
        elif isinstance(arg_datatype, TupleOf):
            raise NotImplementedError

        # Atomic datatypes
        elif isinstance(arg_datatype, atomic_dtypes):
            sig = next(iter(self.arguments.values()))
            argument = arg_datatype.export_datatype(await sig.get_value())

        # Run SECoP Command
        res, qualifiers = await self._secclient.execCommand(
            module=self.path._module_name,
            command=self.path._accessible_name,
            argument=argument,
        )

        # write return Value to corresponding Backends

        # Three different cases:
        res_datatype = self.frappy_datatype.result

        # StructOf()
        if isinstance(res_datatype, StructOf):
            sig: SECoP_CMD_IO_Backend
            for sig_name, sig in self.result.items():
                await sig.put(res.get(sig_name))

        # TupleOf()
        elif isinstance(res_datatype, TupleOf):
            raise NotImplementedError

        elif isinstance(res_datatype, atomic_dtypes):
            sig = next(iter(self.result.values()))
            await sig.put(res)

    async def get_descriptor(self) -> Descriptor:
        res = {}

        res["source"] = self.source

        # ophyd datatype (some SECoP datatypeshaveto be converted)
        res["dtype"] = "string"  # TODO or None??? signalx is never read

        # get shape from datainfo and SECoPtype

        # TODO if array is ragged only first dimension is used otherwise parse the array
        if self.datainfo["type"] == "array":
            res["shape"] = [1, self.datainfo.get("maxlen", None)]
        else:
            res["shape"] = []

        for property_name, prop_val in self._cmd_desc.items():
            # skip datainfo (treated seperately)
            if property_name == "datainfo" or property_name == "datatype":
                continue
            res[property_name] = prop_val

        for property_name, prop_val in self.datainfo.items():
            if property_name == "type":
                property_name = "SECoPtype"
            res[property_name] = prop_val

        return res

    async def get_reading(self) -> Reading:
        return None

    async def get_value(self) -> T:
        return None

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
        self.SECoPdtype: str
        self.SECoPdtype_obj: DataType

        self._set_dtype()

        self.source = (
            secclient.uri
            + ":"
            + secclient.nodename
            + ":"
            + self.path._module_name
            + ":"
            + self.path._accessible_name
        )

    async def connect(self):
        pass

    async def put(self, value: Any | None, wait=True, timeout=None):
        # TODO wait + timeout

        # top level nested datatypes (handled as sting Signals)
        if self.path._dev_path == []:
            if self.SECoPdtype == "tuple":
                value = self.SECoPdtype_obj.from_string(value)

            if self.SECoPdtype == "struct":
                value = self.SECoPdtype_obj.from_string(value)

            await self._secclient.setParameter(**self.get_param_path(), value=value)
            return

        # signal sub element of SECoP parameter (tuple or struct member)

        # get current value
        reading = await self._secclient.getParameter(
            **self.get_param_path(), trycache=True
        )

        curr_val = reading.get_value()

        # insert new val
        new_val = self.path.insert_val(curr_val, value)

        # set new value
        await self._secclient.setParameter(**self.get_param_path(), value=new_val)

    async def get_descriptor(self) -> Descriptor:
        res = {}

        res["source"] = self.source

        # ophyd datatype (some SECoP datatypeshaveto be converted)
        res["dtype"] = self.datatype

        # get shape from datainfo and SECoPtype

        # TODO if array is ragged only first dimension is used otherwise parse the array
        if self.datainfo["type"] == "array":
            res["shape"] = [1, self.datainfo.get("maxlen", None)]
        else:
            res["shape"] = []

        for property_name, prop_val in self._param_description.items():
            # skip datainfo (treated seperately)
            if property_name == "datainfo" or property_name == "datatype":
                continue
            res[property_name] = prop_val

        for property_name, prop_val in self.datainfo.items():
            if property_name == "type":
                property_name = "SECoPtype"
            res[property_name] = prop_val

        return res

    async def get_reading(self) -> Reading:
        dataset = await self._secclient.getParameter(
            **self.get_param_path(), trycache=False
        )

        # select only the tuple/struct member corresponding to the signal
        dataset.value = deep_get(dataset.value, self.path._dev_path)

        if self.SECoPdtype == "enum":
            dataset.value = dataset.value.value
        if self.SECoPdtype in ["tuple", "struct"]:
            dataset.value = str(dataset.value)
        if (isinstance(self.SECoPdtype_obj,ArrayOf) and 
            isinstance(self.SECoPdtype_obj.members,(StructOf,TupleOf))):
            
            dataset = copy.deepcopy(dataset)
            dataset.value = list(map(str,dataset.value))
            

        return dataset.get_reading()

    async def get_value(self) -> T:
        dataset = await self._secclient.getParameter(
            **self.get_param_path(), trycache=False
        )

        # select only the tuple member corresponding to the signal
        dataset.value = deep_get(dataset.value, self.path._dev_path)

        if self.SECoPdtype in ["tuple", "struct"]:
            dataset.value = str(dataset.value)
            
        if (isinstance(self.SECoPdtype_obj,ArrayOf) and 
            isinstance(self.SECoPdtype_obj.members,(StructOf,TupleOf))):
            
            dataset = copy.deepcopy(dataset)
            dataset.value = list(map(str,dataset.value))

        return dataset.get_value()

    def set_callback(self, callback: Callable[[Reading, Any], None] | None) -> None:
        def awaitify(sync_func):
            """Wrap a synchronous callable to allow ``await``'ing it"""

            @wraps(sync_func)
            async def async_func(*args, **kwargs):
                return sync_func(*args, **kwargs)

            return async_func

        def updateItem(module, parameter, entry: CacheItem):
            data = SECoPReading(entry)
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

    def _set_dtype(self) -> None:
        self.SECoPdtype = self.datainfo["type"]
        self.datatype = SECOP2DTYPE.get(self.SECoPdtype, None)
        self.SECoPdtype_obj = self._param_description["datatype"]


class PropertyBackend(SignalBackend):
    """A read/write/monitor backend for a Signals"""

    def __init__(self, prop_key: str, propertyDict: Dict[str, T]) -> None:
        # secclient

        self._property_dict = propertyDict
        self._prop_key = prop_key
        self._datatype = self._get_datatype()

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
        description["shape"] = []

        return description

    async def get_reading(self) -> Reading:
        """The current value, timestamp and severity"""
        # TODO correct timestamp
        return get_read_str(self._property_dict[self._prop_key], timestamp=time.time())

    async def get_value(self) -> T:
        """The current value"""
        # TODO correct timestamp
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

# TODO: status tuple


# Tuple and struct are handled in a special way. They are unfolded into subdevices

SECOP2DTYPE = {
    "double": "number",
    "int": "number",
    "scaled": "number",
    "bool": "boolean",
    "enum": "number",
    "string": "string",
    "blob": "string",
    "array": "array",
    "tuple": "string",  # but variing types of array elements
    "struct": "string",
    "command": "string",
}
