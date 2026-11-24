import inspect
import json
import warnings
from collections.abc import Awaitable, Callable, Sequence
from enum import Enum
from typing import Any, cast, get_origin

import numpy as np
from bluesky.protocols import DataKey, Location, Reading
from frappy.client import CacheItem
from frappy.datatypes import (
    ArrayOf,
    BLOBType,
    BoolType,
    CommandType,
    DataType,
    EnumType,
    FloatRange,
    IntRange,
    ScaledInteger,
    StringType,
    StructOf,
    TupleOf,
)
from ophyd_async.core import (
    NO_ARG_VOID_SIGNATURE,
    Callback,
    CommandBackend,
    Device,
    LazyMock,
    SignalBackend,
    SignalDatatypeT,
    StandardReadableFormat,
    StrictEnum,
    SubsetEnum,
    SupersetEnum,
)

# not exported from ophyd_async.core: needed to give SECoP commands a mock
# backend that tolerates argument types SoftCommandBackend cannot convert
from ophyd_async.core._command import CommandConnector, MockCommandBackend
from ophyd_async.core._soft_signal_backend import make_converter

from secop_ophyd.AsyncFrappyClient import AsyncSecopClient
from secop_ophyd.propertykeys import MEANING
from secop_ophyd.util import (
    MAX_DEPTH,
    Path,
    SECoPDataKey,
    SECoPdtype,
    SECoPReading,
    build_command_signature,
    deep_get,
)

atomic_dtypes = (
    StringType,
    ScaledInteger,
    IntRange,
    FloatRange,
    BoolType,
    BLOBType,
    ArrayOf,
)


class AttributeType(StrictEnum):
    PARAMETER = "parameter"
    PROPERTY = "property"
    MEMBER = "member"


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


class _UnconvertedMockCommandBackend(MockCommandBackend):
    """Mock command backend that records calls without converting arguments.

    ophyd-async's MockCommandBackend converts every argument through a
    SoftCommandBackend, which has no converter for the dict/list/tuple
    argument types of SECoP struct, array and tuple commands. This variant
    passes arguments straight to the mock instead."""

    # always concrete: __init__ falls back to NO_ARG_VOID_SIGNATURE
    signature: inspect.Signature

    def __init__(self, initial_backend: CommandBackend, mock: LazyMock):
        self._initial_backend = initial_backend
        self._mock = mock
        self._mock_execute_callback = None

        signature = initial_backend.signature or NO_ARG_VOID_SIGNATURE
        try:
            self._return_converter = make_converter(signature.return_annotation)
        except TypeError:
            self._return_converter = None

        CommandBackend.__init__(self, signature=signature)

    async def execute(self, *args: Any, **kwargs: Any) -> Any:
        self.signature.bind(*args, **kwargs)
        return await self.execute_mock(*args, **kwargs)


def _real_shaped_signature(annotated: inspect.Signature | None) -> inspect.Signature:
    """The call signature a SECoP command gets from the node description
    (see `build_command_signature`), as far as its class annotation tells:
    the argument is called 'arg', 'wait_for_idle' is appended, and a struct
    argument becomes keyword members -- unknown without the description, so
    any keywords are accepted."""
    wait_for_idle = inspect.Parameter(
        "wait_for_idle",
        inspect.Parameter.POSITIONAL_OR_KEYWORD,
        annotation=bool,
        default=False,
    )
    if annotated is None:
        return inspect.Signature([wait_for_idle], return_annotation=None)

    params = list(annotated.parameters.values())
    return_annotation = annotated.return_annotation
    # NoneType: from a Command[[X], None] annotation
    if return_annotation in (inspect.Signature.empty, type(None)):
        return_annotation = None

    if not params:
        return inspect.Signature([wait_for_idle], return_annotation=return_annotation)
    if len(params) > 1:
        # not a SECoP command annotation (at most one argument): keep it
        return annotated

    annotation = params[0].annotation
    if annotation is dict or get_origin(annotation) is dict:
        members = inspect.Parameter("members", inspect.Parameter.VAR_KEYWORD)
        return inspect.Signature([members], return_annotation=return_annotation)

    arg = inspect.Parameter(
        "arg", inspect.Parameter.POSITIONAL_OR_KEYWORD, annotation=annotation
    )
    return inspect.Signature([arg, wait_for_idle], return_annotation=return_annotation)


