# mypy: disable-error-code="attr-defined"


from ophyd_async.core import SignalR, init_devices

from secop_ophyd.SECoPDevices import SECoPMoveableDevice, SECoPNodeDevice


async def test_subset_signals_annotation(cryo_sim):

    from typing import Annotated as A

    from ophyd_async.core import SignalRW
    from ophyd_async.core import StandardReadableFormat as Format
    from ophyd_async.core import StrictEnum

    from secop_ophyd.SECoPDevices import ParameterMemberType, ParameterType

    class Cryostat_Mode_Enum(StrictEnum):
        """mode enum for `Cryostat`."""

        RAMP = "ramp"
        PID = "pid"
        OPENLOOP = "openloop"

    class Cryostat(SECoPMoveableDevice):
        """A simulated cc cryostat with heat-load, specific heat for the sample and a
        temperature dependent heat-link between sample and regulation."""

        # Module Parameters
        value: A[
            SignalR[float], ParameterType(), Format.HINTED_SIGNAL
        ]  # regulation temperature; Unit: (K)
        # status is StatusType == TupleOf(EnumType, StringType), always split
        status_0: A[SignalR[int], ParameterMemberType()]  # status code
        status_1: A[SignalR[str], ParameterMemberType()]  # status message
        target: A[
            SignalRW[float], ParameterType(), Format.HINTED_SIGNAL
        ]  # target temperature; Unit: (K)

    class Cryo_7_frappy_demo(SECoPNodeDevice):
        """short description

        This is a very long description providing all the gory details about the stuff
        we are describing.
        """

        # Module Devices
        cryo: Cryostat

    async with init_devices():
        cryo = Cryo_7_frappy_demo(
            sec_node_uri="localhost:10769",
        )

    val_read = await cryo.cryo.read()
    assert val_read != {}


async def test_enum_annotation(cryo_sim):

    from typing import Annotated as A

    from numpy import ndarray
    from ophyd_async.core import SignalR, SignalRW, SignalW
    from ophyd_async.core import StandardReadableFormat as Format
    from ophyd_async.core import StrictEnum

    from secop_ophyd.SECoPDevices import (
        ParameterMemberType,
        ParameterType,
        PropertyType,
        SECoPMoveableDevice,
        SECoPNodeDevice,
    )

    class Cryostat_Mode_Enum(StrictEnum):
        """mode enum for `Cryostat`."""

        RAMP = "ramp"
        PID = "pid"
        OPENLOOP = "openloop"

    class Cryostat(SECoPMoveableDevice):
        """A simulated cc cryostat with heat-load, specific heat for the sample and a
        temperature dependent heat-link between sample and regulation."""

        # Module Properties
        group: A[SignalR[str], PropertyType()]
        description: A[SignalR[str], PropertyType()]
        implementation: A[SignalR[str], PropertyType()]
        interface_classes: A[SignalR[ndarray], PropertyType()]
        features: A[SignalR[ndarray], PropertyType()]

        # Module Parameters
        value: A[
            SignalR[float], ParameterType(), Format.HINTED_SIGNAL
        ]  # regulation temperature; Unit: (K)
        # status is StatusType == TupleOf(EnumType, StringType), always split
        status_0: A[SignalR[int], ParameterMemberType()]  # status code
        status_1: A[SignalR[str], ParameterMemberType()]  # status message
        target: A[
            SignalRW[float], ParameterType(), Format.HINTED_SIGNAL
        ]  # target temperature; Unit: (K)
        ramp: A[
            SignalRW[float], ParameterType()
        ]  # ramping speed of the setpoint; Unit: (K/min)
        setpoint: A[
            SignalR[float], ParameterType()
        ]  # current setpoint during ramping else target; Unit: (K)
        mode: A[SignalRW[Cryostat_Mode_Enum], ParameterType()]  # mode of regulation
        maxpower: A[SignalRW[float], ParameterType()]  # Maximum heater power; Unit: (W)
        heater: A[SignalR[float], ParameterType()]  # current heater setting; Unit: (%)
        heaterpower: A[
            SignalR[float], ParameterType()
        ]  # current heater power; Unit: (W)
        # pid is TupleOf(FloatRange, FloatRange, FloatRange), writable and
        # depth 1 -> split read-only members plus a write-only SignalW
        pid_0: A[SignalR[float], ParameterMemberType()]  # regulation coefficient 'p'
        pid_1: A[SignalR[float], ParameterMemberType()]  # regulation coefficient 'i'
        pid_2: A[SignalR[float], ParameterMemberType()]  # regulation coefficient 'd'
        pid: A[SignalW[ndarray], ParameterType()]  # regulation coefficients
        p: A[
            SignalRW[float], ParameterType()
        ]  # regulation coefficient 'p'; Unit: (%/K)
        i: A[SignalRW[float], ParameterType()]  # regulation coefficient 'i'
        d: A[SignalRW[float], ParameterType()]  # regulation coefficient 'd'
        tolerance: A[
            SignalRW[float], ParameterType()
        ]  # temperature range for stability checking; Unit: (K)
        window: A[
            SignalRW[float], ParameterType()
        ]  # time window for stability checking; Unit: (s)
        timeout: A[
            SignalRW[float], ParameterType()
        ]  # max waiting time for stabilisation check; Unit: (s)

    class Cryo_7_frappy_demo(SECoPNodeDevice):
        """short description

        This is a very long description providing all the gory details about the stuff
        we are describing.
        """

        # Module Devices
        cryo: Cryostat

        # Node Properties
        equipment_id: A[SignalR[str], PropertyType()]
        firmware: A[SignalR[str], PropertyType()]
        description: A[SignalR[str], PropertyType()]
        _interfaces: A[SignalR[ndarray], PropertyType()]

    async with init_devices():
        cryo = Cryo_7_frappy_demo(
            sec_node_uri="localhost:10769",
        )

    await cryo.cryo.mode.set(Cryostat_Mode_Enum.RAMP)
    mode_read = await cryo.cryo.mode.get_value()

    print(mode_read)
    assert mode_read == Cryostat_Mode_Enum.RAMP
