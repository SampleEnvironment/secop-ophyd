from frappy.client import SecopClient, CacheItem
from frappy.logging import logger
import asyncio
import time



import re
import json
import queue
import time
from collections import defaultdict
import threading
import weakref

from frappy.datatypes import TupleOf,ArrayOf,EnumType
from bluesky.protocols import Reading


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

from typing import TypeVar

T = TypeVar("T")

class UNREGISTER:
    """a magic value, used a returned value in a callback

    to indicate it has to be unregistered
    used to implement one shot callbacks
    """



#TODO better name
class SECoPReading():
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
        
    

class AsyncSecopClient:
    CALLBACK_NAMES = ('updateEvent', 'updateItem', 'descriptiveDataChange',
                      'nodeStateChange', 'unhandledMessage')
    online = False  # connected or reconnecting since a short time
    state = 'disconnected'  # further possible values: 'connecting', 'reconnecting', 'connected'
    log = None
    
    reconnect_timeout = 10
    _running = False
    _shutdown = False
    disconnect_time = 0  # time of last disconnect
    secop_version = ''
    descriptive_data = {}
    modules = {}
    _last_error = None
    
    
    def __init__(self,host,port,loop, log=Logger):
        # maps expected replies to [request, Event, is_error, result] until a response came
        # there can only be one entry per thread calling 'request'
        self.callbacks = {cbname: defaultdict(list) for cbname in self.CALLBACK_NAMES}
        # caches (module, parameter) = value, timestamp, readerror (internal names!)
        self.cache = {}
       
        self.active_requests = {}
        self.io = None
        self.txq = asyncio.Queue(30)   # queue for tx requests
        self.pending = asyncio.Queue(30)  # requests with colliding action + ident
        self.log = log
        self.uri = host +port
        self.nodename = host +port
        
        self._host = host
        self._port = port
        
        self.reader = None
        self.writer = None
        
        self.tx_task:asyncio.Task = None
        self.rx_task:asyncio.Task = None


        self.loop = loop

        self._disconn_lock = asyncio.Lock()
        self._disconnected = False

        
    @classmethod
    async def create(cls,host,port,loop, log=Logger):
        
        
        self = AsyncSecopClient(host,port,loop)
        
        await self.connect(1)
        
        return self
    async def send(self,message):
        assert message != b''
        self.writer.write(message+ b'\n')
        await self.writer.drain()
    async def connect(self, try_period=0):

        """establish connection

        if a <try_period> is given, repeat trying for the given time (sec)
        """
        if self.writer:
            return
        if self.online:
            self._set_state(True, 'reconnecting')
        else:
            self._set_state(False, 'connecting')
        deadline = time.time() + try_period
        while not self._shutdown:
            try:
                self.reader, self.writer = await asyncio.open_connection(self._host, self._port)
                
                await self.send(IDENTREQUEST.encode('utf-8'))
                               
                reply = await asyncio.wait_for(self.reader.readline(),10)
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
                self.rx_task = asyncio.create_task(self.receive_messages(),name='rx_task')
                self.tx_task = asyncio.create_task(self.transmit_messages(),name='tx_task')
                self._disconnected = False
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
    
    def _init_descriptive_data(self, data):
        """rebuild descriptive data"""
        changed_modules = None
        if json.dumps(data, sort_keys=True) != json.dumps(self.descriptive_data, sort_keys=True):
            if self.descriptive_data:
                changed_modules = set()
                modules = data.get('modules', {})
                for modname, moddesc in self.descriptive_data['modules'].items():
                    if json.dumps(moddesc, sort_keys=True) != json.dumps(modules.get(modname), sort_keys=True):
                        changed_modules.add(modname)
        self.descriptive_data = data
        modules = data['modules']
        self.modules = {}
        self.properties = {k: v for k, v in data.items() if k != 'modules'}
        self.identifier = {}  # map (module, parameter) -> identifier
        self.internal = {}  # map identifier -> (module, parameter)
        for modname, moddescr in modules.items():
            #  separate accessibles into command and parameters
            parameters = {}
            commands = {}
            accessibles = moddescr['accessibles']
            for aname, aentry in accessibles.items():
                iname = self.internalize_name(aname)
                datatype = get_datatype(aentry['datainfo'], iname)
                aentry = dict(aentry, datatype=datatype)
                ident = f'{modname}:{aname}'
                self.identifier[modname, iname] = ident
                self.internal[ident] = modname, iname
                if datatype.IS_COMMAND:
                    commands[iname] = aentry
                else:
                    parameters[iname] = aentry
            properties = {k: v for k, v in moddescr.items() if k != 'accessibles'}
            self.modules[modname] = {'accessibles': accessibles, 'parameters': parameters,
                                     'commands': commands, 'properties': properties}
        if changed_modules is not None:
            done = done_main = self.callback(None, 'descriptiveDataChange', None, self)
            for mname in changed_modules:
                if not self.callback(mname, 'descriptiveDataChange', mname, self):
                    if not done_main:
                        self.log.warning('descriptive data changed on module %r', mname)
                    done = True
            if not done:
                self.log.warning('descriptive data of %r changed', self.nodename)

    async def getParameter(self, module, parameter, trycache=False) -> SECoPReading:
        if trycache:
            cached = self.cache.get((module, parameter), None)
            if cached:
                return cached
        if self.online:
            await self.readParameter(module, parameter)
        return SECoPReading(self.cache[module, parameter])
    
    async def setParameter(self, module, parameter, value) -> SECoPReading:
        await self.connect()  # make sure we are connected
        datatype = self.modules[module]['parameters'][parameter]['datatype']
        value = datatype.export_value(value)
        await self.request(WRITEREQUEST, self.identifier[module, parameter], value)
        return SECoPReading(self.cache[module, parameter])
    
    async def execCommand(self, module, command, argument=None):
        await self.connect()  # make sure we are connected
        datatype = self.modules[module]['commands'][command]['datatype'].argument
        if datatype:
            argument = datatype.export_value(argument)
        else:
            if argument is not None:
                raise frappy.errors.WrongTypeError('command has no argument')
        # pylint: disable=unsubscriptable-object
        data, qualifiers = (await self.request(COMMANDREQUEST, self.identifier[module, command], argument))[2]
        datatype = self.modules[module]['commands'][command]['datatype'].result
        if datatype:
            data = datatype.import_value(data)
        return data, qualifiers
    
    async def readParameter(self, module, parameter)-> SECoPReading:
        """forced read over connection"""
        try:
            await self.request(READREQUEST, self.identifier[module, parameter])
        except frappy.errors.SECoPError:
            # error reply message is already stored as readerror in cache
            pass
        return SECoPReading(self.cache.get((module, parameter), None))
    
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
        entry = [request, asyncio.Event(), None]
        await self.txq.put(entry)
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
    
    async def receive_messages(self):
        noactivity = 0
        try:
            while self._running:
            
                # may raise ConnectionClosed
                reply = await self.reader.readline()
                
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
                    await self.txq.put(await self.pending.get())
        except asyncio.CancelledError:
            print('Received a request to cancel')
        except ConnectionClosed:
            print("conn closed")
        except Exception as e:
            print('rxthread ended with %r', e)
            self.log.error('rxthread ended with %r', e)
            

            
        
        await self.disconnect(False)
        if self._shutdown:
            return
        if self.activate:
            self.log.info('try to reconnect to %s', self.uri)
            self._reconnect()
        else:
            self.log.warning('%s disconnected', self.uri)
            self._set_state(False, 'disconnected')
            
    async def transmit_messages(self):
        try:
            while self._running:
                entry = await self.txq.get()
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
                    await self.pending.put(entry)
                else:
                    self.active_requests[key] = entry
                    line = encode_msg_frame(*request)
                    self.log.debug('TX: %r', line)
                    
                    self.writer.write(line)
                    await self.writer.drain()
        except asyncio.CancelledError:
            print('Received a request to cancel')
       
        await self.disconnect(False)
        
    async def disconnect(self, shutdown=True):
        async with self._disconn_lock:
            self._running = False
            if shutdown:
                self._shutdown = True
                self._set_state(False, 'shutdown')

            if self._disconnected:
                return 
        
            self.disconnect_time = time.time()
            try:  # make sure txq does not block
                while not self.txq.empty():
                   await self.txq.get(False)
            except Exception:
             pass
        
            if not self.tx_task.done() :
                await self.txq.put(None)  # shutdown marker
                self.tx_task.cancel()
                self.tx_task = None
                
            if not self.rx_task.done():
                self.rx_task.cancel()
                self.rx_task = None

            self.writer.close()
            await self.writer.wait_closed()
        
            self.writer = None
            self.reader = None

            self._disconnected = True
        
        
        #TODO
        # abort pending requests early
        #try:  # avoid race condition
        #    while self.active_requests:
        #        _, (_, event, _) = self.active_requests.popitem()
        #        event.set()
        #except KeyError:
        #    pass
        #try:
        #    while True:
        #        _, event, _ = self.pending.get(block=False)
        #        event.set()
        #except queue.Empty:
        #    pass
        
    def updateValue(self, module, param, value, timestamp, readerror):
        entry = CacheItem(value, timestamp, readerror,
                        self.modules[module]['parameters'][param]['datatype'])
        self.cache[(module, param)] = entry
        self.callback(None, 'updateItem', module, param, entry)
        self.callback(module, 'updateItem', module, param, entry)
        self.callback((module, param), 'updateItem', module, param, entry)
        # TODO: change clients to use updateItem instead of updateEvent
        self.callback(None, 'updateEvent', module, param, value, timestamp, readerror)
        self.callback(module, 'updateEvent', module, param, value, timestamp, readerror)
        self.callback((module, param), 'updateEvent', module, param, value, timestamp, readerror)

    def _set_state(self, online, state=None):
        # remark: reconnecting is treated as online
        self.online = online
        self.state = state or self.state
        self.callback(None, 'nodeStateChange', self.online, self.state)
        for mname in self.modules:
            self.callback(mname, 'nodeStateChange', self.online, self.state)
    def _unhandled_message(self, action, ident, data):
        if not self.callback(None, 'unhandledMessage', action, ident, data):
            self.log.warning('unhandled message: %s %s %r', action, ident, data)

    
    def register_callback(self, key, *args, **kwds):
        """register callback functions

        - key might be either:
            1) None: general callback (all callbacks)
            2) <module name>: callbacks related to a module (not called for 'unhandledMessage')
            3) (<module name>, <parameter name>): callback for specified parameter (only called for 'updateEvent')
        - all the following arguments are callback functions. The callback name may be
          given by the keyword, or, for non-keyworded arguments it is taken from the
          __name__ attribute of the function
        """
        for cbfunc in args:
            kwds[cbfunc.__name__] = cbfunc
        for cbname in self.CALLBACK_NAMES:
            cbfunc = kwds.pop(cbname, None)
            if not cbfunc:
                continue
            cbdict = self.callbacks[cbname]
            cbdict[key].append(cbfunc)

            # immediately call for some callback types
            if cbname == 'updateItem':
                if key is None:
                    for (mname, pname), data in self.cache.items():
                        cbfunc(mname, pname, data)
                else:
                    data = self.cache.get(key, None)
                    if data:
                        cbfunc(*key, data)  # case single parameter
                    else:  # case key = module
                        for (mname, pname), data in self.cache.items():
                            if mname == key:
                                cbfunc(mname, pname, data)
            elif cbname == 'updateEvent':
                if key is None:
                    for (mname, pname), data in self.cache.items():
                        cbfunc(mname, pname, *data)
                else:
                    data = self.cache.get(key, None)
                    if data:
                        cbfunc(*key, *data)  # case single parameter
                    else:  # case key = module
                        for (mname, pname), data in self.cache.items():
                            if mname == key:
                                cbfunc(mname, pname, *data)
            elif cbname == 'nodeStateChange':
                cbfunc(self.online, self.state)
        if kwds:
            raise TypeError(f"unknown callback: {', '.join(kwds)}")

    def unregister_callback(self, key, *args, **kwds):
        """unregister a callback

        for the arguments see register_callback
        """
        for cbfunc in args:
            kwds[cbfunc.__name__] = cbfunc
        for cbname, func in kwds.items():
            cblist = self.callbacks[cbname][key]
            if func in cblist:
                cblist.remove(func)
            if not cblist:
                self.callbacks[cbname].pop(key)

    def callback(self, key, cbname, *args):
        """perform callbacks

        key=None:
        key=<module name>: callbacks for specified module
        key=(<module name>, <parameter name): callbacks for specified parameter
        """
        cblist = self.callbacks[cbname].get(key, [])
        self.callbacks[cbname][key] = [cb for cb in cblist if cb(*args) is not UNREGISTER]
        return bool(cblist)
    

    PREDEFINED_NAMES = set(frappy.params.PREDEFINED_ACCESSIBLES)
    activate = True

    def internalize_name(self, name):
        """how to create internal names"""
        if name.startswith('_') and name[1:] not in self.PREDEFINED_NAMES:
            return name[1:]
        return name




