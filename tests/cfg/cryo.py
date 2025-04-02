#  -*- coding: utf-8 -*-
# *****************************************************************************
# This program is free software; you can redistribute it and/or modify it under
# the terms of the GNU General Public License as published by the Free Software
# Foundation; either version 2 of the License, or (at your option) any later
# version.
#
# This program is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
# FOR A PARTICULAR PURPOSE.  See the GNU General Public License for more
# details.
#
# You should have received a copy of the GNU General Public License along with
# this program; if not, write to the Free Software Foundation, Inc.,
# 59 Temple Place, Suite 330, Boston, MA  02111-1307  USA
#
# Module authors:
#   Enrico Faulhaber <enrico.faulhaber@frm2.tum.de>
#
# *****************************************************************************
"""playing implementation of a (simple) simulated cryostat"""


import random
import time
from math import atan

from frappy.datatypes import BoolType, EnumType, FloatRange, StringType, TupleOf
from frappy.lib import clamp, mkthread
from frappy.modules import Command, Drivable, Parameter

# test custom property (value.test can be changed in config file)
from frappy.properties import Property


class TestParameter(Parameter):
    test = Property(
        "A Property for testing purposes", StringType(), default="", export=True
    )


class CryoBase(Drivable):
    is_cryo = Property(
        "private Flag if this is a cryostat", BoolType(), default=True, export=True
    )


