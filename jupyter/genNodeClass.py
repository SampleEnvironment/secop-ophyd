from secop_ophyd.SECoPDevices import SECoPMoveableDevice, SECoPNodeDevice, SECoPCMDDevice 
from ophyd_async.core.signal import SignalRW, SignalR 


class MassflowController(SECoPMoveableDevice):
    status: SignalR
    group: SignalR
    description: SignalR
    implementation: SignalR
    interface_classes: SignalR
    features: SignalR
    value: SignalR
    target: SignalRW
    ramp: SignalRW
    gastype: SignalR
    tolerance: SignalR
    stop_CMD: SECoPCMDDevice
    test_cmd_CMD: SECoPCMDDevice


class PressureController(SECoPMoveableDevice):
    status: SignalR
    group: SignalR
    description: SignalR
    implementation: SignalR
    interface_classes: SignalR
    features: SignalR
    value: SignalR
    target: SignalRW
    ramp: SignalRW
    tolerance: SignalR
    stop_CMD: SECoPCMDDevice


class Gas_dosing(SECoPNodeDevice):
    equipment_id: SignalR
    firmware: SignalR
    version: SignalR
    description: SignalR
    interface: SignalR
    massflow_contr1: MassflowController
    massflow_contr2: MassflowController
    massflow_contr3: MassflowController
    backpressure_contr1: PressureController


