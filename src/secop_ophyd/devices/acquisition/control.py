"""Devices that run acquisition cycles: Acquisition and AcquisitionController."""

import asyncio
import logging
from abc import abstractmethod
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from functools import cached_property
from logging import Logger
from typing import TYPE_CHECKING, Generic, TypeVar, cast

from bluesky.protocols import (
    DataKey,
    EventPageCollectable,
    PartialEventPage,
    Pausable,
    Stoppable,
)
from ophyd_async.core import (
    AsyncStatus,
    Device,
    DeviceMock,
    FlyableLogic,
    SignalR,
    StandardFlyable,
    StandardReadableFormat,
    TriggerableCommand,
)

from secop_ophyd.AsyncFrappyClient import AsyncSecopClient
from secop_ophyd.devices.acquisition.channel import (
    AcquisitionGoal,
    SECoPAcquisitionChannelDevice,
)
from secop_ophyd.devices.base import SECoPDevice
from secop_ophyd.devices.connector import SECoPDeviceConnector
from secop_ophyd.devices.readable import (
    SECoPReadableDevice,
    SECoPReadableMock,
    _is_failed,
    _wait_while_busy,
)

PrepareT_ = TypeVar("PrepareT_")


@dataclass
class SECoPAcquisitionLogic(FlyableLogic[PrepareT_, None]):
    """Fly logic of a SECoP acquisition: ``prepare`` sets the goals and runs
    the controller's 'prepare' command, ``kickoff`` sends 'go', and
    ``complete`` waits until the cycle has ended."""

    device: "_AcquisitionControl[PrepareT_]"

    async def on_prepare(self, value: PrepareT_) -> None:
        await self.device._prepare_cycle(value)

    async def on_kickoff(self, ctx: None) -> None:
        await self.device._start_cycle(buffer=True)

    async def on_complete(self, ctx: None) -> None:
        await self.device._finish_cycle()

    async def on_stage(self) -> None:
        for channel in self.device._acquisition_channels():
            channel._clear_buffers()

    async def on_unstage(self) -> None:
        await self.device._abort_cycle()


