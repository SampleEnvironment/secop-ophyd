from secop_ophyd.SECoPDevices import SECoP_Node_Device, SECoP_CMD_Device, SECoPMoveableDevice 
from ophyd_async.core.signal import SignalR, SignalRW 


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
    stop_CMD: SECoP_CMD_Device
    test_cmd_CMD: SECoP_CMD_Device


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
    stop_CMD: SECoP_CMD_Device


class Gas_dosing(SECoP_Node_Device):
    equipment_id: SignalR
    firmware: SignalR
    version: SignalR
    description: SignalR
    interface: SignalR
    massflow_contr1: MassflowController
    massflow_contr2: MassflowController
    massflow_contr3: MassflowController
    backpressure_contr1: PressureController


