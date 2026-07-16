import warnings

import pytest
from ophyd_async.core import Device, soft_signal_rw, walk_devices

from secop_ophyd.SECoPDevices import TILED_MAX_NAME_LENGTH, warn_on_long_device_names


class _ChildDevice(Device):
    def __init__(self, name: str = "") -> None:
        self.sig = soft_signal_rw(int)
        super().__init__(name=name)


def test_warns_when_device_name_too_long():
    device = _ChildDevice()
    device.set_name("x" * (TILED_MAX_NAME_LENGTH + 1))

    with pytest.warns(UserWarning, match="too long for tiled storage"):
        warn_on_long_device_names(walk_devices(device))


def test_no_warning_when_device_name_within_limit():
    device = _ChildDevice()
    device.set_name("short_name")

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        warn_on_long_device_names(walk_devices(device))