class _AcquisitionControl(
    StandardFlyable[PrepareT_, None],
    EventPageCollectable,
    Stoppable,
    Pausable,
    Generic[PrepareT_],
):
    """The control of acquisition cycles: the controller side of
    `SECoPAcquisitionControllerDevice` and `SECoPAcquisitionDevice`.

    One acquisition cycle runs from the 'go' command until 'status' leaves
    BUSY. It can be run

    - step by step: ``trigger()`` runs one cycle, and ``read()`` then gives
      the result of each channel: its 'value' at the end of the cycle, and
      the result of 'get_data' if it has that command.
    - as a flyer: ``prepare()`` sets the goals and runs the module's 'prepare'
      command (if it has one), ``kickoff()`` sends 'go' and ``complete()``
      waits until the cycle has ended.

    ``collect_pages()`` hands over the data of the cycles run as a flyer, one
    stream per channel, named after the channel:

    - for a channel with a 'get_data' command: the result of 'get_data'.
    - for one without: every update of 'value' the SEC node sent from
      ``kickoff()`` to ``complete()``, buffered by the channel.

    ``stop()`` ends a running cycle. A RunEngine pause stops a cycle that
    ``trigger()`` or ``kickoff()`` started: on resume, the RunEngine runs the
    plan again from its last checkpoint, which starts a new cycle.
    """

    go: TriggerableCommand

    if TYPE_CHECKING:
        # provided by SECoPDevice; not annotated at runtime, where the
        # DeviceFiller would take a Signal annotation for a declared child
        _client: AsyncSecopClient
        _module: str | None
        _logger: Logger
        status_0: SignalR

    # a cycle started by trigger()/kickoff() is running
    _acquiring = False
    # read by wait_for_idle()
    _success = True
    _stopped = False

    @abstractmethod
    def _acquisition_channels(self) -> list[SECoPAcquisitionChannelDevice]:
        """The channels of the acquisition."""

    @abstractmethod
    async def _apply_goals(self, value: PrepareT_) -> None:
        """Write the goals given to ``prepare()`` to the channels."""

    @cached_property
    def flyable_logic(self) -> SECoPAcquisitionLogic[PrepareT_]:
        return SECoPAcquisitionLogic(device=self)

    # --- SEC node access -------------------------------------------------

    @property
    def _node_client(self) -> AsyncSecopClient | None:
        """None in mock mode, where there is no SEC node to talk to."""
        return None if self._mock is not None else self._client

    def _has_command(self, command: str) -> bool:
        if self._node_client is None:
            # mock mode: commands are no-ops, as if the module had them all
            return True
        return command in self._client.modules[self._module]["commands"]

    async def _exec(self, command: str) -> None:
        if (client := self._node_client) is not None:
            await client.exec_command(self._module, command)

    async def _wait_while_busy(self, action: str) -> None:
        """Wait until the module has left BUSY, raise if it is in ERROR or
        DISABLED then."""
        stat_code = await _wait_while_busy(
            self.status_0, cast(str, self._module), None, self._node_client
        )
        if _is_failed(stat_code):
            self._success = False
            text = ""
            if isinstance(status_1 := getattr(self, "status_1", None), SignalR):
                text = f": {await status_1.get_value()}"
            self._logger.error(f"Module {self.name} --> ERROR/DISABLED{text}")
            raise RuntimeError(
                f"{action} of {self.name} failed: module entered ERROR/DISABLED "
                f"state{text}"
            )

    # --- acquisition cycle -----------------------------------------------

    async def _prepare_cycle(self, value: PrepareT_) -> None:
        await self._apply_goals(value)
        if self._has_command("prepare"):
            self._logger.info(f"Preparing {self.name}")
            await self._exec("prepare")
            await self._wait_while_busy("Preparing")

    async def _start_cycle(self, buffer: bool) -> None:
        """Send 'go'. With ``buffer``, the channels buffer the updates of
        their 'value' from now on."""
        self._logger.info(f"Starting an acquisition cycle of {self.name}")
        self._success = True
        self._stopped = False
        channels = self._acquisition_channels()
        for channel in channels:
            channel._cycle_started(buffer)
        self._acquiring = True
        try:
            await self.go.execute()
        except BaseException:
            self._acquiring = False
            for channel in channels:
                channel._stop_buffering()
            raise

    async def _finish_cycle(self) -> None:
        """Wait until the cycle has ended, then fetch the final values."""
        channels = self._acquisition_channels()
        try:
            await self._wait_while_busy("Acquisition")
        finally:
            self._acquiring = False
            for result in await asyncio.gather(
                *(channel._cycle_ended() for channel in channels),
                return_exceptions=True,
            ):
                if isinstance(result, BaseException):
                    raise result
        self._logger.info(f"Acquisition cycle of {self.name} done")

    async def _abort_cycle(self) -> None:
        """Stop a cycle that trigger()/kickoff() started and is still
        running."""
        for channel in self._acquisition_channels():
            channel._stop_buffering()
        if self._acquiring:
            await self.stop()

    def trigger(self) -> AsyncStatus:
        """Run one acquisition cycle: send 'go' and wait until it has ended."""
        return AsyncStatus(self._trigger())

    async def _trigger(self) -> None:
        await self._start_cycle(buffer=False)
        await self._finish_cycle()

    async def stop(self, *, success: bool = False) -> None:
        """End a running cycle with the module's 'stop' command."""
        self._stopped = True
        if not self._has_command("stop"):
            self._logger.warning(f"{self.name} has no 'stop' command")
            return
        self._logger.info(f"Stopping {self.name}")
        await self._exec("stop")

    async def pause(self) -> None:
        """RunEngine pause: stop a cycle that trigger()/kickoff() started. The
        RunEngine resumes by running the plan again from its last checkpoint,
        which starts a new cycle, so a cycle held over the pause would not be
        continued but mix data from before and after the pause."""
        if self._acquiring:
            await self.stop()

    async def resume(self) -> None:
        """RunEngine resume: nothing to do, the plan starts a new cycle."""

    # --- collecting ------------------------------------------------------

    async def describe_collect(self) -> dict[str, dict[str, DataKey]]:
        """One stream per channel, named after the channel."""
        return {
            channel.name: await channel._describe_stream()
            for channel in self._acquisition_channels()
        }

    async def collect_pages(self) -> AsyncIterator[PartialEventPage]:
        """The data collected since the last call: one event page per channel
        that has new data."""
        for channel in self._acquisition_channels():
            page = await channel._collect_page(cycle_running=self._acquiring)
            if page is not None:
                yield page


