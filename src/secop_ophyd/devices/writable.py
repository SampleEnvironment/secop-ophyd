"""Devices for SECoP modules with a 'target': their common base, and the
device for the interface class Writable."""

import asyncio
import time as ttime
from collections.abc import Mapping, Sequence
from logging import Logger
from typing import Any, Generic, TypeVar

import numpy as np
from bluesky.protocols import Checkable, Locatable, Location
from frappy.datatypes import DataType, StructOf, TupleOf
from frappy.errors import SECoPError
from ophyd_async.core import AsyncStatus, DeviceMock, SignalR, set_mock_value

from secop_ophyd.AsyncFrappyClient import AsyncSecopClient
from secop_ophyd.datatypes.composite import optional_struct_members
from secop_ophyd.datatypes.secop_dtype import SECoPdtype
from secop_ophyd.devices.readable import (
    SECoPReadableDevice,
    _is_failed,
    _remaining,
    _set_mock_status_idle,
    _wait_while_busy,
)


class SECoPWritableBase(SECoPReadableDevice):
    """Common base of all device classes for SECoP modules with a 'target':
    the interface classes "Writable" and "Drivable"."""

    hinted_signals: list[str] = ["target", "value"]
    mandatory_parameters: list[str] = SECoPReadableDevice.mandatory_parameters + [
        "target"
    ]

    def _param_signals(self, param: str) -> list[SignalR]:
        """The readable Signal of an atomic parameter, or the split member
        Signals of a decomposed struct/tuple parameter."""
        signal = getattr(self, param, None)
        if isinstance(signal, SignalR):
            return [signal]
        return self._split_signals(param)

    async def _assign_interface_formats(self):
        await super()._assign_interface_formats()

        self._assign_hinted_format("target", self._param_signals("target"))


def _secop_to_python(datatype: DataType, value: Any) -> Any:
    """Convert a value as delivered by the SECoP client into the form the
    Signals deliver it in. A struct becomes a dict and a tuple a tuple, with
    each member converted like the value of its split member Signal."""
    if isinstance(datatype, StructOf):
        return {
            key: _secop_to_python(member, value[key])
            for key, member in datatype.members.items()
            if key in value
        }

    if isinstance(datatype, TupleOf):
        return tuple(
            _secop_to_python(member, member_value)
            for member, member_value in zip(datatype.members, value, strict=True)
        )

    secop_dtype = SECoPdtype(datatype)
    secop_dtype.update_dtype(value)
    return secop_dtype.secop2val(value)


def _python_to_secop(
    datatype: DataType, value: Any, optional: Sequence[str] = ()
) -> Any:
    """Validate a value given to set() against the SECoP datatype and return
    it in the form the SECoP client expects. ``optional`` are the struct
    members that may be left out."""
    if isinstance(datatype, StructOf):
        if not isinstance(value, Mapping):
            raise TypeError(
                f"expected a mapping with the members {list(datatype.members)}"
            )
        # frappy's client-side StructOf accepts any member being left out, and
        # drops members whose value is None
        missing = [
            key
            for key in datatype.members
            if value.get(key) is None and key not in optional
        ]
        if missing:
            raise ValueError(f"missing struct members {missing}")
        # validate(), not __call__(): only validate() checks the limits
        return datatype.validate(dict(value))

    if isinstance(datatype, TupleOf):
        if isinstance(value, np.ndarray) and value.ndim == 1:
            value = value.tolist()
        if isinstance(value, (str, bytes, Mapping)) or not isinstance(value, Sequence):
            raise TypeError(f"expected a sequence of {len(datatype.members)} members")
        return datatype.validate(tuple(value))

    return datatype.validate(value)


T = TypeVar("T")


async def _wait_for_move(
    status: SignalR,
    module: str,
    logger: Logger,
    new_position: Any,
    timeout: float | None,
    secclient: AsyncSecopClient | None,
) -> None:
    """Wait until a move started by writing 'target' has finished: the module's
    status has left BUSY. Raises if it ended in ERROR/DISABLED.

    ``secclient`` is None in mock mode, where there is no SEC node to ask.
    """
    stat_code = await _wait_while_busy(status, module, timeout, secclient)

    if _is_failed(stat_code):
        logger.error(f"Module {module} --> ERROR/DISABLED")
        raise RuntimeError(
            f"Move of {module} to {new_position} failed: module "
            "entered ERROR/DISABLED state"
        )

    logger.info(f"Reached target, module {module} --> IDLE")


class SECoPWritableMock(DeviceMock["SECoPWritableDevice"]):
    """Mock behaviour for SECoP Writable modules, and for Drivable modules with
    a struct/tuple 'value' or 'target': start IDLE. `SECoPWritableDevice.set()`
    itself moves the mocked signals to the target instantly."""

    async def connect(self, device: "SECoPWritableDevice") -> None:
        missing = [
            param for param in ("target", "value") if not device._param_signals(param)
        ]
        if not hasattr(device, "status_0"):
            missing.append("status_0")
        if missing:
            raise AttributeError(
                f"{device.name}: a {type(device).__name__} connected in mock mode "
                f"needs annotated {missing}: without a SEC node description only "
                f"annotated signals exist"
            )
        _set_mock_status_idle(device)


