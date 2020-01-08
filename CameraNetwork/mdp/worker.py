##
## Copyright (C) 2017, Amit Aides, all rights reserved.
## 
## This file is part of Camera Network
## (see https://bitbucket.org/amitibo/cameranetwork_git).
## 
## Redistribution and use in source and binary forms, with or without modification,
## are permitted provided that the following conditions are met:
## 
## 1)  The software is provided under the terms of this license strictly for
##     academic, non-commercial, not-for-profit purposes.
## 2)  Redistributions of source code must retain the above copyright notice, this
##     list of conditions (license) and the following disclaimer.
## 3)  Redistributions in binary form must reproduce the above copyright notice,
##     this list of conditions (license) and the following disclaimer in the
##     documentation and/or other materials provided with the distribution.
## 4)  The name of the author may not be used to endorse or promote products derived
##     from this software without specific prior written permission.
## 5)  As this software depends on other libraries, the user must adhere to and keep
##     in place any licensing terms of those libraries.
## 6)  Any publications arising from the use of this software, including but not
##     limited to academic journal and conference publications, technical reports and
##     manuals, must cite the following works:
##     Dmitry Veikherman, Amit Aides, Yoav Y. Schechner and Aviad Levis, "Clouds in The Cloud" Proc. ACCV, pp. 659-674 (2014).
## 
## THIS SOFTWARE IS PROVIDED BY THE AUTHOR "AS IS" AND ANY EXPRESS OR IMPLIED
## WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF
## MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO
## EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT,
## INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING,
## BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE,
## DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF
## LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE
## OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF
## ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.##
# -*- coding: utf-8 -*-

"""Module containing worker functionality for the MDP implementation.

For the MDP specification see: http://rfc.zeromq.org/spec:7
"""

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


import sys
import time
from pprint import pprint

import zmq
from zmq.eventloop.zmqstream import ZMQStream
from zmq.eventloop.ioloop import IOLoop, DelayedCallback, PeriodicCallback

from util import split_address
from MDP import *

import logging


