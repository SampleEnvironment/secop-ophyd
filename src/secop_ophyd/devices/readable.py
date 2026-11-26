"""Device for the interface class Readable, and the SECoP status codes:
waiting for a module to leave BUSY."""

import asyncio
import logging
import time as ttime
from enum import Enum

import bluesky.plan_stubs as bps
from bluesky.protocols import Reading, Subscribable, Triggerable
from ophyd_async.core import (
    AsyncStatus,
    Device,
    DeviceMock,
    SignalR,
    observe_value,
    set_mock_value,
    wait_for_value,
)
from ophyd_async.core._utils import Callback

from secop_ophyd.AsyncFrappyClient import AsyncSecopClient
from secop_ophyd.devices.base import SECoPDevice
from secop_ophyd.devices.connector import SECoPDeviceConnector

# Predefined Status Codes
DISABLED = 0
IDLE = 100
STANDBY = 130
PREPARED = 150
WARN = 200
WARN_STANDBY = 230
WARN_PREPARED = 250
NSTABLE = 270  # not in SECoP standard (yet)
BUSY = 300
DISABLING = 310
INITIALIZING = 320
PREPARING = 340
STARTING = 360
RAMPING = 370
STABILIZING = 380
FINALIZING = 390
ERROR = 400
ERROR_STANDBY = 430
ERROR_PREPARED = 450
UNKNOWN = 401  # not in SECoP standard (yet)

# status code per standard status name, for status signals whose SECoP
# datatype is not known (mock mode)
STATUS_CODES = {
    "DISABLED": DISABLED,
    "IDLE": IDLE,
    "STANDBY": STANDBY,
    "PREPARED": PREPARED,
    "WARN": WARN,
    "WARN_STANDBY": WARN_STANDBY,
    "WARN_PREPARED": WARN_PREPARED,
    "NSTABLE": NSTABLE,
    "BUSY": BUSY,
    "DISABLING": DISABLING,
    "INITIALIZING": INITIALIZING,
    "PREPARING": PREPARING,
    "STARTING": STARTING,
    "RAMPING": RAMPING,
    "STABILIZING": STABILIZING,
    "FINALIZING": FINALIZING,
    "ERROR": ERROR,
    "ERROR_STANDBY": ERROR_STANDBY,
    "ERROR_PREPARED": ERROR_PREPARED,
    "UNKNOWN": UNKNOWN,
}


def _status_code(status_sig: SignalR, status: str) -> int:
    secop_dtype = getattr(status_sig._connector._init_backend, "SECoPdtype_obj", None)
    if secop_dtype is not None:
        return secop_dtype.export_value(status)

    # no node description (mock mode): only standard status names are known
    status_name = getattr(status, "value", status)
    if status_name not in STATUS_CODES:
        raise ValueError(
            f"Unknown status '{status_name}' of {status_sig.name}: without a SEC "
            f"node description only the standard SECoP status names are known"
        )
    return STATUS_CODES[status_name]


def _is_failed(stat_code: int) -> bool:
    """ERROR or DISABLED"""
    return stat_code >= ERROR or stat_code < IDLE


def _remaining(deadline: float | None) -> float | None:
    """Seconds left until ``deadline`` (a ``time.monotonic()`` value)."""
    return None if deadline is None else max(0.0, deadline - ttime.monotonic())


async def _wait_while_busy(
    status: SignalR,
    module: str,
    timeout: float | None,
    secclient: AsyncSecopClient | None,
) -> int:
    """Wait until the module's status has left BUSY and return the status code
    it is in then.

    Call it only *after* the action that makes the module BUSY (a 'target'
    write, a 'go' command, ...) has been answered: the resting state is already
    "not busy", so waiting from before the action would end at once.

    ``secclient`` is None in mock mode, where there is no SEC node to ask.
    """
    deadline = None if timeout is None else ttime.monotonic() + timeout

    def _left_busy(stat_name) -> bool:
        stat_code = _status_code(status, stat_name)
        return not (BUSY <= stat_code < ERROR)

    # ask the SEC node for the status, so we don't wait on a stale, cached
    # value: the node may send the BUSY update after its reply to the action.
    # The reply updates the cache that 'status' reads from.
    if secclient is not None:
        async with asyncio.timeout(timeout):
            await secclient.get_parameter(module, "status", trycache=False)

    # wait_for_value's own timeout names the signal and its last value
    await wait_for_value(status, _left_busy, timeout=_remaining(deadline))

    return _status_code(status, await status.get_value())


def _set_mock_status_idle(device: Device) -> None:
    """Start a mocked module in the IDLE state, if its status enum has one
    (a mock enum signal otherwise starts at its first member)."""
    status_0 = getattr(device, "status_0", None)
    if not isinstance(status_0, SignalR):
        return

    datatype = status_0.datatype
    if datatype is str:
        set_mock_value(status_0, "IDLE")
    elif isinstance(datatype, type) and issubclass(datatype, Enum):
        idle = next((m for m in datatype if m.value == "IDLE"), None)
        if idle is not None:
            set_mock_value(status_0, idle)


