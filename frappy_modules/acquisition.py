"""Fast, deterministic modules with the SECoP acquisition interface classes.

An acquisition here counts: while acquiring, ``value`` goes up by one every
``tick`` seconds and every step is sent as an update, so tests see the
intermediate values of a cycle.
"""

import threading
import time

from frappy.core import (
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
from frappy.modules import Acquisition


class FastAcquisition(Acquisition):
    """Single-module acquisition counting up while it runs.

    A cycle ends when ``value`` reaches ``goal`` (with ``goal_enable``) or else
    ``limit``. ``hold`` pauses counting and the next ``go`` continues; ``stop``
    ends the cycle and the next ``go`` starts again from 0. With ``fail`` set,
    the cycle ends in ERROR.
    """

    Status = Enum(Drivable.Status, PREPARED=150, PREPARING=340)
    status = Parameter(datatype=StatusType(Status))

    value = Parameter("counts of the current/last cycle", IntRange(), default=0)
    # activate the optional parameters
    goal = Parameter(datatype=FloatRange(0, 1000), default=0)
    goal_enable = Parameter(default=False)

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

    def read_value(self):
        return self.value

    def read_status(self):
        return self.status

    def go(self):
        with self._lock:
            if self._counting:
                return
            if self._fresh:
                self.value = 0
                self._fresh = False
            self._counting = True
            self.status = self.Status.BUSY, "acquiring"

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

    def stop(self):
        with self._lock:
            self._counting = False
            self._fresh = True
            self.status = self.Status.IDLE, "stopped"

    def _sim(self):
        while not self._stopflag:
            time.sleep(self.tick)
            with self._lock:
                if not self._counting:
                    continue
                if self.fail:
                    self._counting = False
                    self._fresh = True
                    self.status = self.Status.ERROR, "acquisition failed"
                    continue
                self.value = self.value + 1
                end = self.goal if self.goal_enable else self.limit
                if self.value >= end:
                    self._counting = False
                    self._fresh = True
                    self.status = self.Status.IDLE, "done"

    def shutdownModule(self):
        self._stopflag = True
        if self._thread and self._thread.is_alive():
            self._thread.join()


class SpectrumAcquisition(FastAcquisition):
    """FastAcquisition whose full data comes from ``get_data``: a spectrum of
    ``bins`` bins, each holding the counts of the cycle so far."""

    bins = Parameter("number of bins", IntRange(1, 64), default=4, readonly=False)

    @Command(result=ArrayOf(IntRange(), 0, 64))
    def get_data(self):
        """the spectrum of the current/last cycle"""
        return [self.value] * self.bins
