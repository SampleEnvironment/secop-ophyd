from abc import ABC, abstractmethod 
from typing import Any 
from secop_ophyd.SECoPDevices import SECoPMoveableDevice, SECoPCMDDevice, SECoPNodeDevice, SECoPReadableDevice 
from ophyd_async.core.signal import SignalR, SignalRW 


class Test_Mod_str(SECoPReadableDevice, ABC):
    status: SignalR
    group: SignalR
    description: SignalR
    implementation: SignalR
    interface_classes: SignalR
    features: SignalR
    value: SignalR
    pollinterval: SignalRW



class Test_Struct_of_arrays(SECoPReadableDevice, ABC):
    status: SignalR
    description: SignalR
    implementation: SignalR
    interface_classes: SignalR
    features: SignalR
    value: SignalR
    pollinterval: SignalRW
    writable_strct_of_arr: SignalRW



class Test_ND_arrays(SECoPReadableDevice, ABC):
    status: SignalR
    description: SignalR
    implementation: SignalR
    interface_classes: SignalR
    features: SignalR
    value: SignalR
    pollinterval: SignalRW
    arr3d: SignalRW



class OPYD_test_struct(SECoPMoveableDevice, ABC):
    status: SignalR
    group: SignalR
    description: SignalR
    implementation: SignalR
    interface_classes: SignalR
    features: SignalR
    value: SignalR
    pollinterval: SignalRW
    target: SignalRW
    nested_struct: SignalRW
    tuple_param: SignalRW
    tolerance: SignalRW
    stop_CMD: SECoPCMDDevice
    test_cmd_CMD: SECoPCMDDevice

    @abstractmethod 
    def test_cmd(self, arg: dict[str, Any], wait_for_idle: bool = False) -> int:
      """test description"""

class Ophyd_secop_frappy_demo(SECoPNodeDevice, ABC):
    equipment_id: SignalR
    firmware: SignalR
    version: SignalR
    description: SignalR
    interface: SignalR
    more: SignalR
    str_test: Test_Mod_str
    struct_of_arrays: Test_Struct_of_arrays
    nd_arr: Test_ND_arrays
    ophy_struct: OPYD_test_struct