class SECoPReadableMock(DeviceMock["SECoPReadableDevice"]):
    """Mock behaviour for SECoP Readable modules: start IDLE."""

    async def connect(self, device: "SECoPReadableDevice") -> None:
        _set_mock_status_idle(device)


class SECoPReadableDevice(SECoPDevice, Triggerable, Subscribable):
    """
    Standard readable SECoP device, corresponding to a SECoP module with the
    interface class "Readable"
    """

    hinted_signals: list[str] = ["value"]
    mandatory_parameters: list[str] = ["value", "status"]

    _mock_class: type[DeviceMock] = SECoPReadableMock

    def __init__(
        self,
        sri: str = "",  # SECoP resource identifier host:port:optional[module]
        name: str = "",
        connector: SECoPDeviceConnector | None = None,
        loglevel=logging.INFO,
        logdir: str | None = None,
    ):
        """Initializes the SECoPReadableDevice

        :param secclient: SECoP client providing communication to the SEC Node
        :type secclient: AsyncSecopClient
        :param module_name: Name of the SEC Node module that is represented by
            this device
        :type module_name: str
        """

        self.value: SignalR
        # status is SECoP's StatusType == TupleOf(EnumType, StringType), always
        # decomposed: status_0 is the status code, status_1 the status text
        self.status_0: SignalR

        super().__init__(
            sri=sri, name=name, connector=connector, loglevel=loglevel, logdir=logdir
        )

    async def _assign_interface_formats(self):
        value_signals = (
            [self.value] if hasattr(self, "value") else self._split_signals("value")
        )
        self._assign_hinted_format("value", value_signals)

        # TODO ensure status signals must be neither config nor read format

    async def wait_for_idle(self):
        """asynchronously waits until module is IDLE again. this is helpful,
        for running commands that are not done immediately
        """

        self._logger.info(f"Waiting for {self.name} to be IDLE")

        if self.status_0 is None:
            self._logger.error("Status Signal not initialized")
            raise Exception("status Signal not initialized")

        # force reading of fresh status from device
        await self.status_0.read(False)

        async for stat_name in observe_value(self.status_0):
            stat_code = _status_code(self.status_0, stat_name)
            # Module is in IDLE/WARN state
            if IDLE <= stat_code < BUSY:
                self._logger.info(f"Module {self.name} --> IDLE")
                break

            if hasattr(self, "_stopped"):
                # self.logger.info(f"Module {self.name} was stopped STOPPED")
                if self._stopped is True:
                    break

            # Error State or DISABLED
            if hasattr(self, "_success"):
                if stat_code >= ERROR or stat_code < IDLE:
                    self._logger.error(f"Module {self.name} --> ERROR/DISABLED")
                    self._success = False
                    break

    # TODO add timeout
    def observe_status_change(self, monitored_status_code: int):
        """Plan stub: block until the module's status code differs from
        ``monitored_status_code``."""

        async def switch_from_status_inner():
            async for stat_name in observe_value(self.status_0):
                if monitored_status_code != _status_code(self.status_0, stat_name):
                    break

        def switch_from_status_factory():
            return switch_from_status_inner()

        yield from bps.wait_for([switch_from_status_factory])

    def trigger(self) -> AsyncStatus:
        if self._mock is not None:
            # mock mode: no SEC node to read from
            return AsyncStatus(asyncio.sleep(0))
        self._logger.info(f"Triggering {self.name}: read fresh data from device")
        # get fresh reading of the value Parameter from the SEC Node
        return AsyncStatus(
            awaitable=self._client.get_parameter(self._module, "value", trycache=False)
        )

    def subscribe_reading(self, function: Callback[dict[str, Reading]]) -> None:
        """Subscribe to updates in the reading. Only supported for an atomic
        (non-composite) 'value' parameter."""
        if not hasattr(self, "value"):
            raise NotImplementedError(
                f"{self.name}: 'value' is a struct/tuple, split into the signals "
                f"{[sig.name for sig in self._split_signals('value')]}; subscribe "
                f"to those"
            )
        self.value.subscribe_reading(function)

    # Backwards-compatible alias (bluesky < 1.16 named this ``subscribe``)
    subscribe = subscribe_reading

    def clear_sub(self, function: Callback) -> None:
        """Remove a subscription."""
        if not hasattr(self, "value"):
            raise NotImplementedError(
                f"{self.name}: 'value' is a struct/tuple, split into the signals "
                f"{[sig.name for sig in self._split_signals('value')]}; "
                f"unsubscribe from those"
            )
        self.value.clear_sub(function=function)
