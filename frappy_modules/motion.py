"""Fast, deterministic modules for exercising the SECoP interface classes.

Unlike the cryostat simulation, a move here takes ``distance / speed`` seconds
and a measurement ``duration`` seconds, so tests finish in well under a second
and still observe the BUSY -> IDLE transition.
"""

import threading
import time

from frappy.core import (
    BoolType,
    Command,
    Communicator,
    Drivable,
    Enum,
    EnumType,
    FloatRange,
    IntRange,
    Parameter,
    Readable,
    StatusType,
    StringType,
    StructOf,
    TupleOf,
    Writable,
)
from frappy.lib import mkthread


class FastMover(Drivable):
    """Linear axis that moves to its target at a constant speed."""

    value = Parameter("current position", FloatRange(unit="mm"), default=0)
    target = Parameter(
        "target position", FloatRange(unit="mm"), default=0, readonly=False
    )
    speed = Parameter(
        "travel speed",
        FloatRange(0.1, 1000, unit="mm/s"),
        default=10,
        readonly=False,
    )
    error_limit = Parameter(
        "moving beyond this position puts the module into ERROR",
        FloatRange(unit="mm"),
        default=1000,
        readonly=False,
    )
    looptime = Parameter(
        "simulation step",
        FloatRange(0.001, 1, unit="s"),
        default=0.01,
        export=False,
    )

    def initModule(self):
        super().initModule()
        self._lock = threading.Lock()
        self._stopflag = False
        self._thread = mkthread(self._sim)

    def initialReads(self):
        self.status = self.Status.IDLE, "at target"

    def read_value(self):
        return self.value

    def read_target(self):
        return self.target

    def read_status(self):
        return self.status

    def write_target(self, value):
        with self._lock:
            self.target = value
            self.status = self.Status.BUSY, "moving"
        return value

    @Command()
    def stop(self):
        """stop the axis at its current position"""
        with self._lock:
            self.target = self.value
            self.status = self.Status.IDLE, "stopped"

    def _sim(self):
        last = time.time()
        while not self._stopflag:
            time.sleep(self.looptime)
            now = time.time()
            dt, last = now - last, now
            with self._lock:
                if self.status[0] != self.Status.BUSY:
                    continue
                pos, target = self.value, self.target
                if abs(pos) > self.error_limit:
                    self.status = self.Status.ERROR, "position limit exceeded"
                    continue
                step = self.speed * dt
                if abs(target - pos) <= step:
                    self.value = target
                    self.status = self.Status.IDLE, "at target"
                else:
                    self.value = pos + step if target > pos else pos - step

    def shutdown(self):
        self._stopflag = True
        if self._thread and self._thread.is_alive():
            self._thread.join()


class _FastVectorMover(Drivable):
    """Several axes moving together at a constant speed. Subclasses define the
    datatypes of ``value`` and ``target`` and how they map to axis positions."""

    speed = Parameter(
        "travel speed of every axis",
        FloatRange(0.1, 1000, unit="mm/s"),
        default=10,
        readonly=False,
    )
    error_limit = Parameter(
        "an axis moving beyond this position puts the module into ERROR",
        FloatRange(unit="mm"),
        default=1000,
        readonly=False,
    )
    looptime = Parameter(
        "simulation step",
        FloatRange(0.001, 1, unit="s"),
        default=0.01,
        export=False,
    )

    def _positions(self, target):
        """axis positions requested by ``target``"""
        raise NotImplementedError

    def _as_value(self, positions, target):
        """``value`` for the given axis positions"""
        raise NotImplementedError

    def _as_target(self, positions, target):
        """``target`` for the given axis positions, keeping the rest of it"""
        raise NotImplementedError

    def initModule(self):
        super().initModule()
        self._lock = threading.Lock()
        self._stopflag = False
        self._pos = list(self._positions(self.target))
        self._thread = mkthread(self._sim)

    def initialReads(self):
        self.value = self._as_value(self._pos, self.target)
        self.status = self.Status.IDLE, "at target"

    def read_value(self):
        return self.value

    def read_target(self):
        return self.target

    def read_status(self):
        return self.status

    def write_target(self, value):
        with self._lock:
            self.target = value
            self.status = self.Status.BUSY, "moving"
        return value

    @Command()
    def stop(self):
        """stop all axes at their current position"""
        with self._lock:
            self.target = self._as_target(self._pos, self.target)
            self.status = self.Status.IDLE, "stopped"

    def _sim(self):
        last = time.time()
        while not self._stopflag:
            time.sleep(self.looptime)
            now = time.time()
            dt, last = now - last, now
            with self._lock:
                if self.status[0] != self.Status.BUSY:
                    continue
                if any(abs(pos) > self.error_limit for pos in self._pos):
                    self.status = self.Status.ERROR, "position limit exceeded"
                    continue
                step = self.speed * dt
                goal = self._positions(self.target)
                arrived = True
                for axis, (pos, end) in enumerate(zip(self._pos, goal, strict=True)):
                    if abs(end - pos) <= step:
                        self._pos[axis] = end
                    else:
                        self._pos[axis] = pos + step if end > pos else pos - step
                        arrived = False
                self.value = self._as_value(self._pos, self.target)
                if arrived:
                    self.status = self.Status.IDLE, "at target"

    def shutdown(self):
        self._stopflag = True
        if self._thread and self._thread.is_alive():
            self._thread.join()


