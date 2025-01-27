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
"""
Setup the proxy server. This script should be run on the proxy server that connects the
clients to the servers.
"""
from CameraNetwork import mdp
import CameraNetwork
import CameraNetwork.global_settings as gs
import argparse
import logging
from zmq.eventloop.ioloop import IOLoop
import zmq


def main ():
    parser = argparse.ArgumentParser(
        description='Start the proxy application'
    )
    parser.add_argument(
        '--log_level',
        default='INFO',
        help='Set the log level (possible values: info, debug, ...)'
    )
    parser.add_argument(
        '--log_path',
        default='proxy_logs',
        help='Set the log folder'
    )
    args = parser.parse_args()

    gs.initPaths()

    #
    # Initialize the logger
    #
    CameraNetwork.initialize_logger(
        log_path=args.log_path,
        log_level=args.log_level,
        postfix='_proxy'
    )
    proxy_params = CameraNetwork.retrieve_proxy_parameters()

    #
    # Start the broker pattern.
    #
    context = zmq.Context()
    broker = mdp.MDPBroker(
        context,
        main_ep="tcp://*:{proxy_port}".format(**proxy_params),
        client_ep="tcp://*:{client_port}".format(**proxy_params),
        hb_ep="tcp://*:{hb_port}".format(**proxy_params),
    )
    IOLoop.instance().start()
    broker.shutdown()


if __name__ == '__main__':
    main()
