from frappy.client import SecopClient, CacheItem
from frappy.logging import logger
import asyncio
import time

import re
import json
import queue
import time
from collections import defaultdict
from threading import Event, RLock, current_thread

from frappy.datatypes import TupleOf,ArrayOf,EnumType
from bluesky.protocols import Reading
from ophyd.v2.core import T

import frappy.errors
import frappy.params
from frappy.datatypes import get_datatype
from frappy.lib import mkthread, formatExtendedStack
from frappy.lib.asynconn import AsynConn, ConnectionClosed
from frappy.protocol.interface import decode_msg, encode_msg_frame
from frappy.protocol.messages import COMMANDREQUEST, \
    DESCRIPTIONREQUEST, ENABLEEVENTSREQUEST, ERRORPREFIX, \
    EVENTREPLY, HEARTBEATREQUEST, IDENTPREFIX, IDENTREQUEST, \
    READREPLY, READREQUEST, REQUEST2REPLY, WRITEREPLY, WRITEREQUEST
VERSIONFMT= re.compile(r'^[^,]*?ISSE[^,]*,SECoP,')

# replies to be handled for cache
UPDATE_MESSAGES = {EVENTREPLY, READREPLY, WRITEREPLY, ERRORPREFIX + READREQUEST, ERRORPREFIX + EVENTREPLY}


from frappy.client import Logger

#TODO better name
class CacheReading():
    def __init__(self,entry:CacheItem) -> None:
        
        if isinstance(entry.value,TupleOf):
            self.value = list(entry.value)
        
        if isinstance(entry.value,EnumType):
            self.value = entry.value.value
        
        if isinstance(entry.value,ArrayOf):
            self.value = entry.value
        
        else:
            self.value = entry.value 
            
        self.timestamp = entry.timestamp

        self.readerror = entry.readerror
        
        
    def get_reading(self) -> Reading:
        return {'value':self.value,'timestamp':self.timestamp}
    def get_value(self) -> T:
        return self.value 
        
    

class Event_ts(asyncio.Event):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self._loop is None:
            self._loop = asyncio.get_event_loop()

    def set(self):
        self._loop.call_soon_threadsafe(super().set)

    def clear(self):
        self._loop.call_soon_threadsafe(super().clear)


