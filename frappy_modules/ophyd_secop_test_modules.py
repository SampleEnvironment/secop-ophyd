import random
import string
import time

import numpy as np
from frappy.core import Command, Drivable, IntRange, Parameter, Readable, StructOf
from frappy.datatypes import (
    ArrayOf,
    BoolType,
    EnumType,
    FloatRange,
    StatusType,
    StringType,
    TupleOf,
)
from frappy.errors import ImpossibleError
from frappy.lib import clamp, mkthread
from frappy.lib.enum import Enum

# test custom property (value.test can be changed in config file)


class Test_Mod_str(Readable):
    Status = Enum(Readable.Status)

    status = Parameter(datatype=StatusType(Status))

    value = Parameter("string read value", StringType())

    def get_random_string(self, length):
        # choose from all lowercase letter
        letters = string.ascii_lowercase
        return "".join(random.choice(letters) for i in range(length))

    def read_value(self):
        return self.get_random_string(10)

    def read_status(self):
        # instead of asking a 'Hardware' take the value from the simulation
        return self.status


class Test_ND_arrays(Readable):
    Status = Enum(Readable.Status)

    status = Parameter(datatype=StatusType(Status))

    value = Parameter(
        "2D integer Array",
        datatype=ArrayOf(
            members=ArrayOf(members=IntRange(min=0, max=100), minlen=5, maxlen=5),
            minlen=5,
            maxlen=5,
        ),
        readonly=True,
    )

    arr3d = Parameter(
        "3D integer Array",
        datatype=ArrayOf(
            ArrayOf(
                members=ArrayOf(
                    members=IntRange(min=0, max=100), minlen=5, maxlen=5
                ),
                minlen=5,
                maxlen=5,
            ),
            minlen=5,
            maxlen=5,
        ),
        readonly=False,
    )

    def read_value(self):
        arr2d = np.random.randint(0, 10, 5**2).reshape(5, 5)

        return arr2d.tolist()

    def write_arr3d(self, val):
        self.arr3d = val

    def read_arr3d(self):
        return self.arr3d


class Test_Struct_of_arrays(Readable):
    Status = Enum(Readable.Status)

    status = Parameter(datatype=StatusType(Status))

    value = Parameter(
        "struct of arrays containing primitive datatypes",
        datatype=StructOf(
            ints=ArrayOf(IntRange(), minlen=5, maxlen=5),
            strings=ArrayOf(StringType(), minlen=5, maxlen=5),
            floats=ArrayOf(FloatRange(), minlen=5, maxlen=5),
        ),
        readonly=True,
    )

    writable_strct_of_arr = Parameter(
        "writable struct of arrays containing primitive datatypes",
        datatype=StructOf(
            ints=ArrayOf(IntRange(), minlen=5, maxlen=5),
            strings=ArrayOf(StringType(), minlen=5, maxlen=5),
            floats=ArrayOf(FloatRange(), minlen=5, maxlen=5),
        ),
        readonly=False,
    )

    def read_value(self):
        strings = [
            "".join(random.choices(string.ascii_lowercase, k=5)) for _ in range(0, 5)
        ]
        ints = random.sample(range(0, 50), 5)
        floats = [random.random() for _ in range(0, 5)]

        return {"ints": ints, "strings": strings, "floats": floats}

    def read_writable_strct_of_arr(self):
        return self.writable_strct_of_arr

    def write_writable_strct_of_arr(self, val):
        self.writable_strct_of_arr = val


class OPHYD_test_primitive_arrays(Readable):

    value = Parameter("struct Value", datatype=FloatRange(), default=1.0, readonly=True)
    arr_int = Parameter(
        "array of ints",
        ArrayOf(IntRange(), 0, 10),
        readonly=True,
        default=[1, 2, 4, 5, 6, 7],
    )

    arr_float = Parameter(
        "array of floats",
        ArrayOf(FloatRange(), 0, 10),
        readonly=True,
        default=[1.2, 1.4],
    )

    arr_arr_float = Parameter(
        "array of floats",
        ArrayOf(ArrayOf(FloatRange(), 0, 10), 0, 10),
        readonly=True,
        default=[[2.3, 2.4], [1.2, 1.4]],
    )

    arr_bool = Parameter(
        "array of floats",
        ArrayOf(BoolType(), 0, 10),
        readonly=True,
        default=[True, False, True],
    )

    arr_String = Parameter(
        "array of Strings",
        ArrayOf(StringType(maxchars=20), 0, 10),
        readonly=True,
        default=["abdf", "sds", "ass"],
    )

    arr_String_nomax = Parameter(
        "array of Strings",
        ArrayOf(StringType(), 0, 10),
        readonly=True,
        default=["abdfefcsdcsdcsdcsdcsdcsdcsdc", "sds", "ass"],
    )


