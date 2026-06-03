import asyncio
import json
import time
from collections import defaultdict
from typing import Any, TypeVar

import frappy.params
from frappy.client import (
    UPDATE_MESSAGES,
    VERSIONFMT,
    CacheItem,
    Logger,
    NullLogger,
    ProxyClient,
)
from frappy.datatypes import get_datatype
from frappy.errors import HardwareError, SECoPError, WrongTypeError, make_secop_error
from frappy.protocol.interface import decode_msg, encode_msg_frame
from frappy.protocol.messages import (
    COMMANDREQUEST,
    DESCRIPTIONREQUEST,
    ENABLEEVENTSREQUEST,
    ERRORPREFIX,
    EVENTREPLY,
    HEARTBEATREQUEST,
    IDENTREQUEST,
    READREQUEST,
    REQUEST2REPLY,
    WRITEREQUEST,
)

T = TypeVar("T")


class AsyncSecopClient(ProxyClient):
    """Native asyncio SECoP client — no asyncio.to_thread() calls."""

    reconnect_timeout = 10
    PREDEFINED_NAMES = set(frappy.params.PREDEFINED_ACCESSIBLES)
    _max_error_count = 10

    def __init__(self, host: str, port: str, log=Logger) -> None:
        super().__init__()
        self.host = host
        self.port = port
        self.uri = f"{host}:{port}"
        self.nodename: str = self.uri
        self.log = log or NullLogger
        self.external = False
        self.conn_timestamp: float = 0.0

        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._rx_task: asyncio.Task | None = None
        self._reconnect_task: asyncio.Task | None = None
        self._shutdown: asyncio.Event = asyncio.Event()
        self._active_requests: dict[tuple, list] = defaultdict(list)

        self.secop_version: str = ""
        self.descriptive_data: dict = {}
        self.modules: dict = {}
        self.properties: dict = {}
        self.identifier: dict = {}
        self.internal: dict = {}
        self.disconnect_time: float = 0.0
        self._update_error_count: int = 0
        self._last_error: str | None = None

        self.register_callback(None, self.handleError)

    def handleError(self, exc):
        if self._update_error_count < self._max_error_count:
            self.log.error("%s", exc)
            self._update_error_count += 1
            if self._update_error_count == self._max_error_count:
                self.log.error("disabled reporting of further update errors")

    def _set_state(self, online, state=None):
        self.online = online
        if state is not None:
            self.state = state
        self.callback(None, "nodeStateChange", self.online, self.state)
        for mname in self.modules:
            self.callback(mname, "nodeStateChange", self.online, self.state)

    def internalize_name(self, name):
        if name.startswith("_") and name[1:] not in self.PREDEFINED_NAMES:
            return name[1:]
        return name

    def _init_descriptive_data(self, data):
        """Parse describing reply into modules/properties/identifier/internal."""
        changed_modules = None
        if json.dumps(data, sort_keys=True) != json.dumps(
            self.descriptive_data, sort_keys=True
        ):
            if self.descriptive_data:
                changed_modules = set()
                modules = data.get("modules", {})
                for modname, moddesc in self.descriptive_data["modules"].items():
                    if json.dumps(moddesc, sort_keys=True) != json.dumps(
                        modules.get(modname), sort_keys=True
                    ):
                        changed_modules.add(modname)
        self.descriptive_data = data
        modules = data["modules"]
        self.modules = {}
        self.properties = {k: v for k, v in data.items() if k != "modules"}
        self.identifier = {}
        self.internal = {}
        for modname, moddescr in modules.items():
            parameters = {}
            commands = {}
            accessibles = moddescr["accessibles"]
            for aname, aentry in accessibles.items():
                iname = self.internalize_name(aname)
                datatype = get_datatype(aentry["datainfo"], iname)
                aentry = dict(aentry, datatype=datatype)
                ident = f"{modname}:{aname}"
                self.identifier[modname, iname] = ident
                self.internal[ident] = modname, iname
                if datatype.IS_COMMAND:
                    commands[iname] = aentry
                else:
                    parameters[iname] = aentry
            props = {k: v for k, v in moddescr.items() if k != "accessibles"}
            self.modules[modname] = {
                "accessibles": accessibles,
                "parameters": parameters,
                "commands": commands,
                "properties": props,
            }
        if changed_modules is not None:
            done = done_main = self.callback(None, "descriptiveDataChange", None, self)
            for mname in changed_modules:
                if not self.callback(mname, "descriptiveDataChange", mname, self):
                    if not done_main:
                        self.log.warning("descriptive data changed on module %r", mname)
                    done = True
            if not done:
                self.log.warning("descriptive data of %r changed", self.nodename)

    def updateValue(self, module, param, value, timestamp, readerror):
        datatype = self.modules[module]["parameters"][param]["datatype"]
        if readerror:
            assert isinstance(readerror, Exception)
        else:
            value = datatype.import_value(value)
        entry = CacheItem(value, timestamp, readerror, datatype)
        self.cache[(module, param)] = entry
        self.callback(None, "updateItem", module, param, entry)
        self.callback(module, "updateItem", module, param, entry)
        self.callback((module, param), "updateItem", module, param, entry)
        super().updateValue(module, param, value, timestamp, readerror)

    def _handle_message(self, line: bytes):
        """Dispatch one decoded message — called from _rx_loop."""
        try:
            action, ident, data = decode_msg(line)
        except Exception as e:
            self.log.error("cannot decode %r: %s", line, e)
            return

        if ident == ".":
            ident = None

        now = time.time()

        if action in UPDATE_MESSAGES:
            module_param = self.internal.get(ident)
            if module_param is None and ":" not in (ident or ""):
                if action == "changed":
                    module_param = self.internal.get(f"{ident}:target")
                else:
                    module_param = self.internal.get(f"{ident}:value")
            if module_param is not None:
                if action.startswith(ERRORPREFIX):
                    timestamp = data[2].get("t", now)
                    readerror = make_secop_error(*data[0:2])
                    value = None
                else:
                    timestamp = data[1].get("t", now)
                    value = data[0]
                    readerror = None
                module, param = module_param
                self.updateValue(module, param, value, min(now, timestamp), readerror)
                if action in (EVENTREPLY, ERRORPREFIX + EVENTREPLY):
                    return

        key = (action, ident)
        lst = self._active_requests.get(key)
        if not lst and action.startswith(ERRORPREFIX):
            try:
                orig_reply = REQUEST2REPLY[action[len(ERRORPREFIX) :]]
                key = (orig_reply, ident)
                lst = self._active_requests.get(key)
            except KeyError:
                pass

        if lst:
            fut = lst.pop(0)
            if not lst:
                del self._active_requests[key]
            if not fut.done():
                if action.startswith(ERRORPREFIX):
                    fut.set_exception(make_secop_error(*data[0:2]))
                else:
                    fut.set_result((action, ident, data))
        else:
            if not self.callback(None, "unhandledMessage", action, ident, data):
                self.log.warning("unhandled message: %s %s %r", action, ident, data)

    async def _rx_loop(self):
        """Background task: read lines and dispatch messages."""
        assert self._reader is not None
        noactivity = 0
        try:
            while True:
                try:
                    line = await asyncio.wait_for(self._reader.readline(), timeout=1.0)
                except asyncio.TimeoutError:
                    noactivity += 1
                    if noactivity % 5 == 0 and self._writer:
                        self._writer.write(
                            encode_msg_frame(HEARTBEATREQUEST, str(noactivity))
                        )
                    continue
                if not line:
                    break
                noactivity = 0
                try:
                    self._handle_message(line.strip())
                except Exception as e:
                    e.args = (f"error handling SECoP message {line!r}: {e}",)
                    try:
                        self.callback(None, "handleError", e)
                    except Exception:
                        pass
        except asyncio.CancelledError:
            return
        except Exception as e:
            self.callback(None, "handleError", e)

        await self._do_disconnect(False)
        if not self._shutdown.is_set():
            self._reconnect_task = asyncio.create_task(self._reconnect())

    async def _do_disconnect(self, shutdown_flag: bool):
        """Close the transport and abort pending requests."""
        self.disconnect_time = time.time()
        writer, self._writer = self._writer, None
        self._reader = None
        if writer:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass
        err = ConnectionError("connection closed")
        for futures in list(self._active_requests.values()):
            for fut in futures:
                if not fut.done():
                    fut.set_exception(err)
        self._active_requests.clear()
        if shutdown_flag:
            self._shutdown.set()
            self._set_state(False, "shutdown")

    async def request(self, action, ident=None, data=None):
        """Send a request and await the matching reply."""
        if self._writer is None:
            raise ConnectionError("not connected")
        reply_action = REQUEST2REPLY.get(action)
        key = (reply_action, ident)
        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        self._active_requests[key].append(fut)
        self._writer.write(encode_msg_frame(action, ident, data))
        await self._writer.drain()
        try:
            return await asyncio.wait_for(fut, timeout=10.0)
        except asyncio.TimeoutError:
            lst = self._active_requests.get(key, [])
            if fut in lst:
                lst.remove(fut)
            raise TimeoutError(f"no response within 10s for {action} {ident}")

    async def connect(self, try_period=0):
        if self._writer is not None:
            return

        self._shutdown.clear()

        # Cancel any running rx_task before opening a new stream. Without
        # this, the old task's next readline() hits the same StreamReader as
        # the new handshake, asyncio raises RuntimeError, _rx_loop calls
        # _do_disconnect() (setting _reader=None and closing the stream), and
        # the new connection's readline() sees EOF -> secop_version=''.
        if self._rx_task and not self._rx_task.done():
            self._rx_task.cancel()
            try:
                await self._rx_task
            except asyncio.CancelledError:
                pass
        self._rx_task = None

        current = asyncio.current_task()
        if (
            self._reconnect_task
            and not self._reconnect_task.done()
            and self._reconnect_task is not current
        ):
            self._reconnect_task.cancel()
            try:
                await self._reconnect_task
            except asyncio.CancelledError:
                pass
            self._reconnect_task = None

        if self.online:
            self._set_state(True, "reconnecting")
        else:
            self._set_state(False, "connecting")

        deadline = time.time() + try_period

        while not self._shutdown.is_set():
            try:
                self._reader, self._writer = await asyncio.open_connection(
                    self.host, int(self.port)
                )
                self._writer.write((IDENTREQUEST + "\n").encode("utf-8"))
                await self._writer.drain()
                raw = await asyncio.wait_for(self._reader.readline(), timeout=10.0)
                self.secop_version = raw.strip().decode("utf-8")
                if not VERSIONFMT.match(self.secop_version):
                    raise HardwareError(
                        f"bad answer to {IDENTREQUEST}: {self.secop_version!r}"
                    )

                self._rx_task = asyncio.create_task(self._rx_loop())

                _, _, desc_data = await self.request(DESCRIPTIONREQUEST)
                self._init_descriptive_data(desc_data)
                self.nodename = self.properties.get("equipment_id") or self.uri

                self._set_state(True, "activating")
                await self.request(ENABLEEVENTSREQUEST)

                self._set_state(True, "connected")
                self.conn_timestamp = time.time()
                self.log.info("%s ready", self.nodename)
                return

            except asyncio.CancelledError:
                raise
            except Exception:
                if time.time() > deadline:
                    self._set_state(self.online)
                    raise
                await asyncio.sleep(1)

    async def disconnect(self, shutdown=True):
        if self._rx_task and not self._rx_task.done():
            self._rx_task.cancel()
            try:
                await self._rx_task
            except asyncio.CancelledError:
                pass
        self._rx_task = None

        if self._reconnect_task and not self._reconnect_task.done():
            self._reconnect_task.cancel()
            try:
                await self._reconnect_task
            except asyncio.CancelledError:
                pass
        self._reconnect_task = None

        await self._do_disconnect(shutdown)

        if not shutdown and not self._shutdown.is_set():
            self._reconnect_task = asyncio.create_task(self._reconnect())

    async def _reconnect(self):
        self._set_state(True, "reconnecting")
        while not self._shutdown.is_set():
            try:
                await self.connect()
                break
            except asyncio.CancelledError:
                return
            except Exception as e:
                txt = str(e).split("\n", 1)[0]
                if txt != self._last_error:
                    self._last_error = txt
                    self.log.error(str(e))
                if time.time() > self.disconnect_time + self.reconnect_timeout:
                    if self.online:
                        self.disconnect_time = 0
                        self.log.warning(
                            "can not reconnect to %s (%r)", self.nodename, e
                        )
                        self._set_state(False)
                    await asyncio.sleep(self.reconnect_timeout)
                else:
                    await asyncio.sleep(1)
        self._reconnect_task = None

    async def get_parameter(self, module, parameter, trycache=False) -> CacheItem:
        print(f"get parameter call trycache:{trycache}")
        if trycache:
            cached = self.cache.get((module, parameter))
            if cached:
                return cached
        if self.online:
            ident = self.identifier[module, parameter]
            try:
                await self.request(READREQUEST, ident)
            except SECoPError as e:
                result = self.cache[module, parameter]
                if e == result.readerror:
                    return result
                self.updateValue(module, parameter, None, time.time(), e)
        return self.cache[module, parameter]

    async def set_parameter(self, module, parameter, value) -> CacheItem:
        datatype = self.modules[module]["parameters"][parameter]["datatype"]
        value = datatype.export_value(value)
        await self.request(WRITEREQUEST, self.identifier[module, parameter], value)
        return self.cache[module, parameter]

    async def exec_command(self, module, command, argument=None) -> tuple[Any, dict]:
        datatype = self.modules[module]["commands"][command]["datatype"].argument
        if datatype:
            argument = datatype.export_value(argument)
        else:
            if argument is not None:
                raise WrongTypeError("command has no argument")
        _, _, (data, qualifiers) = await self.request(
            COMMANDREQUEST, self.identifier[module, command], argument
        )
        result_dtype = self.modules[module]["commands"][command]["datatype"].result
        if result_dtype:
            data = result_dtype.import_value(data)
        return data, qualifiers
