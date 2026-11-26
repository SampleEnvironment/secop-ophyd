"""Command backend and connector for SECoP commands."""

import inspect
from collections.abc import Awaitable, Callable
from typing import Any, get_origin

import numpy as np
from frappy.datatypes import CommandType, DataType, StructOf
from ophyd_async.core import NO_ARG_VOID_SIGNATURE, CommandBackend, Device, LazyMock
from ophyd_async.core._command import CommandConnector, MockCommandBackend
from ophyd_async.core._soft_signal_backend import make_converter

from secop_ophyd.AsyncFrappyClient import AsyncSecopClient
from secop_ophyd.datatypes.commands import build_command_signature, is_numeric_array
from secop_ophyd.datatypes.secop_dtype import SECoPdtype
from secop_ophyd.util import Path


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
        if is_numeric_array(self.raw_result):
            # as for a parameter: an array of numbers is a numpy array
            result = np.asarray(result)

        if wait_for_idle:
            if self._wait_for_idle_fn is None:
                raise RuntimeError(
                    f"wait_for_idle is not supported for command "
                    f"'{self.source_name}': module has no status signal"
                )
            await self._wait_for_idle_fn()

        return result
