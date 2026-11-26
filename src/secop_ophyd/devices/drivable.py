"""Devices for the interface class Drivable."""

import asyncio
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from functools import cached_property
from logging import Logger
from typing import Any

from bluesky.protocols import Stoppable
from ophyd_async.core import (
    AsyncStatus,
    Device,
    DeviceMock,
    InstantMovableMock,
    MovableLogic,
    SignalDatatypeT,
    SignalR,
    SignalRW,
    StandardMovable,
    TimeoutCalculator,
)

from secop_ophyd.AsyncFrappyClient import AsyncSecopClient
from secop_ophyd.devices.connector import SECoPDeviceConnector, _composite_target_params
from secop_ophyd.devices.readable import _set_mock_status_idle
from secop_ophyd.devices.writable import (
    SECoPWritableBase,
    SECoPWritableDevice,
    T,
    _wait_for_move,
)


@dataclass
class SECoPMovableLogic(MovableLogic[Any]):
    """Move logic for a SECoP "Drivable" module.

    A move is considered complete once the module's status parameter
    leaves BUSY and enters the IDLE range, rather than when readback
    equals setpoint.
    """

    # bound to status_0 (the status code Signal, resolved to its enum member
    # name, e.g. "RAMPING") -- status is SECoP's StatusType ==
    # TupleOf(EnumType, StringType), always decomposed
    status: SignalR
    secclient: AsyncSecopClient
    module: str
    logger: Logger

    async def stop(self) -> None:
        if self.setpoint._mock is not None:
            # mock mode: no SEC node to send 'stop' to
            return
        self.logger.info(f"Stopping {self.module}")
        await self.secclient.exec_command(self.module, "stop")

    async def move(self, new_position: Any, timeout: TimeoutCalculator) -> None:
        self.logger.info(f"Moving {self.module} to {new_position}")

        await self.setpoint.set(new_position)

        await _wait_for_move(
            self.status,
            self.module,
            self.logger,
            new_position,
            timeout(),
            None if self.setpoint._mock is not None else self.secclient,
        )


class SECoPDrivableBase(SECoPWritableBase):
    """Common base of the device classes for SECoP modules with the interface
    class "Drivable": `SECoPMoveableDevice` and `SECoPCompositeMoveableDevice`.
    Which of the two a module gets depends on the datatypes of its 'target'
    and 'value' parameters, see `class_from_interface`."""


class SECoPMovableMock(InstantMovableMock):
    """Mock behaviour for SECoP Drivable modules: start IDLE, and move the
    readback to the target instantly."""

    async def connect(  # type: ignore[override]
        self, device: "SECoPMoveableDevice"
    ) -> None:
        missing = [
            name
            for name in ("target", "value", "status_0")
            if not hasattr(device, name)
        ]
        if missing:
            raise AttributeError(
                f"{device.name}: a Drivable connected in mock mode needs annotated "
                f"{missing}: without a SEC node description only annotated "
                f"signals exist"
            )
        _set_mock_status_idle(device)
        await super().connect(device)


class SECoPMoveableDevice(SECoPDrivableBase, StandardMovable[SignalDatatypeT]):
    """
    Standard movable SECoP device, corresponding to a SECoP module with the
    interface class "Drivable" whose 'target' and 'value' are atomic (scalar
    or array). A Drivable with a struct/tuple 'target' or 'value' is a
    `SECoPCompositeMoveableDevice`.
    """

    _mock_class: type[DeviceMock] = SECoPMovableMock

    def __init__(
        self,
        sri: str = "",  # SECoP resource identifier host:port:optional[module]
        name: str = "",
        connector: SECoPDeviceConnector | None = None,
        loglevel=logging.INFO,
        logdir: str | None = None,
    ):
        """Initialize SECoPMovableDevice

        :param secclient: SECoP client providing communication to the SEC Node
        :type secclient: AsyncSecopClient
        :param module_name: ame of the SEC Node module that is represented by
            this device
        :type module_name: str
        """

        self.target: SignalRW

        super().__init__(
            sri=sri, name=name, connector=connector, loglevel=loglevel, logdir=logdir
        )

    @cached_property
    def movable_logic(self) -> MovableLogic:
        if self._module is None:
            raise RuntimeError(f"{self.name}: device is not bound to a SECoP module")

        return SECoPMovableLogic(
            setpoint=self.target,
            readback=self.value,
            status=self.status_0,
            secclient=self._client,
            module=self._module,
            logger=self._logger,
        )

    def set_name(self, name: str, *, child_name_separator: str | None = None) -> None:
        # set_name() can run several times before movable_logic is actually
        # resolvable: once before connect() (e.g. init_devices() naming devices
        # up front), and again mid-connect whenever a sibling/parent signal is
        # filled in (DeviceFiller.fill_child_signal() -> _set_device_child()
        # triggers a renaming cascade down the whole tree) -- including a
        # rename of *this* device as a not-yet-connected child, triggered by
        # a *sibling* module being filled into the parent node, well before
        # this device's own connect_real() has run at all. StandardMovable's
        # set_name() needs '_module' (set by set_module(), early in the
        # parent node's connect_real()) and 'target', 'value' and 'status_0'
        # (only created once this device's own connect_real() fills its
        # signals, in the order the node describes them) to resolve
        # movable_logic, so skip it and fall back to plain Device.set_name()
        # until all of them are present; a later call does the real renaming.
        if self._module is None or not all(
            hasattr(self, signal) for signal in ("target", "value", "status_0")
        ):
            Device.set_name(self, name, child_name_separator=child_name_separator)
            return
        super().set_name(name, child_name_separator=child_name_separator)