class Cryostat(CryoBase):
    """simulated cryostat with:

    - heat capacity of the sample
    - cooling power
    - thermal transfer between regulation and samplen
    """

    jitter = Parameter(
        "amount of random noise on readout values",
        datatype=FloatRange(0, 1),
        unit="K",
        default=0.1,
        readonly=False,
        export=False,
    )
    T_start = Parameter(
        "starting temperature for simulation",
        datatype=FloatRange(0),
        default=10,
        export=False,
    )
    looptime = Parameter(
        "timestep for simulation",
        datatype=FloatRange(0.01, 10),
        unit="s",
        default=1,
        readonly=False,
        export=False,
    )
    ramp = Parameter(
        "ramping speed of the setpoint",
        datatype=FloatRange(0, 1e3),
        unit="K/min",
        default=1,
        readonly=False,
    )
    setpoint = Parameter(
        "current setpoint during ramping else target",
        datatype=FloatRange(),
        default=1,
        unit="K",
    )
    maxpower = Parameter(
        "Maximum heater power",
        datatype=FloatRange(0),
        default=1,
        unit="W",
        readonly=False,
        group="heater_settings",
    )
    heater = Parameter(
        "current heater setting",
        datatype=FloatRange(0, 100),
        default=0,
        unit="%",
        group="heater_settings",
    )
    heaterpower = Parameter(
        "current heater power",
        datatype=FloatRange(0),
        default=0,
        unit="W",
        group="heater_settings",
    )
    target = Parameter(
        "target temperature",
        datatype=FloatRange(0),
        default=0,
        unit="K",
        readonly=False,
    )
    value = TestParameter(
        "regulation temperature",
        datatype=FloatRange(0),
        default=0,
        unit="K",
        test="TEST",
    )
    pid = Parameter(
        "regulation coefficients",
        datatype=TupleOf(FloatRange(0), FloatRange(0, 100), FloatRange(0, 100)),
        default=(40, 10, 2),
        readonly=False,
        group="pid",
    )
    # pylint: disable=invalid-name
    p = Parameter(
        "regulation coefficient 'p'",
        datatype=FloatRange(0),
        default=40,
        unit="%/K",
        readonly=False,
        group="pid",
    )
    i = Parameter(
        "regulation coefficient 'i'",
        datatype=FloatRange(0, 100),
        default=10,
        readonly=False,
        group="pid",
    )
    d = Parameter(
        "regulation coefficient 'd'",
        datatype=FloatRange(0, 100),
        default=2,
        readonly=False,
        group="pid",
    )
    mode = Parameter(
        "mode of regulation",
        datatype=EnumType("mode", ramp=None, pid=None, openloop=None),
        default="ramp",
        readonly=False,
    )
    pollinterval = Parameter("polling interval", datatype=FloatRange(0), default=5)
    tolerance = Parameter(
        "temperature range for stability checking",
        datatype=FloatRange(0, 100),
        default=0.1,
        unit="K",
        readonly=False,
        group="stability",
    )
    window = Parameter(
        "time window for stability checking",
        datatype=FloatRange(1, 900),
        default=30,
        unit="s",
        readonly=False,
        group="stability",
    )
    timeout = Parameter(
        "max waiting time for stabilisation check",
        datatype=FloatRange(1, 36000),
        default=900,
        unit="s",
        readonly=False,
        group="stability",
    )

    def initModule(self):
        super().initModule()
        self._stopflag = False
        self._thread = mkthread(self.thread)

    def read_status(self):
        # instead of asking a 'Hardware' take the value from the simulation
        return self.status

    def read_value(self):
        # return regulation value (averaged regulation temp)
        return self.regulationtemp + self.jitter * (0.5 - random.random())

    def read_target(self):
        return self.target

    def write_target(self, value):
        value = round(value, 2)
        if value == self.target:
            # nothing to do
            return value
        self.target = value
        # next read_status will see this status, until the loop updates it
        self.status = self.Status.BUSY, "new target set"
        return value

    def read_maxpower(self):
        return self.maxpower

    def write_maxpower(self, newpower):
        # rescale heater setting in % to keep the power
        heat = max(0, min(100, self.heater * self.maxpower / float(newpower)))
        self.heater = heat
        self.maxpower = newpower
        return newpower

    def write_pid(self, newpid):
        self.p, self.i, self.d = newpid
        return (self.p, self.i, self.d)

    def read_pid(self):
        return (self.p, self.i, self.d)

    @Command()
    def stop(self):
        """Stop ramping the setpoint

        by setting the current setpoint as new target"""
        # XXX: discussion: take setpoint or current value ???
        self.write_target(self.setpoint)

    #
    # calculation helpers
    #
    def __coolerPower(self, temp):
        """returns cooling power in W at given temperature"""
        # quadratic up to 42K, is linear from 40W@42K to 100W@600K
        # return clamp((temp-2)**2 / 32., 0., 40.) + temp * 0.1
        return clamp(15 * atan(temp * 0.01) ** 3, 0.0, 40.0) + temp * 0.1 - 0.2

    def __coolerCP(self, temp):
        """heat capacity of cooler at given temp"""
        return 75 * atan(temp / 50) ** 2 + 1

    def __heatLink(self, coolertemp, sampletemp):
        """heatflow from sample to cooler. may be negative..."""
        flow = (sampletemp - coolertemp) * ((coolertemp + sampletemp) ** 2) / 400.0
        cp = clamp(self.__coolerCP(coolertemp) * self.__sampleCP(sampletemp), 1, 10)
        return clamp(flow, -cp, cp)

    def __sampleCP(self, temp):
        return 3 * atan(temp / 30) + 12 * temp / ((temp - 12.0) ** 2 + 10) + 0.5

    def __sampleLeak(self, temp):
        return 0.02 / temp

    def thread(self):
        self.sampletemp = self.T_start
        self.regulationtemp = self.T_start
        self.status = self.Status.IDLE, ""
        while not self._stopflag:
            try:
                self.__sim()
            except Exception as e:
                self.log.exception(e)
                self.status = self.Status.ERROR, str(e)

    def __sim(self):
        # complex thread handling:
        # a) simulation of cryo (heat flow, thermal masses,....)
        # b) optional PID temperature controller with windup control
        # c) generating status+updated value+ramp
        # this thread is not supposed to exit!

        self.setpoint = self.target
        # local state keeping:
        regulation = self.regulationtemp
        sample = self.sampletemp
        # keep history values for stability check
        window = []
        timestamp = time.time()
        heater = 0
        lastflow = 0
        last_heaters = (0, 0)
        delta = 0
        _I = _D = 0
        lastD = 0
        damper = 1
        lastmode = self.mode
        while not self._stopflag:
            t = time.time()
            h = t - timestamp
            if h < self.looptime / damper:
                time.sleep(clamp(self.looptime / damper - h, 0.1, 60))
                continue
            # a)
            sample = self.sampletemp
            regulation = self.regulationtemp
            heater = self.heater

            heatflow = self.__heatLink(regulation, sample)
            self.log.debug(
                "sample = %.5f, regulation = %.5f, heatflow = %.5g",
                sample,
                regulation,
                heatflow,
            )
            newsample = max(
                0,
                sample
                + (self.__sampleLeak(sample) - heatflow) / self.__sampleCP(sample) * h,
            )
            # avoid instabilities due to too small CP
            newsample = clamp(newsample, sample, regulation)
            regdelta = (
                heater * 0.01 * self.maxpower
                + heatflow
                - self.__coolerPower(regulation)
            )
            newregulation = max(
                0, regulation + regdelta / self.__coolerCP(regulation) * h
            )
            # b) see
            # http://brettbeauregard.com/blog/2011/04/
            # improving-the-beginners-pid-introduction/
            if self.mode != self.mode.openloop:
                # fix artefacts due to too big timesteps
                # actually i would prefer reducing looptime, but i have no
                # good idea on when to increase it back again
                if heatflow * lastflow != -100:
                    if (newregulation - newsample) * (regulation - sample) < 0:
                        # newregulation = (newregulation + regulation) / 2
                        # newsample = (newsample + sample) / 2
                        damper += 1
                lastflow = heatflow

                error = self.setpoint - newregulation
                # use a simple filter to smooth delta a little
                delta = (delta + regulation - newregulation) * 0.5

                kp = self.p * 0.1  # LakeShore P = 10*k_p
                ki = kp * abs(self.i) / 500.0  # LakeShore I = 500/T_i
                kd = kp * abs(self.d) / 2.0  # LakeShore D = 2*T_d
                _P = kp * error
                _I += ki * error * h
                _D = kd * delta / h

                # avoid reset windup
                _I = clamp(_I, 0.0, 100.0)  # _I is in %

                # avoid jumping heaterpower if switching back to pid mode
                if lastmode != self.mode:
                    # adjust some values upon switching back on
                    _I = self.heater - _P - _D

                v = _P + _I + _D
                # in damping mode, use a weighted sum of old + new heaterpower
                if damper > 1:
                    v = ((damper**2 - 1) * self.heater + v) / damper**2

                # damp oscillations due to D switching signs
                if _D * lastD < -0.2:
                    v = (v + heater) * 0.5
                # clamp new heater power to 0..100%
                heater = clamp(v, 0.0, 100.0)
                lastD = _D

                self.log.debug(
                    "PID: P = %.2f, I = %.2f, D = %.2f, " "heater = %.2f",
                    _P,
                    _I,
                    _D,
                    heater,
                )

                # check for turn-around points to detect oscillations ->
                # increase damper
                x, y = last_heaters
                if (x + 0.1 < y and y > heater + 0.1) or (
                    x > y + 0.1 and y + 0.1 < heater
                ):
                    damper += 1
                last_heaters = (y, heater)

            else:
                # self.heaterpower is set manually, not by pid
                heater = self.heater
                last_heaters = (0, 0)

            heater = round(heater, 1)
            sample = newsample
            regulation = newregulation
            lastmode = self.mode
            # c)
            if self.setpoint != self.target:
                if self.ramp == 0 or self.mode == self.mode.enum.pid:
                    maxdelta = 10000
                else:
                    maxdelta = self.ramp / 60.0 * h
                try:
                    self.setpoint = round(
                        self.setpoint
                        + clamp(self.target - self.setpoint, -maxdelta, maxdelta),
                        3,
                    )
                    self.log.debug(
                        "setpoint changes to %r (target %r)", self.setpoint, self.target
                    )
                except (TypeError, ValueError):
                    # self.target might be None
                    pass

            # temperature is stable when all recorded values in the window
            # differ from setpoint by less than tolerance
            currenttime = time.time()
            window.append((currenttime, sample))
            while window[0][0] < currenttime - self.window:
                # remove old/stale entries
                window.pop(0)
            # obtain min/max
            deviation = 0
            for _, _T in window:
                if abs(_T - self.target) > deviation:
                    deviation = abs(_T - self.target)
            if (len(window) < 3) or deviation > self.tolerance:
                self.status = self.Status.BUSY, "unstable"
            elif self.setpoint == self.target:
                self.status = self.Status.IDLE, "at target"
                damper -= (damper - 1) * 0.1  # max value for damper is 11
            else:
                self.status = self.Status.BUSY, "ramping setpoint"
            damper -= (damper - 1) * 0.05
            self.regulationtemp = round(regulation, 3)
            self.sampletemp = round(sample, 3)
            self.heaterpower = round(heater * self.maxpower * 0.01, 3)
            self.heater = heater
            timestamp = t
            self.read_value()

    def shutdownModule(self):
        # should be called from server when the server is stopped
        self._stopflag = True
        if self._thread and self._thread.is_alive():
            self._thread.join()