_XY_MODE = StructOf(
    x=FloatRange(unit="mm"),
    y=FloatRange(unit="mm"),
    mode=EnumType("mode", relaxed=0, strict=1),
)


class FastStructMover(_FastVectorMover):
    """Two axes whose value and target are structs."""

    value = Parameter("current position", _XY_MODE, default=dict(x=0, y=0, mode=0))
    target = Parameter(
        "target position", _XY_MODE, default=dict(x=0, y=0, mode=0), readonly=False
    )

    def _positions(self, target):
        return [target["x"], target["y"]]

    def _as_value(self, positions, target):
        return dict(x=positions[0], y=positions[1], mode=target["mode"])

    _as_target = _as_value


class FastTupleMover(_FastVectorMover):
    """Two axes whose value and target are tuples."""

    value = Parameter(
        "current position",
        TupleOf(FloatRange(unit="mm"), FloatRange(unit="mm")),
        default=(0, 0),
    )
    target = Parameter(
        "target position",
        TupleOf(FloatRange(unit="mm"), FloatRange(unit="mm")),
        default=(0, 0),
        readonly=False,
    )

    def _positions(self, target):
        return list(target)

    def _as_value(self, positions, target):
        return tuple(positions)

    _as_target = _as_value


class FastTrigger(Readable):
    """Measurement that runs for ``duration`` seconds after ``go``.

    Each measurement increments ``value``. With ``fail`` set, the measurement
    ends in ERROR instead of IDLE.
    """

    interface_classes = ["Triggerable", "Readable"]

    Status = Enum(Drivable.Status)  # Readable has no BUSY
    status = Parameter(datatype=StatusType(Status))

    value = Parameter("number of finished measurements", IntRange(), default=0)
    duration = Parameter(
        "measurement time",
        FloatRange(0, 10, unit="s"),
        default=0.1,
        readonly=False,
    )
    fail = Parameter(
        "let the next measurement fail", BoolType(), default=False, readonly=False
    )

    def initModule(self):
        super().initModule()
        self._timer = None

    def initialReads(self):
        self.status = self.Status.IDLE, "ready"

    def read_value(self):
        return self.value

    def read_status(self):
        return self.status

    def _finish(self):
        if self.fail:
            self.status = self.Status.ERROR, "measurement failed"
        else:
            self.value = self.value + 1
            self.status = self.Status.IDLE, "measurement done"

    @Command()
    def go(self):
        """start a measurement"""
        self.status = self.Status.BUSY, "measuring"
        self._timer = threading.Timer(self.duration, self._finish)
        self._timer.start()

    @Command()
    def stop(self):
        """abort the measurement"""
        if self._timer:
            self._timer.cancel()
        self.status = self.Status.IDLE, "stopped"


class InstantWritable(Writable):
    """Writable whose value follows the target immediately."""

    value = Parameter("current value", FloatRange(), default=0)
    target = Parameter("target value", FloatRange(), default=0, readonly=False)

    def initialReads(self):
        self.status = self.Status.IDLE, ""

    def read_value(self):
        return self.value

    def read_status(self):
        return self.status

    def write_target(self, value):
        self.value = value
        return value


class SlowSwitch(Writable):
    """Writable with a struct target that needs ``duration`` seconds to reach
    it and cannot be stopped. With ``fail`` set, it ends in ERROR instead."""

    Status = Enum(Drivable.Status)  # Writable has no BUSY
    status = Parameter(datatype=StatusType(Status))

    value = Parameter(
        "current state",
        StructOf(on=BoolType(), level=FloatRange(0, 100)),
        default=dict(on=False, level=0),
    )
    target = Parameter(
        "requested state",
        StructOf(on=BoolType(), level=FloatRange(0, 100)),
        default=dict(on=False, level=0),
        readonly=False,
    )
    duration = Parameter(
        "time needed to reach the target",
        FloatRange(0, 10, unit="s"),
        default=0.1,
        readonly=False,
    )
    fail = Parameter(
        "let the next change fail", BoolType(), default=False, readonly=False
    )

    def initModule(self):
        super().initModule()
        self._timer = None

    def initialReads(self):
        self.status = self.Status.IDLE, "at target"

    def read_value(self):
        return self.value

    def read_target(self):
        return self.target

    def read_status(self):
        return self.status

    def _finish(self):
        if self.fail:
            self.status = self.Status.ERROR, "switching failed"
        else:
            self.value = self.target
            self.status = self.Status.IDLE, "at target"

    def write_target(self, value):
        self.target = value
        self.status = self.Status.BUSY, "switching"
        if self._timer:
            self._timer.cancel()
        self._timer = threading.Timer(self.duration, self._finish)
        self._timer.start()
        return value


class EchoCommunicator(Communicator):
    """Communicator answering every command with an echo."""

    @Command(StringType(), result=StringType())
    def communicate(self, command):
        """send a command, the reply is an echo of it"""
        return f"echo: {command}"