class SECoPWritableDevice(SECoPWritableBase, Locatable[T], Checkable[T], Generic[T]):
    """
    Settable SECoP device, corresponding to a SECoP module with the interface
    class "Writable": a module with a 'target' but without a 'stop' command.

    ``set()`` writes 'target' and waits until the module is not BUSY. For a
    module whose value follows the target at once that is immediately; a
    Writable that needs time to reach its target reports BUSY meanwhile. The
    module must report BUSY by the time it answers the target write, or
    ``set()`` ends at once: SECoP has no other sign that a move has started.
    ``locate()`` returns the setpoint ('target') and readback ('value').

    'target' and 'value' may be of any SECoP datatype. For a struct or tuple,
    the position is one value in ``set()`` and ``locate()``, but several data
    keys in ``read()`` and ``describe()``, because an event can only hold
    scalars and arrays:

    * ``set()`` takes the whole target: a dict for a struct, a tuple for a
      tuple. ``locate()`` returns setpoint and readback in the same form.
    * ``read()`` has one key per member, from the read-only Signals
      ``target_<member>`` and ``value_<member>`` (``_0``, ``_1``, ... for a
      tuple). ``locate()["readback"]["x"]`` is the value of ``value_x``.

    ``set()`` is the only way to change a struct/tuple target: there is no
    writable 'target' Signal for it. An atomic 'target' is also a ``SignalRW``,
    whose ``set()`` only writes the parameter, without waiting.

    The type parameter is the type of the target, e.g. ``float``, or a
    ``TypedDict`` in a generated device class.
    """

    _mock_class: type[DeviceMock] = SECoPWritableMock

    def _description(self, param: str) -> dict[str, Any]:
        if self._module is None:
            raise RuntimeError(f"{self.name}: device is not bound to a SECoP module")
        return self._client.modules[self._module]["parameters"][param]

    def _datatype(self, param: str) -> DataType:
        return self._description(param)["datatype"]

    def _target_to_secop(self, value: T) -> Any:
        target = self._description("target")
        try:
            return _python_to_secop(
                target["datatype"],
                value,
                optional_struct_members(target.get("datainfo", {})),
            )
        except (SECoPError, TypeError, ValueError) as exc:
            raise ValueError(
                f"{self.name}: {value!r} is not a valid target: {exc}"
            ) from exc

    async def check_value(self, value: T) -> None:
        """Check that ``value`` is a valid target, without moving. In mock mode
        there is no node description to check against, so every value passes."""
        if self._mock is None:
            self._target_to_secop(value)

    @AsyncStatus.wrap
    async def set(self, value: T, timeout: float | None = None) -> None:
        """Set the target and wait until the module is not BUSY."""
        await self._move(value, timeout)

    async def _move(self, value: T, timeout: float | None) -> None:
        if self._mock is not None:
            self._mock_move(value)
            return

        assert self._module is not None
        secop_value = self._target_to_secop(value)

        self._logger.info(f"Moving {self._module} to {value}")
        # the timeout covers writing the target as well as waiting
        deadline = None if timeout is None else ttime.monotonic() + timeout
        try:
            async with asyncio.timeout(timeout):
                await self._client.set_parameter(self._module, "target", secop_value)
        except TimeoutError as exc:
            raise TimeoutError(
                f"{self.name}: writing the target {value!r} took longer than "
                f"{timeout} s"
            ) from exc
        await _wait_for_move(
            self.status_0,
            self._module,
            self._logger,
            value,
            _remaining(deadline),
            self._client,
        )

    def _mock_move(self, value: Any) -> None:
        """Arrive instantly: set the mocked 'target' and 'value' Signals to
        ``value``, as far as they have the same form as 'target'."""
        if isinstance(value, np.ndarray) and value.ndim == 1:
            # accepted as a tuple target in real mode too
            value = value.tolist()
        composite_target = not isinstance(getattr(self, "target", None), SignalR)
        for param in ("target", "value"):
            signal = getattr(self, param, None)
            if isinstance(signal, SignalR):
                # an atomic parameter: takes the target only if that is atomic
                # too (a struct target says nothing about a scalar value)
                if not composite_target:
                    set_mock_value(signal, value)
                continue

            for key, signal in self._split_members(param).items():
                if isinstance(value, Mapping):
                    if key in value:
                        set_mock_value(signal, value[key])
                elif isinstance(value, (tuple, list)):
                    if key.isdigit() and int(key) < len(value):
                        set_mock_value(signal, value[int(key)])

    async def _mock_position(self, param: str) -> Any:
        signal = getattr(self, param, None)
        if isinstance(signal, SignalR):
            return await signal.get_value()

        members = self._split_members(param)
        values = await asyncio.gather(*(sig.get_value() for sig in members.values()))
        position = dict(zip(members, values, strict=True))
        if all(key.isdigit() for key in position):
            return tuple(position[key] for key in sorted(position, key=int))
        return position

    async def locate(self) -> Location[T]:
        """Return the current setpoint ('target') and readback ('value')."""
        if self._mock is not None:
            setpoint, readback = await asyncio.gather(
                self._mock_position("target"), self._mock_position("value")
            )
            return Location(setpoint=setpoint, readback=readback)

        assert self._module is not None
        # no single Signal holds a whole struct/tuple: read both parameters
        # straight from the SEC node
        target, value = await asyncio.gather(
            self._client.get_parameter(self._module, "target", trycache=True),
            self._client.get_parameter(self._module, "value", trycache=True),
        )
        for entry in (target, value):
            if entry.readerror is not None:
                raise entry.readerror

        return Location(
            setpoint=_secop_to_python(self._datatype("target"), target.value),
            readback=_secop_to_python(self._datatype("value"), value.value),
        )
