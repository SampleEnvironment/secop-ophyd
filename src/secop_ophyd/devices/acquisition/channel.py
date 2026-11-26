"""Device for the interface class AcquisitionChannel, and the buffer of the
updates it collects."""

import logging
import time as ttime
from dataclasses import dataclass
from typing import Any

import numpy as np
from bluesky.protocols import DataKey, PartialEventPage, Reading
from ophyd_async.core import Command, SignalR, SignalW
from ophyd_async.core._utils import Callback

from secop_ophyd.AsyncFrappyClient import AsyncSecopClient
from secop_ophyd.datatypes.secop_dtype import SECoPdtype, describedict_to_datakey
from secop_ophyd.devices.connector import SECoPDeviceConnector
from secop_ophyd.devices.readable import SECoPReadableDevice


class _ReadingBuffer:
    """The readings of one signal, kept in numpy arrays that double in size
    when full.

    Numeric (and bool) values of one shape go into one array, so buffering
    many updates neither allocates per update nor holds a Python object each.
    Once a value does not fit the array (another shape, or a type that cannot
    be cast to it without loss) and for all other values (strings, enums),
    the buffer holds a list instead.
    """

    def __init__(self, capacity: int = 64) -> None:
        self._capacity = capacity
        self._len = 0
        self._timestamps = np.empty(capacity)
        self._values: np.ndarray | None = None
        self._objects: list[Any] | None = None

    def __len__(self) -> int:
        return self._len

    @property
    def last_timestamp(self) -> float | None:
        return float(self._timestamps[self._len - 1]) if self._len else None

    def _grow(self) -> None:
        size = 2 * len(self._timestamps)
        timestamps = np.empty(size)
        timestamps[: self._len] = self._timestamps[: self._len]
        self._timestamps = timestamps
        if self._values is not None:
            values = np.empty((size, *self._values.shape[1:]), self._values.dtype)
            values[: self._len] = self._values[: self._len]
            self._values = values

    def append(self, value: Any, timestamp: float) -> None:
        if self._len == len(self._timestamps):
            self._grow()
        self._timestamps[self._len] = timestamp

        if self._objects is None:
            array = np.asarray(value)
            if self._values is None and self._len == 0 and array.dtype.kind in "biuf":
                self._values = np.empty(
                    (len(self._timestamps), *array.shape), array.dtype
                )
            if (
                self._values is not None
                and array.shape == self._values.shape[1:]
                and np.can_cast(array.dtype, self._values.dtype, "same_kind")
            ):
                self._values[self._len] = array
                self._len += 1
                return
            # does not fit (or never fitted) an array: keep a list from now on
            self._objects = (
                [] if self._values is None else list(self._values[: self._len])
            )
            self._values = None

        self._objects.append(value)
        self._len += 1

    def take(self, count: int) -> tuple[list[Any], list[float]]:
        """Remove the first ``count`` readings and return their values and
        timestamps."""
        count = min(count, self._len)
        timestamps = self._timestamps[:count].tolist()
        rest = self._len - count

        values: list[Any]
        if self._objects is not None:
            values = self._objects[:count]
            del self._objects[:count]
        elif self._values is not None:
            taken = self._values[:count]
            # scalars as Python numbers, arrays as one ndarray per reading
            values = taken.tolist() if taken.ndim == 1 else list(taken.copy())
            self._values[:rest] = self._values[count : self._len]
        else:
            values = []

        self._timestamps[:rest] = self._timestamps[count : self._len]
        self._len = rest
        return values, timestamps

    def clear(self) -> None:
        self.take(self._len)


@dataclass(frozen=True)
class AcquisitionGoal:
    """When an acquisition channel ends a cycle: the value given to
    `SECoPAcquisitionDevice.prepare`.

    :param goal: written to the 'goal' parameter: the cycle ends when 'value'
        reaches it. None leaves 'goal' unchanged.
    :param enable: written to the 'goal_enable' parameter: whether the goal
        ends the cycle at all. None leaves 'goal_enable' unchanged. A module
        with a 'goal' but no 'goal_enable' always has its goal active.
    """

    goal: Any = None
    enable: bool | None = None