class SECoPCommandConnector(CommandConnector):
    """CommandConnector that can mock commands with struct, array or tuple
    arguments (see `_UnconvertedMockCommandBackend`)."""

    async def connect_mock(self, device: Device, mock: LazyMock):
        try:
            self.backend = MockCommandBackend(self._init_backend, mock)
        except TypeError:
            self.backend = _UnconvertedMockCommandBackend(self._init_backend, mock)


def _is_composite_dtype(datatype: DataType) -> bool:
    if isinstance(datatype, (StructOf, TupleOf)):
        return True
    if isinstance(datatype, ArrayOf):
        return _is_composite_dtype(datatype.members)
    return False


def property_dtype_and_value(prop_val: Any) -> tuple[DataType, Any]:
    """Datatype and value with which a SECoP property is exposed as a Signal.

    Struct/tuple-valued properties (and arrays of them) are exposed as a JSON
    string: bluesky/TiledWriter cannot store numpy structured arrays."""
    if isinstance(prop_val, dict):
        return StringType(), json.dumps(prop_val)
    try:
        datatype = secop_dtype_obj_from_json(prop_val)
    except Exception:
        # e.g. null members: keep the value, as JSON
        return StringType(), json.dumps(prop_val)
    if _is_composite_dtype(datatype):
        return StringType(), json.dumps(prop_val)
    return datatype, prop_val


# members of the predefined module property 'meaning' (SECoP 2.0: a struct
# whose members are all optional)
MEANING_MEMBERS: dict[str, DataType] = {
    "function": StringType(),
    "importance": IntRange(0, 50),
    "belongs_to": StringType(),
    "link": StringType(),
    "key": StringType(),
}


def _meaning_members(value: Any) -> dict[str, Any] | None:
    """Members of a 'meaning' module property, or None if it is malformed.

    SECoP 1.x sends a ``(function, importance)`` tuple, SECoP 2.0 a struct."""
    if isinstance(value, (list, tuple)) and len(value) == 2:
        value = {"function": value[0], "importance": value[1]}
    if (
        not isinstance(value, dict)
        or not value
        or not set(value) <= MEANING_MEMBERS.keys()
    ):
        return None
    try:
        return {key: MEANING_MEMBERS[key](member) for key, member in value.items()}
    except Exception:
        return None


def module_property_signals(
    properties: dict[str, Any],
) -> dict[str, tuple[DataType, Any]]:
    """Signal name -> (datatype, value) for the properties of a module.

    A well-formed 'meaning' becomes one typed signal per member it has
    (``meaning_function``, ``meaning_importance``, ...). Every other property,
    and a malformed 'meaning', is one signal as given by
    `property_dtype_and_value`."""
    signals: dict[str, tuple[DataType, Any]] = {}
    for name, value in properties.items():
        members = _meaning_members(value) if name == MEANING else None
        if members is None:
            signals[name] = property_dtype_and_value(value)
            continue
        for key, member in members.items():
            signals[f"{name}_{key}"] = (MEANING_MEMBERS[key], member)
    return signals


