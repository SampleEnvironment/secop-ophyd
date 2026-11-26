"""Signal backend for SECoP parameters, properties and struct/tuple members,
and the check of the datatype a signal is annotated with."""

import warnings
from collections.abc import Callable, Sequence
from enum import Enum
from typing import Any, cast, get_origin

import numpy as np
from bluesky.protocols import DataKey, Location, Reading
from frappy.client import CacheItem
from frappy.datatypes import ArrayOf, DataType, EnumType
from ophyd_async.core import (
    Callback,
    SignalBackend,
    SignalDatatypeT,
    StandardReadableFormat,
    StrictEnum,
    SubsetEnum,
    SupersetEnum,
)

from secop_ophyd.AsyncFrappyClient import AsyncSecopClient
from secop_ophyd.datatypes.composite import MAX_DEPTH
from secop_ophyd.datatypes.properties import (
    module_property_signals,
    property_dtype_and_value,
)
from secop_ophyd.datatypes.secop_dtype import (
    SECoPdtype,
    SECoPReading,
    describedict_to_datakey,
)
from secop_ophyd.util import deep_get

_ENUM_BASES = (StrictEnum, SubsetEnum, SupersetEnum)


def _is_concrete_enum_class(datatype: Any) -> bool:
    """True only for a genuine, member-carrying Strict/Subset/SupersetEnum
    subclass produced by codegen (e.g. Cryostat_Mode_Enum) -- as opposed to
    the generic, member-less base classes used when there is no class
    annotation (pure introspection instantiation)."""
    return (
        isinstance(datatype, type)
        and issubclass(datatype, _ENUM_BASES)
        and datatype not in _ENUM_BASES
    )


def _datatype_kind(datatype: Any) -> str | None:
    """Coarse kind of a Signal datatype, for comparing an annotation with what
    the SEC node describes. None means "not checked"."""
    if isinstance(datatype, type):
        if issubclass(datatype, Enum):
            return "enum"
        if datatype is bool:
            return "bool"
        # one kind: frappy delivers floats for ScaledInteger, and JSON numbers
        # of properties are all described as FloatRange
        if datatype in (int, float):
            return "number"
        if datatype is str:
            return "str"
        if issubclass(datatype, np.ndarray):
            return "array"
        return None
    origin = get_origin(datatype)
    if origin is np.ndarray or (
        isinstance(origin, type) and issubclass(origin, Sequence)
    ):
        return "array"
    return None


def check_annotated_datatype(
    annotated: Any, secop_dtype: DataType, described: Any, what: str
) -> None:
    """Raise TypeError if the datatype from a class annotation (``annotated``)
    does not fit what the SEC node describes (``secop_dtype``, mapped to the
    Signal datatype ``described``).

    Enum annotations must match the node's enum members: exactly for a
    StrictEnum, as a subset for a SubsetEnum and as a superset for a
    SupersetEnum."""
    if annotated is None:
        return

    annotated_kind = _datatype_kind(annotated)
    described_kind = _datatype_kind(described)
    if annotated_kind is None or described_kind is None:
        return

    name = getattr(annotated, "__name__", str(annotated))
    if annotated_kind == "str" and described_kind == "enum":
        # enum values are read as their (string) member names
        return
    if annotated_kind != described_kind:
        raise TypeError(
            f"'{what}' is annotated as {name}, but the SEC node describes it as "
            f"{secop_dtype!r}; regenerate or fix the device class"
        )

    if (
        annotated_kind == "enum"
        and isinstance(secop_dtype, EnumType)
        and annotated not in _ENUM_BASES
    ):
        node_members = set(secop_dtype.export_datatype()["members"])
        annotated_members = {member.value for member in annotated}

        if issubclass(annotated, SubsetEnum):
            ok = annotated_members <= node_members
        elif issubclass(annotated, SupersetEnum):
            ok = node_members <= annotated_members
        else:
            ok = annotated_members == node_members

        if not ok:
            raise TypeError(
                f"'{what}' is annotated with enum {name} "
                f"{sorted(annotated_members)}, but the SEC node describes the "
                f"members {sorted(node_members)}; regenerate or fix the device class"
            )