class MDPWorker(object):

    """Class for the MDP worker side.

    Thin encapsulation of a zmq.DEALER socket.
    Provides a send method with optional timeout parameter.

    Will use a timeout to indicate a broker failure.
    """

    _proto_version = W_WORKER

    # TODO: integrate that into API
    HB_INTERVAL = 2500 # in milliseconds
    HB_LIVENESS = 5    # HBs to miss before connection counts as dead
    RECONNECT_PERIOD = 5000

    def __init__(self, context, endpoint, hb_endpoint, service, endpoint_callback=None):
        """Initialize the MDPWorker.

        context is the zmq context to create the socket from.
        service is a byte-string with the service name.
        """
        self.context = context
        self.endpoint = endpoint
        self.hb_endpoint = hb_endpoint
        self.service = service
        self.endpoint_callback = endpoint_callback
        self.stream = None
        self.hb_stream = None
        self.ticker = None
        self._delayed_reconnect = None
        self._unique_id = ''
        self._create_stream()

    def _create_stream(self):
        """Helper to create the socket and the stream.
        """

        logging.debug('Worker creating stream')

        ioloop = IOLoop.instance()

        socket = self.context.socket(zmq.DEALER)
        self.stream = ZMQStream(socket, ioloop)
        self.stream.on_recv(self._on_message)
        self.stream.socket.setsockopt(zmq.LINGER, 0)
        self.stream.connect(self.endpoint)

        socket = self.context.socket(zmq.DEALER)
        self.hb_stream = ZMQStream(socket, ioloop)
        self.hb_stream.on_recv(self._on_message)
        self.hb_stream.socket.setsockopt(zmq.LINGER, 0)
        self.hb_stream.connect(self.hb_endpoint)

        self.ticker = PeriodicCallback(self._tick, self.HB_INTERVAL)
        self._send_ready()
        self.ticker.start()

    def _send_ready(self):
        """Helper method to prepare and send the workers READY message.
        """

        ready_msg = [EMPTY_FRAME, self._proto_version, W_READY, self.service]
        self.stream.send_multipart(ready_msg)
        self.curr_liveness = self.HB_LIVENESS

    def _tick(self):
        """Method called every HB_INTERVAL milliseconds.
        """

        self.curr_liveness -= 1
        logging.debug('Worker HB tick, current liveness: %d' % self.curr_liveness)

        self.send_hb()
        if self.curr_liveness >= 0:
            return

        #
        # Ouch, connection seems to be dead
        #
        logging.debug('Worker lost connection')
        self.shutdown()
        #
        # try to recreate the connection
        #
        self._delayed_reconnect = DelayedCallback(self._recreate_stream, self.RECONNECT_PERIOD)
        self._delayed_reconnect.start()

    def _recreate_stream(self):

        logging.debug('Worker trying to recreate stream')

        if self.endpoint_callback is not None:
            #
            # Check, maybe the ip of the proxy changed.
            #
            try:
                self.endpoint, self.hb_endpoint = self.endpoint_callback()
            except:
                #
                # Probably some problem in accessing the server.
                #
                self._delayed_reconnect = DelayedCallback(self._recreate_stream, self.RECONNECT_PERIOD)
                self._delayed_reconnect.start()
                return

        self._create_stream()

    def send_hb(self):
        """Construct and send HB message to broker.
        """

        msg = [EMPTY_FRAME, self._proto_version, W_HEARTBEAT, self._unique_id]

        self.hb_stream.send_multipart(msg)

    def shutdown(self):
        """Method to deactivate the worker connection completely.

        Will delete the stream and the underlying socket.
        """

        logging.debug('Shutdown of the worker')

        if self.ticker:
            logging.debug('Stopping the HB ticker')
            self.ticker.stop()
            self.ticker = None

        if not self.stream:
            return

        logging.debug('Closing the stream')

        self.stream.socket.close()
        self.stream.close()
        self.stream = None

        self.hb_stream.socket.close()
        self.hb_stream.close()
        self.hb_stream = None

        self.timed_out = False
        self.connected = False

    def reply(self, msg):
        """Send the given message.

        msg can either be a byte-string or a list of byte-strings.
        """

        #
        # prepare full message
        #
        to_send = self.envelope
        self.envelope = None

        if isinstance(msg, list):
            to_send.extend(msg)
        else:
            to_send.append(msg)

        self.stream.send_multipart(to_send)

    def _on_message(self, msg):
        """Helper method called on message receive.

        msg is a list w/ the message parts
        """

        logging.debug('Received message: {}'.format(msg))

        #
        # 1st part is empty
        #
        msg.pop(0)

        #
        # 2nd part is protocol version
        # TODO: version check
        #
        proto = msg.pop(0)

        #
        # 3rd part is message type
        #
        msg_type = msg.pop(0)

        #
        # XXX: hardcoded message types!
        # any message resets the liveness counter
        #
        self.curr_liveness = self.HB_LIVENESS

        if msg_type == W_DISCONNECT:
            #
            # Disconnect. Reconnection will be triggered by hb timer
            #
            self.curr_liveness = 0
        elif msg_type == W_READY:
            #
            # The message contains the unique id attached to the worker.
            #
            if len(msg) > 0:
                #
                # This above check is used for supporting older version of
                # the code.
                #
                self._unique_id = msg[0]
        elif msg_type == W_REQUEST:
            #
            # Request. Remaining parts are the user message
            #
            envelope, msg = split_address(msg)
            envelope.append(EMPTY_FRAME)
            envelope = [EMPTY_FRAME, self._proto_version, W_REPLY] + envelope
            self.envelope = envelope

            self.on_request(msg)
        else:
            #
            # invalid message
            # ignored
            #
            pass

    def on_request(self, msg):
        """Public method called when a request arrived.

        Must be overloaded!
        """

        raise NotImplementedError('on_request must be implemented by the subclass.')