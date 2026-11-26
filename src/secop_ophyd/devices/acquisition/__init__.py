"""Devices for the SECoP acquisition interface classes: Acquisition,
AcquisitionController and AcquisitionChannel."""

from secop_ophyd.devices.acquisition.channel import (
    AcquisitionGoal,
    SECoPAcquisitionChannelDevice,
)
from secop_ophyd.devices.acquisition.control import (
    AcquisitionChannelType,
    SECoPAcquisitionControllerDevice,
    SECoPAcquisitionDevice,
    SECoPAcquisitionLogic,
    SECoPTriggerableDevice,
)

__all__ = [
    "AcquisitionChannelType",
    "AcquisitionGoal",
    "SECoPAcquisitionChannelDevice",
    "SECoPAcquisitionControllerDevice",
    "SECoPAcquisitionDevice",
    "SECoPAcquisitionLogic",
    "SECoPTriggerableDevice",
]