class AttributeType(StrictEnum):
    PARAMETER = "parameter"
    PROPERTY = "property"
    MEMBER = "member"


class SECoPBackend(SignalBackend[SignalDatatypeT]):
    """Unified backend for SECoP Parameters, Properties, and split composite
    Members (one struct field / tuple index of a decomposed "depth <=
    MAX_DEPTH" StructOf/TupleOf parameter).

    This allows a single backend type to be used in signal_backend_factory
    (`DeviceFiller` always constructs the same backend class for every child
    Signal, regardless of what `Signal` subclass -- SignalR/SignalW/SignalRW
    -- it ends up wrapped in), with deferred initialization based on
    annotation metadata.

    Several MEMBER backends typically share the same underlying (module,
    parent_param) SECoP parameter -- one per member. Each fetches the
    parent's raw wire reading and extracts its own field; no extra network
    round-trips are incurred since `AsyncFrappyClient.get_parameter(...,
    trycache=True)` is backed by frappy's own per-(module, parameter) cache.
    """

    format: StandardReadableFormat
    attribute_type: str | None
    _module_name: str | None
    _attribute_name: str | None  # parameter or property name
    _secclient: AsyncSecopClient
    path_str: str
    SECoPdtype_obj: DataType
    SECoP_type_info: SECoPdtype
    describe_dict: dict
    _update_callback: Callable[..., None] | None
    # MEMBER only
    _parent_param: str
    _member_key: str
    _is_tuple_member: bool

    def __init__(
        self,
        datatype: type[SignalDatatypeT] | None,
        path: str | None = None,
        attribute_type: str | None = None,
        secclient: AsyncSecopClient | None = None,
    ):
        """Initialize backend (supports deferred initialization).

        Args:
            datatype: Optional datatype for the signal
            path: Optional path for immediate initialization (module:param or prop_key)
            secclient: Optional SECoP client for immediate initialization
        """
        self._module_name = None
        self._attribute_name = None
        self._update_callback = None

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

        # Snapshot of whatever datatype the class annotation provided (e.g.
        # `Cryostat_Mode_Enum` from `SignalRW[Cryostat_Mode_Enum]`), captured
        # before init_parameter_from_introspection()/init_property_from_introspection()
        # overwrite self.datatype with a throwaway string. None for pure
        # introspection instantiation (no class annotation).
        self._annotated_datatype: type | None = datatype

        super().__init__(datatype)

    def init_parameter_from_introspection(
        self,
        datatype: type[SignalDatatypeT],
        path: str,
        secclient: AsyncSecopClient,
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
        self, datatype: type[SignalDatatypeT], path: str, secclient: AsyncSecopClient
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

    def init_member_from_introspection(
        self,
        module_name: str,
        parent_param: str,
        member_key: str,
        member_datatype: DataType,
        secclient: AsyncSecopClient,
    ) -> None:
        """Bind this backend to one member (struct field / tuple index) of a
        decomposed composite parameter. Fully initialized eagerly here
        (rather than deferred to connect(), like _init_parameter/_init_property)
        since a member's own datatype is fully known once the parent
        parameter has been introspected -- mirrors SECoPCommandBackend's
        single-phase init."""
        if self.attribute_type is not None:

            if secclient != self._secclient:
                raise RuntimeError(
                    "Backend already initialized with a different SECoP client, cannot "
                    "re-initialize"
                )

            if self.attribute_type != AttributeType.MEMBER:
                raise RuntimeError(
                    f"Backend already initialized as {self.attribute_type}, "
                    f"cannot re-initialize as MEMBER"
                )

        self.attribute_type = AttributeType.MEMBER
        self._module_name = module_name
        self._parent_param = parent_param
        self._member_key = member_key
        self._is_tuple_member = member_key.isdigit()
        self._secclient = secclient

        self.SECoPdtype_obj = member_datatype
        self.SECoP_type_info = SECoPdtype(member_datatype)

        # split member signals mirror the format of the parent (whole)
        # parameter they were split from, e.g. a struct declared
        # _signal_format="HINTED_SIGNAL" makes every one of its member
        # signals HINTED_SIGNAL too
        parent_description = secclient.modules[module_name]["parameters"][parent_param]
        match parent_description.get("_signal_format", None):
            case "HINTED_SIGNAL":
                self.format = StandardReadableFormat.HINTED_SIGNAL
            case "HINTED_UNCACHED_SIGNAL":
                self.format = StandardReadableFormat.HINTED_UNCACHED_SIGNAL
            case "UNCACHED_SIGNAL":
                self.format = StandardReadableFormat.UNCACHED_SIGNAL
            case _:
                self.format = StandardReadableFormat.CONFIG_SIGNAL

        self.path_str = f"{module_name}:{parent_param}.{member_key}"
        self.source_name = (
            self._secclient.uri + ":" + self._secclient.nodename + ":" + self.path_str
        )

        self.describe_dict = {}
        self.describe_dict["source"] = self.source_name
        self.describe_dict.update(self.SECoP_type_info.get_datakey())

        check_annotated_datatype(
            self._annotated_datatype,
            member_datatype,
            self.SECoP_type_info.np_datatype,
            self.path_str,
        )
        if _is_concrete_enum_class(self._annotated_datatype):
            self.datatype = cast(type, self._annotated_datatype)
        else:
            self.datatype = self.SECoP_type_info.np_datatype

    def source(self, name: str, read: bool) -> str:
        secclient = getattr(self, "_secclient", None)
        if secclient is None:
            # annotated without ParamT()/PropT(), not yet introspected
            return self.path_str or name
        return secclient.host + ":" + secclient.port + ":" + self.path_str

    async def connect(self, timeout: float):
        """Connect and initialize backend (parameters/properties are
        deferred-initialized here; members are already fully initialized by
        init_member_from_introspection)."""

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

        assert self._module_name is not None
        assert self._attribute_name is not None
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

        check_annotated_datatype(
            self._annotated_datatype,
            self.SECoPdtype_obj,
            self.SECoP_type_info.np_datatype,
            self.path_str,
        )
        if _is_concrete_enum_class(self._annotated_datatype):
            self.datatype = cast(type, self._annotated_datatype)
        else:
            self.datatype = self.SECoP_type_info.np_datatype

    async def _init_property(self):
        """Initialize as a property signal."""
        assert self._attribute_name is not None

        if self._module_name:
            module_desc = self._secclient.modules[self._module_name]
            self._property_dict = module_desc["properties"]
            self.SECoPdtype_obj, self._prop_value = module_property_signals(
                self._property_dict
            )[self._attribute_name]
        else:
            self._property_dict = self._secclient.properties
            self.SECoPdtype_obj, self._prop_value = property_dtype_and_value(
                self._property_dict[self._attribute_name]
            )
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
        check_annotated_datatype(
            self._annotated_datatype,
            self.SECoPdtype_obj,
            self.SECoP_type_info.np_datatype,
            self.path_str,
        )
        if _is_concrete_enum_class(self._annotated_datatype):
            self.datatype = cast(type, self._annotated_datatype)
        else:
            self.datatype = self.SECoP_type_info.np_datatype

    async def put(self, value: Any | None):
        """Put a value to the parameter. Properties and struct/tuple members
        are read-only."""

        if self.attribute_type == AttributeType.PROPERTY:
            raise RuntimeError(
                f"Cannot set property '{self._attribute_name}', properties are readonly"
            )

        if self.attribute_type == AttributeType.MEMBER:
            raise RuntimeError(
                f"Cannot set '{self._parent_param}.{self._member_key}': struct/tuple "
                f"member signals are read-only. Set the whole parameter "
                f"'{self._parent_param}' instead."
            )

        # convert to frappy compatible Format
        secop_val = self.SECoP_type_info.val2secop(value)

        await self._secclient.set_parameter(**self.get_param_path(), value=secop_val)

    def _extract_member(self, raw_value: Any) -> Any:
        if self._is_tuple_member:
            return raw_value[int(self._member_key)]
        return raw_value[self._member_key]

    def _convert_member(self, raw_member_val: Any) -> Any:
        self.SECoP_type_info.update_dtype(raw_member_val)
        return self.SECoP_type_info.secop2val(raw_member_val)

    async def get_datakey(self, source: str) -> DataKey:
        """Metadata like source, dtype, shape, precision, units"""
        if self.attribute_type == AttributeType.PROPERTY:
            # Properties have static metadata
            return describedict_to_datakey(self.describe_dict)

        if self.attribute_type == AttributeType.MEMBER:
            if isinstance(self.SECoPdtype_obj, ArrayOf):
                entry = await self._secclient.get_parameter(
                    self._module_name, self._parent_param, trycache=True
                )
                self.SECoP_type_info.update_dtype(self._extract_member(entry.value))
                self.describe_dict.update(self.SECoP_type_info.get_datakey())

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
        """Get reading, handling parameters, properties, and members."""
        if self.attribute_type == AttributeType.PROPERTY:
            # Properties have static values
            dataset = CacheItem(
                value=self._prop_value, timestamp=self._secclient.conn_timestamp
            )
            sec_reading = SECoPReading(entry=dataset, secop_dt=self.SECoP_type_info)
            return sec_reading.get_reading()

        if self.attribute_type == AttributeType.MEMBER:
            entry = await self._secclient.get_parameter(
                self._module_name, self._parent_param, trycache=True
            )
            if entry.readerror is not None:
                raise entry.readerror

            member_val = self._extract_member(entry.value)
            return {
                "value": self._convert_member(member_val),
                "timestamp": entry.timestamp,
            }

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

    async def get_location(self) -> Location[SignalDatatypeT]:
        # reads and writes go to the same SECoP parameter (like an EPICS
        # signal whose read and write PV are the same), so one read gives both
        value = await self.get_value()
        return Location(setpoint=value, readback=value)

    def set_callback(self, callback: Callback[Reading[SignalDatatypeT]] | None) -> None:
        if self.attribute_type == AttributeType.PROPERTY:
            # Properties are static, no callbacks
            return

        if self.attribute_type == AttributeType.MEMBER:
            # members are views onto their parent parameter: subscribe to the
            # parent and extract this member's value from every update
            path = (self._module_name, self._parent_param)
        else:
            path = self.get_path_tuple()

        if callback is None:
            if self._update_callback is not None:
                self._secclient.unregister_callback(path, self._update_callback)
                self._update_callback = None
            return

        # must be named exactly 'updateItem': frappy's
        # ProxyClient.register_callback() derives the callback kind from
        # cbfunc.__name__ and validates it against a fixed whitelist
        # (CALLBACK_NAMES) -- any other name is rejected with a TypeError
        if self.attribute_type == AttributeType.MEMBER:

            def updateItem(  # noqa: N802
                module: str, parameter: str, entry: CacheItem
            ) -> None:
                member_val = self._extract_member(entry.value)
                reading: Reading = {
                    "value": self._convert_member(member_val),
                    "timestamp": entry.timestamp,
                }
                callback(reading)

        else:

            def updateItem(  # noqa: N802
                module: str, parameter: str, entry: CacheItem
            ) -> None:
                data = SECoPReading(secop_dt=self.SECoP_type_info, entry=entry)
                callback(data.get_reading())

        self._update_callback = updateItem
        self._secclient.register_callback(path, updateItem)

    def _get_param_desc(self) -> dict:
        return deep_get(
            self._secclient.modules,
            [self._module_name, "parameters", self._attribute_name],
        )

    def get_param_path(self):
        return {"module": self._module_name, "parameter": self._attribute_name}

    def get_path_tuple(self):
        return (self._module_name, self._attribute_name)