class OPYD_test_struct(Drivable):
    Status = Enum(Drivable.Status)  #: status codes

    status = Parameter(datatype=StatusType(Status))  # override Readable.status

    p_start = Parameter(
        "starting temperature for simulation",
        datatype=IntRange(0),
        default=5,
        export=False,
    )

    value = Parameter(
        "struct Value",
        datatype=StructOf(
            x=FloatRange(0, 100, unit="m"),
            y=FloatRange(0, 100, unit="m"),
            z=FloatRange(0, 100, unit="m"),
            color=StringType(),
        ),
    )

    target = Parameter(
        "struct target",
        datatype=StructOf(
            x=FloatRange(0, 100, unit="m"),
            y=FloatRange(0, 100, unit="m"),
            z=FloatRange(0, 100, unit="m"),
            color=StringType(),
        ),
    )

    nested_struct = Parameter(
        "nestedstruct dict containing other structs and tuples ",
        datatype=StructOf(
            number=FloatRange(0, 100, unit="s"),
            string=StringType(),
            tupl=TupleOf(FloatRange(0, 100), FloatRange(0, 100), FloatRange(0, 100)),
            pos_struct=StructOf(
                x=FloatRange(0, 100, unit="m"),
                y=FloatRange(0, 100, unit="m"),
                z=FloatRange(0, 100, unit="m"),
                col=StringType(),
                enum=EnumType(
                    "Test Enum", {"mode_zero": 0, "mode_one": 1, "mode_max": 2}
                ),
            ),
        ),
        readonly=False,
    )

    tuple_param = Parameter(
        "tuple parameter for testing",
        datatype=TupleOf(
            FloatRange(0, 100, unit="m"),
            FloatRange(0, 100, unit="m"),
            FloatRange(0, 100, unit="m"),
            StringType(),
        ),
        readonly=False,
    )

    tolerance = Parameter(
        "distance range for stability checking",
        datatype=FloatRange(0, 10),
        default=0.1,
        unit="m",
        readonly=False,
        group="stability",
    )

    looptime = Parameter(
        "timestep for simulation",
        datatype=FloatRange(0.01, 10),
        unit="s",
        default=1,
        readonly=False,
        export=False,
    )

    def read_value(self):
        return self.value

    def read_target(self):
        return self.target

    def read_status(self):
        # instead of asking a 'Hardware' take the value from the simulation
        return self.status

    def write_nested_struct(self, value):
        self.nested_struct = value

    def write_tuple_param(self, value):
        self.tuple_param = value

    def write_target(self, value):

        self.target = value
        # next read_status will see this status, until the loop updates it
        self.status = self.Status.BUSY, "new target set"
        return value

    @Command(
        StructOf(
            name=StringType(), id=IntRange(max=1000, min=0), sort=BoolType()
        ),
        result=IntRange(),
    )
    def test_cmd(self, name, id, sort):
        """testing with ophyd secop integration"""
        if name == "bad_name":
            raise ImpossibleError("bad name received")
        return random.randint(0, 1000)

    def initModule(self):
        super().initModule()
        self._stopflag = False
        self._thread = mkthread(self.thread)

    def thread(self):
        self.p_x = self.p_start
        self.p_y = self.p_start
        self.p_z = self.p_start

        self.t_x = self.p_start
        self.t_y = self.p_start
        self.t_z = self.p_start

        self.col = "blue"
        self.tcol = "blue"

        self.status = self.Status.IDLE, ""

        while not self._stopflag:
            try:
                self.__sim()
            except Exception as e:
                self.log.exception(e)
                self.status = self.Status.ERROR, str(e)

    def __heat(self, step, tolerance, target, pos):

        deviation = abs(pos - target)

        if deviation < tolerance:
            return pos, deviation

        if target > pos:
            return pos + step, deviation
        else:
            return pos - step, deviation

    def __sim(self):
        # complex thread handling:
        # a) simulation of cryo (heat flow, thermal masses,....)
        # b) optional PID temperature controller with windup control
        # c) generating status+updated value+ramp
        # this thread is not supposed to exit!

        # local state keeping:
        px = self.p_x
        py = self.p_y
        pz = self.p_z

        tx = self.t_x
        ty = self.t_y
        tz = self.t_z

        cols = ["blue", "red", "green", "yellow"]

        # keep history values for stability check

        timestamp = time.time()

        damper = 1

        while not self._stopflag:
            t = time.time()
            h = t - timestamp
            if h < self.looptime / damper:
                time.sleep(clamp(self.looptime / damper - h, 0.1, 60))
                continue
            # a)

            val = self.read_value()
            tar = self.read_target()

            px = val["x"]
            py = val["y"]
            pz = val["z"]

            tx = tar["x"]
            ty = tar["y"]
            tz = tar["z"]
            tcol = tar["color"]
            pcol = val["color"]

            new_px, dev_x = self.__heat(
                step=1, tolerance=self.tolerance, target=tx, pos=px
            )
            new_py, dev_y = self.__heat(
                step=1, tolerance=self.tolerance, target=ty, pos=py
            )
            new_pz, dev_z = self.__heat(
                step=1, tolerance=self.tolerance, target=tz, pos=pz
            )

            if tcol != pcol:
                new_col = cols[random.randint(0, 3)]
            else:
                new_col = pcol

            newpos = {"x": new_px, "y": new_py, "z": new_pz, "color": new_col}

            self.value = newpos
            # temperature is stable when all recorded values in the window
            # differ from setpoint by less than tolerance
            currenttime = time.time()

            # obtain min/max
            deviation = 0

            maxdev = abs(max([dev_x, dev_y, dev_z]))

            if maxdev > self.tolerance or pcol != tcol:
                self.status = self.Status.BUSY, "unstable"
            else:
                self.status = self.Status.IDLE, "at target"

            timestamp = t
            self.read_value()

    def shutdown(self):
        # should be called from server when the server is stopped
        self._stopflag = True
        if self._thread and self._thread.is_alive():
            self._thread.join()