class SECoPCommandBackend(CommandBackend[Any, Any]):
    """Backend for a SECoP command.

    Converts the argument/result between SECoP wire format and numpy/python
    values and calls `AsyncSecopClient.exec_command` to execute the command.

    Supports deferred initialization (matching the `SECoPBackend` pattern used
    for Parameters/Properties): constructed empty by the `DeviceFiller` when a
    command is declared via a `Command`/`TriggerableCommand` class annotation,
    then bound to a concrete SECoP command via `init_command_from_introspection`
    once the SEC node has been introspected.
    """

    # always concrete: set in __init__ and by init_command_from_introspection()
    signature: inspect.Signature

    def __init__(self, signature: inspect.Signature | None = None) -> None:
        """Initialize SECoPCommandBackend (optionally with a signature derived
        from a `Command[[ArgT], ResT]` class annotation)."""
        # NO_ARG_VOID_SIGNATURE (rather than a bare inspect.Signature()) so the
        # mock backend sees an annotated return type for TriggerableCommands
        resolved_signature = signature or NO_ARG_VOID_SIGNATURE
        # Snapshot of the annotation-derived signature (e.g. carrying the
        # concrete `Cryostat_SetMode_Arg_Enum` class from a generated
        # `Command[[Cryostat_SetMode_Arg_Enum], ...]` annotation), captured
        # before init_command_from_introspection() rebuilds self.signature
        # from live introspection. NO_ARG_VOID_SIGNATURE for pure
        # introspection instantiation (no class annotation).
        self._annotated_signature: inspect.Signature = resolved_signature
        # set by init_command_from_introspection(); empty without a node
        # description (mock mode)
        self.source_name = ""
        # until the node description replaces it, use the call convention the
        # command will have then, so mocked commands are called like real ones
        super().__init__(signature=_real_shaped_signature(signature))

    def init_command_from_introspection(
        self,
        cmd_datatype: CommandType,
        path: Path,
        secclient: AsyncSecopClient,
        wait_for_idle_fn: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        """Bind this backend to a concrete SECoP command.

        :param cmd_datatype: SECoP command datatype, holding the argument and
        result datatypes (either of which may be None)
        :type cmd_datatype: CommandType
        :param path: Path to the command in the secclient module dict
        :type path: Path
        :param secclient: SECoP client providing communication to the SEC Node
        :type secclient: AsyncSecopClient
        :param wait_for_idle_fn: Owning module's `wait_for_idle` coroutine
        (bound method), if it has one (i.e. it has a status signal); used to
        support the `wait_for_idle` kwarg on `execute()`.
        :type wait_for_idle_fn: Callable[[], Awaitable[None]] | None
        """
        self._secclient: AsyncSecopClient = secclient

        # module:acessible Path for reading/writing (module,accessible)
        self.path: Path = path

        self.raw_argument: DataType | None = cmd_datatype.argument
        self.raw_result: DataType | None = cmd_datatype.result

        self._arg_type: SECoPdtype | None = (
            SECoPdtype(self.raw_argument) if self.raw_argument is not None else None
        )
        self._res_type: SECoPdtype | None = (
            SECoPdtype(self.raw_result) if self.raw_result is not None else None
        )

        self._wait_for_idle_fn = wait_for_idle_fn

        self.source_name = self.path._module_name + ":" + self.path._accessible_name

        self.signature = build_command_signature(
            cmd_datatype, annotated_signature=self._annotated_signature
        )

    def source(self, name: str) -> str:
        return self.source_name or name

    async def connect(self, timeout: float) -> None:
        pass

    async def execute(self, *args: Any, **kwargs: Any) -> Any:
        bound = self.signature.bind(*args, **kwargs)
        bound.apply_defaults()

        wait_for_idle = bound.arguments.pop("wait_for_idle")

        argument = None
        if self._arg_type is not None:
            if isinstance(self.raw_argument, StructOf):
                argument = self._arg_type.val2secop(dict(bound.arguments))
            else:
                argument = self._arg_type.val2secop(bound.arguments["arg"])

        res, _qualifiers = await self._secclient.exec_command(
            module=self.path._module_name,
            command=self.path._accessible_name,
            argument=argument,
        )

        result = None if self._res_type is None else self._res_type.secop2val(res)

        if wait_for_idle:
            if self._wait_for_idle_fn is None:
                raise RuntimeError(
                    f"wait_for_idle is not supported for command "
                    f"'{self.source_name}': module has no status signal"
                )
            await self._wait_for_idle_fn()

        return result


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

    async def get_setpoint(self) -> SignalDatatypeT:
        return await self.get_value()

    async def get_location(self) -> Location[SignalDatatypeT]:
        # a SECoP parameter has no separate setpoint (get_setpoint() is its
        # value too), so one read gives both fields
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


def secop_dtype_obj_from_json(prop_val):
    if isinstance(prop_val, str):
        return StringType()

    # bool first: bool is a subclass of int
    if isinstance(prop_val, bool):
        return BoolType()

    if isinstance(prop_val, (int, float)):
        return FloatRange()

    if isinstance(prop_val, dict):
        members = {}
        for key, elem in prop_val.items():
            members[key] = secop_dtype_obj_from_json(elem)

        return StructOf(**members)

    if isinstance(prop_val, list):
        # empty list, cannot infer proper type
        if not prop_val:
            return ArrayOf(FloatRange())
        # numbers: one float array, whether the node sends 10 or 10.0
        if all(
            isinstance(elem, (int, float)) and not isinstance(elem, bool)
            for elem in prop_val
        ):
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