class SECoPAcquisitionDevice(
    _AcquisitionControl[AcquisitionGoal | None], SECoPAcquisitionChannelDevice
):
    """
    SECoP device for a module with the interface class "Acquisition": a data
    acquisition with a single channel, in one module. The legacy interface
    class "Triggerable" maps here too.

    It runs like a `SECoPAcquisitionControllerDevice` whose only channel is the
    module itself: ``trigger()`` runs one cycle, and ``read()`` then gives
    'value' (and the result of 'get_data'). ``prepare()`` takes an
    `AcquisitionGoal` (or None), and ``collect_pages()`` hands over one stream,
    named after the device.
    """

    def _acquisition_channels(self) -> list[SECoPAcquisitionChannelDevice]:
        return [self]

    async def _apply_goals(self, value: AcquisitionGoal | None) -> None:
        if value is not None:
            await self._apply_goal(value)


class AcquisitionChannelType:
    """Annotation for the channel devices of an acquisition controller: the
    role of the channel and the SECoP module it is."""

    def __init__(self, role: str, module: str) -> None:
        self.role = role
        self.module = module

    def __repr__(self) -> str:
        """Return repr suitable for code generation in annotations."""
        return f"ChannelT({self.role!r}, {self.module!r})"

    def __call__(self, parent: Device, child: Device):
        if isinstance(child, SECoPDevice):
            child.set_module(self.module)
            child._acquisition_role = self.role


class SECoPAcquisitionControllerDevice(
    _AcquisitionControl[Mapping[str, AcquisitionGoal] | None], SECoPDevice
):
    """
    SECoP device for a module with the interface class "AcquisitionController":
    the control of a data acquisition whose measured quantities are separate
    "AcquisitionChannel" modules.

    The channels listed in the module property 'acquisition_channels' are child
    devices of the controller, named by their role there (``control.t`` for
    the role "t"), and are no module devices of the node. ``channels`` maps
    each role to its channel device.

    ``trigger()`` runs one cycle, and ``read()`` then gives the result of every
    channel. ``prepare()`` takes the goals of the channels by role
    (``{"t": AcquisitionGoal(goal=10, enable=True)}``) or None, and
    ``collect_pages()`` hands over one stream per channel, named after the
    channel. See `_AcquisitionControl` for the details of a cycle.
    """

    hinted_signals: list[str] = []
    mandatory_parameters: list[str] = ["status"]

    # mocked: start IDLE
    _mock_class: type[DeviceMock] = SECoPReadableMock

    def __init__(
        self,
        sri: str = "",  # SECoP resource identifier host:port:optional[module]
        name: str = "",
        connector: SECoPDeviceConnector | None = None,
        loglevel=logging.INFO,
        logdir: str | None = None,
    ):
        self.status_0: SignalR

        super().__init__(
            sri=sri, name=name, connector=connector, loglevel=loglevel, logdir=logdir
        )

    # wait until the controller is no longer BUSY
    wait_for_idle = SECoPReadableDevice.wait_for_idle

    @property
    def channels(self) -> dict[str, SECoPAcquisitionChannelDevice]:
        """The channel devices by role."""
        return {
            child._acquisition_role: child
            for _, child in self.children()
            if isinstance(child, SECoPAcquisitionChannelDevice)
            and child._acquisition_role is not None
        }

    def _acquisition_channels(self) -> list[SECoPAcquisitionChannelDevice]:
        return list(self.channels.values())

    async def _apply_goals(self, value: Mapping[str, AcquisitionGoal] | None) -> None:
        if value is None:
            return
        channels = self.channels
        unknown = set(value) - set(channels)
        if unknown:
            raise ValueError(
                f"{self.name} has no channels with the roles {sorted(unknown)} "
                f"(roles: {sorted(channels)})"
            )
        await asyncio.gather(
            *(channels[role]._apply_goal(goal) for role, goal in value.items())
        )

    async def _assign_interface_formats(self):
        # the channels' readings are the controller's readings; skip channels
        # registered by an earlier (mock) connect
        channels = [
            channel
            for channel in self._acquisition_channels()
            if channel.read not in self._read_funcs
        ]
        self.add_readables(channels, StandardReadableFormat.CHILD)


# The interface class "Triggerable" was the working name of "Acquisition"
SECoPTriggerableDevice = SECoPAcquisitionDevice