class SECoPAcquisitionChannelDevice(SECoPReadableDevice):
    """
    SECoP device for a module with the interface class "AcquisitionChannel":
    one measured quantity of a data acquisition, whose cycles its
    `SECoPAcquisitionControllerDevice` runs. A channel listed by a controller
    is a child device of that controller, named by its role.

    ``read()`` gives its 'value', and the result of 'get_data' if the module
    has that command. While its controller flies, the channel buffers every
    update of 'value' (unless it has 'get_data'), for the controller's
    ``collect_pages()``.

    `SECoPAcquisitionDevice` is a controller and a channel in one module.
    """

    hinted_signals: list[str] = ["value"]

    def __init__(
        self,
        sri: str = "",  # SECoP resource identifier host:port:optional[module]
        name: str = "",
        connector: SECoPDeviceConnector | None = None,
        loglevel=logging.INFO,
        logdir: str | None = None,
    ):
        # the result of 'get_data' was collected after its cycle ended
        self._data_collected = False

        # signal name -> buffered updates
        self._buffers: dict[str, _ReadingBuffer] = {}
        self._subscriptions: list[tuple[SignalR, Callback]] = []

        super().__init__(
            sri=sri, name=name, connector=connector, loglevel=loglevel, logdir=logdir
        )

    @property
    def _secclient(self) -> AsyncSecopClient | None:
        """None in mock mode, where there is no SEC node to talk to."""
        return None if self._mock is not None else self._client

    @property
    def _get_data(self) -> Command | None:
        """The module's 'get_data' command, if it has one."""
        command = getattr(self, "get_data", None)
        return command if isinstance(command, Command) else None

    async def _apply_goal(self, value: AcquisitionGoal) -> None:
        """Write 'goal' and 'goal_enable'."""
        if value.goal is not None:
            goal = getattr(self, "goal", None)
            if not isinstance(goal, SignalW):
                raise ValueError(f"{self.name} has no writable 'goal' parameter")
            await goal.set(value.goal)

        if value.enable is not None:
            goal_enable = getattr(self, "goal_enable", None)
            if isinstance(goal_enable, SignalW):
                await goal_enable.set(value.enable)
            elif not value.enable:
                raise ValueError(
                    f"{self.name} has no writable 'goal_enable' parameter: "
                    f"its goal is always active"
                )

    # --- one cycle, as seen by the channel ---------------------------------

    def _cycle_started(self, buffer: bool) -> None:
        """A cycle starts. With ``buffer``, buffer the updates of 'value' from
        now on (unless the module has 'get_data')."""
        self._data_collected = False
        if buffer and self._get_data is None:
            self._start_buffering()

    async def _cycle_ended(self) -> None:
        """The cycle has ended: fetch the final 'value' and stop buffering."""
        try:
            # the update of the final value may only arrive after the status
            # update: ask for it, so read() and the buffer have the result
            if self._secclient is not None:
                await self._secclient.get_parameter(
                    self._module, "value", trycache=False
                )
        finally:
            self._stop_buffering()

    # --- buffering -------------------------------------------------------

    def _collected_signals(self) -> list[SignalR]:
        """'value', or its members if it is a struct/tuple: buffered and
        collected together, one row per update."""
        if isinstance(value := getattr(self, "value", None), SignalR):
            return [value]
        return self._split_signals("value")

    def _start_buffering(self) -> None:
        self._stop_buffering()
        for signal in self._collected_signals():
            buffer = self._buffers.setdefault(signal.name, _ReadingBuffer())
            # the reading given at once on subscribing is from before the cycle
            subscribed = [False]

            def on_reading(
                reading: dict[str, Reading],
                buffer: _ReadingBuffer = buffer,
                subscribed: list[bool] = subscribed,
            ) -> None:
                if not subscribed[0]:
                    return
                for update in reading.values():
                    last = buffer.last_timestamp
                    # the reply to an explicit read can repeat an update
                    if last is None or update["timestamp"] > last:
                        buffer.append(update["value"], update["timestamp"])

            signal.subscribe_reading(on_reading)
            subscribed[0] = True
            self._subscriptions.append((signal, on_reading))

    def _stop_buffering(self) -> None:
        for signal, callback in self._subscriptions:
            signal.clear_sub(callback)
        self._subscriptions.clear()

    def _clear_buffers(self) -> None:
        for buffer in self._buffers.values():
            buffer.clear()

    # --- get_data --------------------------------------------------------

    def _get_data_key(self) -> str:
        return f"{self.name}-get_data"

    async def _get_data_reading(self, get_data: Command) -> Reading:
        """Run 'get_data': its result, timestamped by the SEC node."""
        if self._secclient is None:
            # mock mode: the mocked command
            return Reading(
                value=np.asarray(await get_data.execute()), timestamp=ttime.time()
            )
        backend = getattr(get_data._connector, "_init_backend", None)
        res_type: SECoPdtype | None = getattr(backend, "_res_type", None)
        result, qualifiers = await self._secclient.exec_command(
            self._module, "get_data"
        )
        if res_type is not None:
            res_type.update_dtype(result)
            result = res_type.secop2val(result)
        return Reading(
            value=np.asarray(result), timestamp=qualifiers.get("t", ttime.time())
        )

    async def _get_data_datakey(self, get_data: Command) -> DataKey:
        backend = getattr(get_data._connector, "_init_backend", None)
        res_type: SECoPdtype | None = getattr(backend, "_res_type", None)
        if self._secclient is None or res_type is None:
            # mock mode: the result datatype is not known
            return DataKey(dtype="array", shape=[None], source=self._get_data_key())
        # the shape of an array result is only known from a result
        result, _ = await self._secclient.exec_command(self._module, "get_data")
        res_type.update_dtype(result)
        source = (
            f"{self._secclient.uri}:{self._secclient.nodename}:{self._module}:get_data"
        )
        return describedict_to_datakey({**res_type.get_datakey(), "source": source})

    async def read(self) -> dict[str, Reading]:
        """The readings of the module, and the result of 'get_data' if the
        module has that command."""
        reading = await super().read()
        if (get_data := self._get_data) is not None:
            reading[self._get_data_key()] = await self._get_data_reading(get_data)
        return reading

    async def describe(self) -> dict[str, DataKey]:
        description = await super().describe()
        if (get_data := self._get_data) is not None:
            description[self._get_data_key()] = await self._get_data_datakey(get_data)
        return description

    # --- collecting ------------------------------------------------------

    async def _describe_stream(self) -> dict[str, DataKey]:
        """The data keys of the channel's collected stream: the result of
        'get_data', or the updates of 'value'."""
        if (get_data := self._get_data) is not None:
            return {self._get_data_key(): await self._get_data_datakey(get_data)}
        data_keys: dict[str, DataKey] = {}
        for signal in self._collected_signals():
            data_keys.update(await signal.describe())
        return data_keys

    async def _collect_page(self, cycle_running: bool) -> PartialEventPage | None:
        """The data collected since the last call, or None if there is none."""
        if (get_data := self._get_data) is not None:
            if self._data_collected:
                # the result of this cycle was already handed over
                return None
            # collected before its cycle ended: collect it again later
            self._data_collected = not cycle_running
            reading = await self._get_data_reading(get_data)
            key = self._get_data_key()
            return PartialEventPage(
                data={key: [reading["value"]]},
                timestamps={key: [reading["timestamp"]]},
                time=[reading["timestamp"]],
            )

        signals = self._collected_signals()
        buffers = [self._buffers.get(signal.name) for signal in signals]
        # the members of a struct/tuple 'value' all update together
        count = min((len(buffer) if buffer else 0) for buffer in buffers)
        if count == 0:
            return None
        data: dict[str, list[Any]] = {}
        timestamps: dict[str, list[Any]] = {}
        time: list[float] = []
        for signal, buffer in zip(signals, buffers, strict=True):
            assert buffer is not None
            data[signal.name], timestamps[signal.name] = buffer.take(count)
            time = time or timestamps[signal.name]
        return PartialEventPage(data=data, timestamps=timestamps, time=time)
