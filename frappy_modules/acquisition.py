"""Fast, deterministic modules with the SECoP acquisition interface classes.

An acquisition here counts: while acquiring, the ``value`` of every channel
goes up by one every ``tick`` seconds and every step is sent as an update, so
tests see the intermediate values of a cycle.
"""

import threading
import time

from frappy.core import (
    BUSY,
    ERROR,
    IDLE,
    ArrayOf,
    BoolType,
    Command,
    Drivable,
    Enum,
    FloatRange,
    IntRange,
    Parameter,
    StatusType,
)
from frappy.lib import mkthread
from frappy.modules import (
    Acquisition,
    AcquisitionChannel,
    AcquisitionController,
    Module,
)


class _CountingSim(Module):
    """The acquisition cycle of `FastAcquisition` and `FastController`.

    A cycle ends when the ``value`` of a channel with ``goal_enable`` reaches
    its ``goal``, or else when the values reach ``limit``. ``hold`` pauses
    counting and the next ``go`` continues; ``stop`` ends the cycle and the
    next ``go`` starts again from 0. With ``fail`` set, the cycle ends in
    ERROR.
    """

    Status = Enum(Drivable.Status, PREPARED=150, PREPARING=340)
    status = Parameter(datatype=StatusType(Status))

    tick = Parameter(
        "time per count", FloatRange(0.001, 1, unit="s"), default=0.01, readonly=False
    )
    limit = Parameter(
        "counts after which a cycle without active goal ends",
        IntRange(1, 1000),
        default=5,
        readonly=False,
    )
    fail = Parameter(
        "let the next cycle fail", BoolType(), default=False, readonly=False
    )

    def _counted(self) -> list:
        """The modules whose 'value' counts."""
        raise NotImplementedError

    def _set_channels_status(self, code, text) -> None:
        """Status of the channels (other than this module)."""

    def initModule(self):
        super().initModule()
        self._lock = threading.Lock()
        self._stopflag = False
        self._counting = False
        # the next go starts a new cycle (from 0) instead of continuing one
        self._fresh = True
        self._thread = mkthread(self._sim)

    def initialReads(self):
        self.status = self.Status.IDLE, "ready"

    def read_status(self):
        return self.status

    def _end(self, code, text) -> None:
        self._counting = False
        self._fresh = True
        self.status = code, text
        self._set_channels_status(code, text)

    def go(self):
        with self._lock:
            if self._counting:
                return
            if self._fresh:
                for module in self._counted():
                    module.value = 0
                self._fresh = False
            self._counting = True
            self.status = self.Status.BUSY, "acquiring"
            self._set_channels_status(BUSY, "acquiring")

    def prepare(self):
        with self._lock:
            if self._counting:
                return
            self._fresh = True
            self.status = self.Status.PREPARED, "prepared"

    def hold(self):
        with self._lock:
            if not self._counting:
                return
            self._counting = False
            self.status = self.Status.PREPARED, "held"
            self._set_channels_status(IDLE, "held")

    def stop(self):
        with self._lock:
            self._end(self.Status.IDLE, "stopped")

    def _sim(self):
        while not self._stopflag:
            time.sleep(self.tick)
            with self._lock:
                if not self._counting:
                    continue
                if self.fail:
                    self._end(self.Status.ERROR, "acquisition failed")
                    continue
                modules = self._counted()
                for module in modules:
                    module.value = module.value + 1
                goals = [
                    module.value >= module.goal
                    for module in modules
                    if module.goal_enable
                ]
                if any(goals) or (
                    not goals and max(m.value for m in modules) >= self.limit
                ):
                    self._end(self.Status.IDLE, "done")

    def shutdownModule(self):
        self._stopflag = True
        if self._thread and self._thread.is_alive():
            self._thread.join()


class FastAcquisition(_CountingSim, Acquisition):
    """Single-module acquisition counting up while it runs."""

    value = Parameter("counts of the current/last cycle", IntRange(), default=0)
    # activate the optional parameters
    goal = Parameter(datatype=FloatRange(0, 1000), default=0)
    goal_enable = Parameter(default=False)

    def read_value(self):
        return self.value

    def _counted(self) -> list:
        return [self]


class _SpectrumMixin:
    """``get_data``: a spectrum of ``bins`` bins, each holding the counts of
    the cycle so far."""

    bins = Parameter("number of bins", IntRange(1, 64), default=4, readonly=False)
    value: int

    @Command(result=ArrayOf(IntRange(), 0, 64))
    def get_data(self):
        """the spectrum of the current/last cycle"""
        return [self.value] * self.bins


class SpectrumAcquisition(_SpectrumMixin, FastAcquisition):
    """FastAcquisition whose full data comes from ``get_data``."""


class FastChannel(AcquisitionChannel):
    """Channel of a `FastController`: counts while the controller acquires."""

    value = Parameter("counts of the current/last cycle", IntRange(), default=0)
    # activate the optional parameters
    goal = Parameter(datatype=FloatRange(0, 1000), default=0)
    goal_enable = Parameter(default=False)

    def initialReads(self):
        self.status = IDLE, "ready"

    def read_value(self):
        return self.value

    def read_status(self):
        return self.status


class SpectrumChannel(_SpectrumMixin, FastChannel):
    """FastChannel whose full data comes from ``get_data``."""


class FastController(_CountingSim, AcquisitionController):
    """Controller counting up the values of its channels while it runs."""

    def _counted(self) -> list:
        return list(self.channels.values())

    def _set_channels_status(self, code, text) -> None:
        if code >= ERROR:
            code = ERROR
        elif code >= BUSY:
            code = BUSY
        else:
            code = IDLE
        for channel in self.channels.values():
            channel.status = code, text