class AsyncSecopClient(SecopClient):
    def __init__(self, uri, log=Logger):
        super().__init__(uri, log)
        self._ev_loop = asyncio.get_event_loop()
    
    
    async def connect(self, try_period=0):

        """establish connection

        if a <try_period> is given, repeat trying for the given time (sec)
        """
        with self._lock:
            if self.io:
                return
            if self.online:
                self._set_state(True, 'reconnecting')
            else:
                self._set_state(False, 'connecting')
            deadline = time.time() + try_period
            while not self._shutdown:
                try:
                    self.io = AsynConn(self.uri)  # timeout 1 sec
                    self.io.writeline(IDENTREQUEST.encode('utf-8'))
                    reply = self.io.readline(10)
                    if reply:
                        self.secop_version = reply.decode('utf-8')
                    else:
                        raise self.error_map('HardwareError')('no answer to %s' % IDENTREQUEST)

                    if not VERSIONFMT.match(self.secop_version):
                        raise self.error_map('HardwareError')('bad answer to %s: %r' %
                                                              (IDENTREQUEST, self.secop_version))
                    # inform that the other party still uses a legacy identifier
                    # see e.g. Frappy Bug #4659 (https://forge.frm2.tum.de/redmine/issues/4659)
                    if not self.secop_version.startswith(IDENTPREFIX):
                        self.log.warning('SEC-Node replied with legacy identify reply: %s'
                                         % self.secop_version)

                    # now its safe to do secop stuff
                    self._running = True
                    self._rxthread = mkthread(self.__rxthread)
                    self._txthread = mkthread(self.__txthread)
                    self.log.debug('connected to %s', self.uri)
                    # pylint: disable=unsubscriptable-object
                    rep =  await self.request(DESCRIPTIONREQUEST)

                    self._init_descriptive_data(rep[2])
                    self.nodename = self.properties.get('equipment_id', self.uri)
                    if self.activate:
                        await self.request(ENABLEEVENTSREQUEST)
                    self._set_state(True, 'connected')
                    break
                except Exception:
                    # print(formatExtendedTraceback())
                    if time.time() > deadline:
                        # stay online for now, if activated
                        self._set_state(self.online and self.activate)
                        raise
                    time.sleep(1)
            if not self._shutdown:
                self.log.info('%s ready', self.nodename)
                
    async def get_reply(self, entry):
        """wait for reply and return it"""
        event = entry[1]
        if not await event.wait():  # event
            raise TimeoutError('no response within 10s')
        if not entry[2]:  # reply
            raise ConnectionError('connection closed before reply')
        action, _, data = entry[2]  # pylint: disable=unpacking-non-sequence
        if action.startswith(ERRORPREFIX):
            raise frappy.errors.make_secop_error(*data[0:2])
        return entry[2]  # reply
        #get_reply_coro =  asyncio.to_thread(super().get_reply,entry)
        #asyncio.gather(get_reply_coro)
        
        #return entry[2]
        
    async def getParameter(self, module, parameter, trycache=False):
        if trycache:
            cached = self.cache.get((module, parameter), None)
            if cached:
                return cached
        if self.online:
            await self.readParameter(module, parameter)
        return CacheReading(self.cache[module, parameter])
    
    async def setParameter(self, module, parameter, value):
        await self.connect()  # make sure we are connected
        datatype = self.modules[module]['parameters'][parameter]['datatype']
        value = datatype.export_value(value)
        await self.request(WRITEREQUEST, self.identifier[module, parameter], value)
        return CacheReading(self.cache[module, parameter])
    
    async def execCommand(self, module, command, argument=None):
        await self.connect()  # make sure we are connected
        datatype = self.modules[module]['commands'][command]['datatype'].argument
        if datatype:
            argument = datatype.export_value(argument)
        else:
            if argument is not None:
                raise frappy.errors.WrongTypeError('command has no argument')
        # pylint: disable=unsubscriptable-object
        data, qualifiers = await self.request(COMMANDREQUEST, self.identifier[module, command], argument)[2]
        datatype = self.modules[module]['commands'][command]['datatype'].result
        if datatype:
            data = datatype.import_value(data)
        return data, qualifiers
    
    async def readParameter(self, module, parameter):
        """forced read over connection"""
        try:
            await self.request(READREQUEST, self.identifier[module, parameter])
        except frappy.errors.SECoPError:
            # error reply message is already stored as readerror in cache
            pass
        return CacheReading(self.cache.get((module, parameter), None))
    
    async def request(self, action, ident=None, data=None):
        """make a request

        and wait for reply
        """
        entry = await self.queue_request(action, ident, data)
        return await self.get_reply(entry)

    async def queue_request(self, action, ident=None, data=None):
        """make a request"""
        request = action, ident, data
        await self.connect()  # make sure we are connected
        # the last item is for the reply
        entry = [request, Event_ts(), None]
        self.txq.put(entry, timeout=3)
        return entry
    
    def _reconnect(self, connected_callback=None):
        while not self._shutdown:
            try:
                asyncio.run(self.connect)
                if connected_callback:
                    connected_callback()
                break
            except Exception as e:
                txt = str(e).split('\n', 1)[0]
                if txt != self._last_error:
                    self._last_error = txt
                    if 'join' in str(e):
                        raise
                    self.log.error(str(e))
                if time.time() > self.disconnect_time + self.reconnect_timeout:
                    if self.online:  # was recently connected
                        self.disconnect_time = 0
                        self.log.warning('can not reconnect to %s (%r)', self.nodename, e)
                        self.log.info('continue trying to reconnect')
                        # self.log.warning(formatExtendedTraceback())
                        self._set_state(False)
                    time.sleep(self.reconnect_timeout)
                else:
                    time.sleep(1)
        self._connthread = None
    
    def __rxthread(self):
        noactivity = 0
        try:
            while self._running:
                # may raise ConnectionClosed
                reply = self.io.readline()
                if reply is None:
                    noactivity += 1
                    if noactivity % 5 == 0:
                        # send ping to check if the connection is still alive
                        self.queue_request(HEARTBEATREQUEST, str(noactivity))
                    continue
                self.log.debug('RX: %r', reply)
                noactivity = 0
                action, ident, data = decode_msg(reply)
                if ident == '.':
                    ident = None
                if action in UPDATE_MESSAGES:
                    module_param = self.internal.get(ident, None)
                    if module_param is None and ':' not in (ident or ''):
                        # allow missing ':value'/':target'
                        if action == WRITEREPLY:
                            module_param = self.internal.get('%s:target' % ident, None)
                        else:
                            module_param = self.internal.get('%s:value' % ident, None)
                    if module_param is not None:
                        if action.startswith(ERRORPREFIX):
                            timestamp = data[2].get('t', None)
                            readerror = frappy.errors.make_secop_error(*data[0:2])
                            value = None
                        else:
                            timestamp = data[1].get('t', None)
                            value = data[0]
                            readerror = None
                        module, param = module_param
                        try:
                            self.updateValue(module, param, value, timestamp, readerror)
                        except KeyError:
                            pass  # ignore updates of unknown parameters
                        if action in (EVENTREPLY, ERRORPREFIX + EVENTREPLY):
                            continue
                try:
                    key = action, ident
                    entry = self.active_requests.pop(key)
                except KeyError:
                    if action.startswith(ERRORPREFIX):
                        try:
                            key = REQUEST2REPLY[action[len(ERRORPREFIX):]], ident
                        except KeyError:
                            key = None
                        entry = self.active_requests.pop(key, None)
                    else:
                        # this may be a response to the last unknown request
                        key = None
                        entry = self.active_requests.pop(key, None)
                if entry is None:
                    self._unhandled_message(action, ident, data)
                    continue
                entry[2] = action, ident, data
                entry[1].set()  # trigger event
                while not self.pending.empty():
                    # let the TX thread sort out which entry to treat
                    # this may have bad performance, but happens rarely
                    self.txq.put(self.pending.get())
        except ConnectionClosed:
            pass
        except Exception as e:
            self.log.error('rxthread ended with %r', e)
        self._rxthread = None
        self.disconnect(False)
        if self._shutdown:
            return
        if self.activate:
            self.log.info('try to reconnect to %s', self.uri)
            self._connthread = mkthread(self._reconnect)
        else:
            self.log.warning('%s disconnected', self.uri)
            self._set_state(False, 'disconnected')
            
    def __txthread(self):
        while self._running:
            entry = self.txq.get()
            if entry is None:
                break
            request = entry[0]
            reply_action = REQUEST2REPLY.get(request[0], None)
            if reply_action:
                key = (reply_action, request[1])  # action and identifier
            else:  # allow experimental unknown requests, but only one at a time
                key = None
            if key in self.active_requests:
                # store to requeue after the next reply was received
                self.pending.put(entry)
            else:
                self.active_requests[key] = entry
                line = encode_msg_frame(*request)
                self.log.debug('TX: %r', line)
                self.io.send(line)
        self._txthread = None
        self.disconnect(False)