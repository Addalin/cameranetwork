# -*- coding: utf-8 -*-


__license__ = """
    This file is part of MDP.

    MDP is free software: you can redistribute it and/or modify
    it under the terms of the GNU General Public License as published by
    the Free Software Foundation, either version 3 of the License, or
    (at your option) any later version.

    MDP is distributed in the hope that it will be useful,
    but WITHOUT ANY WARRANTY; without even the implied warranty of
    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
    GNU General Public License for more details.

    You should have received a copy of the GNU General Public License
    along with MDP.  If not, see <http://www.gnu.org/licenses/>.
"""

__author__ = 'Guido Goldstein'
__email__ = 'gst-py@a-nugget.de'

import cPickle
import glob
import json
import logging
from MDP import *
import os
import time
from util import split_address
import zmq
from zmq.eventloop.zmqstream import ZMQStream
from zmq.eventloop.ioloop import PeriodicCallback


HB_INTERVAL = 2500 #: in milliseconds
HB_LIVENESS = 15    #: HBs to miss before connection counts as dead

class MDPBroker(object):

    """The MDP broker class.

    The broker routes messages from clients to appropriate workers based on the
    requested service.

    This base class defines the overall functionality and the API. Subclasses are
    ment to implement additional features (like logging).

    The broker uses ØMQ ROUTER sockets to deal with clients and workers. These sockets
    are wrapped in pyzmq streams to fit well into IOLoop.

    .. note::

      The workers will *always* be served by the `main_ep` endpoint.

      In a two-endpoint setup clients will be handled via the `opt_ep`
      endpoint.

    :param context:    the context to use for socket creation.
    :type context:     zmq.Context
    :param main_ep:    the primary endpoint for workers and clients.
    :type main_ep:     str
    :param opt_ep:     is an optional 2nd endpoint.
    :type opt_ep:      str
    :param service_q:   the class to be used for the service worker-queue.
    :type service_q:    class
    """

    CLIENT_PROTO = C_CLIENT  #: Client protocol identifier
    WORKER_PROTO = W_WORKER  #: Worker protocol identifier


    def __init__(self, context, main_ep, opt_ep=None, service_q=None):
        """Init MDPBroker instance.
        """
        
        if service_q is None:
            self.service_q = ServiceQueue
        else:
            self.service_q = service_q
        
        #
        # Setup the zmq socket.
        #
        socket = context.socket(zmq.ROUTER)
        socket.bind(main_ep)
        self.main_stream = ZMQStream(socket)
        self.main_stream.on_recv(self.on_message)
        if opt_ep:
            socket = context.socket(zmq.ROUTER)
            socket.bind(opt_ep)
            self.client_stream = ZMQStream(socket)
            self.client_stream.on_recv(self.on_message)
        else:
            self.client_stream = self.main_stream
            
        self._workers = {}
        
        #
        # services contain the service queue and the request queue
        #
        self._services = {}
        
        #
        # Mapping of worker commands and callbacks.
        #
        self._worker_cmds = {
            W_READY: self.on_ready,
            W_REPLY: self.on_reply,
            W_HEARTBEAT: self.on_heartbeat,
            W_DISCONNECT: self.on_disconnect,
        }
        
        #
        # 'Cleanup' timer for workers without heartbeat.
        #
        self.hb_check_timer = PeriodicCallback(self.on_timer, HB_INTERVAL)
        self.hb_check_timer.start()

    def register_worker(self, wid, service):
        """Register the worker id and add it to the given service.

        Does nothing if worker is already known.

        :param wid:    the worker id.
        :type wid:     str
        :param service:    the service name.
        :type service:     str

        :rtype: None
        """
        
        if wid in self._workers:
            logging.debug('Worker %s already registered' % service)            
            return
        
        logging.debug('Registering new worker %s' % service)                    

        self._workers[wid] = WorkerRep(self.WORKER_PROTO, wid, service, self.main_stream)
        
        if service in self._services:
            wq, wr = self._services[service]
            wq.put(wid)
        else:
            q = self.service_q()
            q.put(wid)
            self._services[service] = (q, [])

    def unregister_worker(self, wid):
        """Unregister the worker with the given id.

        If the worker id is not registered, nothing happens.

        Will stop all timers for the worker.

        :param wid:    the worker id.
        :type wid:     str

        :rtype: None
        """
        
        try:
            wrep = self._workers[wid]
        except KeyError:
            #
            # Not registered, ignore
            #
            return
        
        logging.debug('Unregistering worker %s' % wrep.service)                    

        wrep.shutdown()
        
        service = wrep.service
        if service in self._services:
            wq, wr = self._services[service]
            wq.remove(wid)
            
        del self._workers[wid]

    def disconnect(self, wid):
        """Send disconnect command and unregister worker.

        If the worker id is not registered, nothing happens.

        :param wid:    the worker id.
        :type wid:     str

        :rtype: None
        """
        
        try:
            wrep = self._workers[wid]
        except KeyError:
            #
            # Not registered, ignore
            #
            return
        
        logging.debug('Disconnecting worker %s' % wrep.service)                    

        to_send = [wid, self.WORKER_PROTO, W_DISCONNECT]
        self.main_stream.send_multipart(to_send)
        
        self.unregister_worker(wid)

    def client_response(self, rp, service, msg):
        """Package and send reply to client.

        :param rp:       return address stack
        :type rp:        list of str
        :param service:  name of service
        :type service:   str
        :param msg:      message parts
        :type msg:       list of str

        :rtype: None
        """

        logging.debug('Send reply to client from worker {}: {}'.format(service, msg))

        to_send = rp[:]
        to_send.extend([EMPTY_FRAME, self.CLIENT_PROTO, service])
        to_send.extend(msg)
        self.client_stream.send_multipart(to_send)

    def shutdown(self):
        """Shutdown broker.

        Will unregister all workers, stop all timers and ignore all further
        messages.

        .. warning:: The instance MUST not be used after :func:`shutdown` has been called.

        :rtype: None
        """
        
        logging.debug('Shutting down')
        
        if self.client_stream == self.main_stream:
            self.client_stream = None

        self.main_stream.on_recv(None)
        self.main_stream.socket.setsockopt(zmq.LINGER, 0)
        self.main_stream.socket.close()
        self.main_stream.close()
        self.main_stream = None
        
        if self.client_stream:
            self.client_stream.on_recv(None)
            self.client_stream.socket.setsockopt(zmq.LINGER, 0)
            self.client_stream.socket.close()
            self.client_stream.close()
            self.client_stream = None
            
        self._workers = {}
        self._services = {}

    def on_timer(self):
        """Method called on timer expiry.

        Checks which workers are dead and unregisters them.

        :rtype: None
        """
        
        #
        #  Remove 'dead' (not responding to heartbeats) workers.
        #
        for wrep in self._workers.values():
            if not wrep.is_alive():
                self.unregister_worker(wrep.id)

    def on_ready(self, rp, msg):
        """Process worker READY command.

        Registers the worker for a service.

        :param rp:  return address stack
        :type rp:   list of str
        :param msg: message parts
        :type msg:  list of str

        :rtype: None
        """
        
        ret_id = rp[0]
        logging.debug('Worker sent ready msg: {} ,{}'.format(rp, msg))
        self.register_worker(ret_id, msg[0])

    def on_reply(self, rp, msg):
        """Process worker REPLY command.

        Route the `msg` to the client given by the address(es) in front of `msg`.

        :param rp:  return address stack
        :type rp:   list of str
        :param msg: message parts
        :type msg:  list of str

        :rtype: None
        """
        
        ret_id = rp[0]
        wrep = self._workers.get(ret_id)
        
        if not wrep:
            #
            # worker not found, ignore message
            #
            return
        
        service = wrep.service
        
        try:
            wq, wr = self._services[service]
            
            #
            # Send response to client
            #            
            cp, msg = split_address(msg)
            self.client_response(cp, service, msg)

            #
            # make worker available again
            #
            wq.put(wrep.id)
            
            if wr:
                proto, rp, msg = wr.pop(0)
                self.on_client(proto, rp, msg)
        except KeyError:
            #
            # unknown service
            #
            self.disconnect(ret_id)

    def on_heartbeat(self, rp, msg):
        """Process worker HEARTBEAT command.

        :param rp:  return address stack
        :type rp:   list of str
        :param msg: message parts
        :type msg:  list of str

        :rtype: None
        """
        
        ret_id = rp[0]
        try:
            worker = self._workers[ret_id]
            if worker.is_alive():
                worker.on_heartbeat()
        except KeyError:
            #
            # Ignore HB for unknown worker
            #
            pass

    def on_disconnect(self, rp, msg):
        """Process worker DISCONNECT command.

        Unregisters the worker who sent this message.

        :param rp:  return address stack
        :type rp:   list of str
        :param msg: message parts
        :type msg:  list of str

        :rtype: None
        """
        
        wid = rp[0]
        self.unregister_worker(wid)

    def on_mmi(self, rp, service, msg):
        """Process MMI request.

        mmi.service is used for querying if a specific service is available.
        mmi.services is used for querying the list of services available.

        :param rp:      return address stack
        :type rp:       list of str
        :param service: the protocol id sent
        :type service:  str
        :param msg:     message parts
        :type msg:      list of str

        :rtype: None
        """
        
        if service == MMI_SERVICE:
            s = msg[0]
            ret = [UNKNOWN_SERVICE]
            
            for wr in self._workers.values():
                if s == wr.service:
                    ret = [KNOWN_SERVICE]
                    break
                
        elif service == MMI_SERVICES:
            #
            # Return list of services
            #
            ret = [wr.service for wr in self._workers.values()]
            
        elif service == MMI_TUNNELS:
            #
            # Read the tunnel files, and send back the network info.
            #
            tunnel_paths = glob.glob(os.path.expanduser("~/tunnel_port_*.txt"))
            tunnels_data = {}
            for path in tunnel_paths:
                filename = os.path.split(path)[-1]
                service_name = filename[13:-4]
                with open(path, 'r') as f:
                    tunnels_data[service_name] = json.load(f)
            ret = [cPickle.dumps(tunnels_data)]
        else:
            #
            # Unknown command.
            #
            ret = [UNKNOWN_COMMAND]

        self.client_response(rp, service, ret)

    def on_client(self, proto, rp, msg):
        """Method called on client message.

        Frame 0 of msg is the requested service.
        The remaining frames are the request to forward to the worker.

        .. note::

           If the service is unknown to the broker the message is
           ignored.

        .. note::

           If currently no worker is available for a known service,
           the message is queued for later delivery.

        If a worker is available for the requested service, the
        message is repackaged and sent to the worker. The worker in
        question is removed from the pool of available workers.

        If the service name starts with `mmi.`, the message is passed to
        the internal MMI_ handler.

        .. _MMI: http://rfc.zeromq.org/spec:8

        :param proto: the protocol id sent
        :type proto:  str
        :param rp:    return address stack
        :type rp:     list of str
        :param msg:   message parts
        :type msg:    list of str

        :rtype: None
        """
 
        service = msg.pop(0)

        if service.startswith(b'mmi.'):
            self.on_mmi(rp, service, msg)
            return

        try:
            wq, wr = self._services[service]
            wid = wq.get()
            
            if not wid:
                #
                # No worker ready. Queue message
                #
                msg.insert(0, service)
                wr.append((proto, rp, msg))
                return
            
            wrep = self._workers[wid]
            to_send = [wrep.id, EMPTY_FRAME, self.WORKER_PROTO, W_REQUEST]
            to_send.extend(rp)
            to_send.append(EMPTY_FRAME)
            to_send.extend(msg)
            self.main_stream.send_multipart(to_send)
            
        except KeyError:
            #
            # Unknwon service. Ignore request
            #
            logging.info('broker has no service "%s"' % service)

    def on_worker(self, proto, rp, msg):
        """Method called on worker message.

        Frame 0 of msg is the command id.
        The remaining frames depend on the command.

        This method determines the command sent by the worker and
        calls the appropriate method. If the command is unknown the
        message is ignored and a DISCONNECT is sent.

        :param proto: the protocol id sent
        :type proto:  str
        :param rp:  return address stack
        :type rp:   list of str
        :param msg: message parts
        :type msg:  list of str

        :rtype: None
        """
        
        cmd = msg.pop(0)
        if cmd in self._worker_cmds:
            fnc = self._worker_cmds[cmd]
            fnc(rp, msg)
        else:
            #
            # Ignore unknown command. Disconnect worker.
            #
            self.disconnect(rp[0])

    def on_message(self, msg):
        """Processes given message.

        Decides what kind of message it is -- client or worker -- and
        calls the appropriate method. If unknown, the message is
        ignored.

        :param msg: message parts
        :type msg:  list of str

        :rtype: None
        """
        
        rp, msg = split_address(msg)
        
        #
        # Dispatch on first frame after path
        #
        t = msg.pop(0)
        if t.startswith(b'MDPW'):
            logging.debug('Recieved message from worker {}: {}'.format(rp, msg))
            self.on_worker(t, rp, msg)
        elif t.startswith(b'MDPC'):
            logging.debug('Recieved message from client {}: {}'.format(rp, msg))
            self.on_client(t, rp, msg)
        else:
            logging.info('Broker unknown Protocol: "%s"' % t)