class SECoPCompositeMoveableDevice(
    SECoPDrivableBase, SECoPWritableDevice[T], Stoppable
):
    """
    Movable SECoP device, corresponding to a SECoP module with the interface
    class "Drivable" whose 'target' and/or 'value' is a struct or tuple.

    Behaves like `SECoPWritableDevice` for a struct/tuple, and can be stopped:
    ``set()`` takes the whole target as a dict or tuple and is the only way to
    change it, ``locate()`` returns setpoint and readback in the same form,
    and ``read()`` has one data key per member. A move reports no progress
    updates.
    """

    def __init__(
        self,
        sri: str = "",  # SECoP resource identifier host:port:optional[module]
        name: str = "",
        connector: SECoPDeviceConnector | None = None,
        loglevel=logging.INFO,
        logdir: str | None = None,
    ):
        # None: not stopped; else the 'success' argument of the last stop()
        self._stop_success: bool | None = None
        self._move_task: asyncio.Task | None = None

        super().__init__(
            sri=sri, name=name, connector=connector, loglevel=loglevel, logdir=logdir
        )

    @AsyncStatus.wrap
    async def set(self, value: T, timeout: float | None = None) -> None:
        """Move to the given target and wait until the module has left BUSY."""
        self._stop_success = None
        self._move_task = asyncio.create_task(self._move(value, timeout))
        try:
            await self._move_task
        except asyncio.CancelledError:
            # cancelled by stop(): how the move ends is decided below.
            # Otherwise (e.g. the plan was aborted) pass the cancellation on.
            if self._stop_success is None:
                raise
        finally:
            self._move_task = None

        stop_success, self._stop_success = self._stop_success, None
        if stop_success is False:
            raise RuntimeError(f"Device {self.name} was stopped.")

    async def stop(self, success: bool = False) -> None:
        """Stop the module and end a running ``set()``: successfully if
        ``success``, else with a RuntimeError."""
        self._stop_success = success
        if self._mock is None:
            self._logger.info(f"Stopping {self._module}")
            try:
                await self._client.exec_command(self._module, "stop")
            except Exception:
                # not stopped: a running set() ends as it would have
                self._stop_success = None
                raise
        if self._move_task is not None:
            self._move_task.cancel()


def _check_drivable_class(
    device: Device,
    module: str,
    parameters: Mapping[str, Any],
    commands: Mapping[str, Any],
) -> None:
    """A Drivable's device class depends on the datatypes of 'target' and
    'value', which a directly instantiated device cannot know in advance."""
    composite = _composite_target_params(parameters)

    if isinstance(device, SECoPCompositeMoveableDevice) and "stop" not in commands:
        raise TypeError(
            f"Module '{module}' has no 'stop' command, which "
            f"{SECoPCompositeMoveableDevice.__name__} needs: use "
            f"{SECoPWritableDevice.__name__} for this module"
        )

    if isinstance(device, SECoPMoveableDevice) and composite:
        raise TypeError(
            f"{composite} of module '{module}' is a struct/tuple, which "
            f"{SECoPMoveableDevice.__name__} does not support: use "
            f"{SECoPCompositeMoveableDevice.__name__} for this module "
            f"(regenerate the device classes if this one was generated)"
        )

    if (
        isinstance(device, SECoPWritableDevice)
        and "target" in composite
        and "target" in dict(device.children())
    ):
        # declared by a device class generated before struct/tuple targets
        # became set()-only
        raise TypeError(
            f"'target' of module '{module}' is a struct/tuple and only changed "
            f"through set(), but {type(device).__name__} declares a 'target' "
            f"signal: regenerate the device classes"
        )

    if isinstance(device, SECoPCompositeMoveableDevice) and not composite:
        raise TypeError(
            f"Neither 'target' nor 'value' of module '{module}' is a "
            f"struct/tuple: use {SECoPMoveableDevice.__name__} for this module"
        )