class WorkerRep(object):

    """Helper class to represent a worker in the broker.

    Instances of this class are used to track the state of the attached worker
    and carry the timers for incomming and outgoing heartbeats.

    :param proto:    the worker protocol id.
    :type wid:       str
    :param wid:      the worker id.
    :type wid:       str
    :param service:  service this worker serves
    :type service:   str
    :param stream:   the ZMQStream used to send messages
    :type stream:    ZMQStream
    """

    def __init__(self, proto, wid, service, stream):
        self.proto = proto
        self.id = wid
        self.service = service
        self.curr_liveness = HB_LIVENESS
        self.stream = stream
        
        self.hb_out_timer = PeriodicCallback(self.send_hb, HB_INTERVAL)
        self.hb_out_timer.start()

    def send_hb(self):
        """Called on every HB_INTERVAL.

        Decrements the current liveness by one.

        Sends heartbeat to worker.
        """
        
        self.curr_liveness -= 1
        logging.debug('Broker to Worker %s HB tick, current liveness: %d' % (self.service, self.curr_liveness))
        
        msg = [self.id, EMPTY_FRAME, self.proto, W_HEARTBEAT]
        self.stream.send_multipart(msg)

    def on_heartbeat(self):
        """Called when a heartbeat message from the worker was received.

        Sets current liveness to HB_LIVENESS.
        """
        
        logging.debug('Received HB from worker %s' % self.service)
        
        self.curr_liveness = HB_LIVENESS
        
    def is_alive(self):
        """Returns True when the worker is considered alive.
        """
        
        return self.curr_liveness > 0

    def shutdown(self):
        """Cleanup worker.

        Stops timer.
        """
        
        logging.debug('Shuting down worker %s' % self.service)
        
        self.hb_out_timer.stop()
        self.hb_out_timer = None
        self.stream = None


class ServiceQueue(object):

    """Class defining the Queue interface for workers for a service.

    The methods on this class are the only ones used by the broker.
    """

    def __init__(self):
        """Initialize queue instance.
        """
        self.q = []

    def __contains__(self, wid):
        """Check if given worker id is already in queue.

        :param wid:    the workers id
        :type wid:     str
        :rtype:        bool
        """
        
        return wid in self.q

    def __len__(self):
        return len(self.q)

    def remove(self, wid):
        try:
            self.q.remove(wid)
        except ValueError:
            pass

    def put(self, wid, *args, **kwargs):
        if wid not in self.q:
            self.q.append(wid)

    def get(self):
        if not self.q:
            return None
        
        return self.q.pop(0)
