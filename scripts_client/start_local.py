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
"""Start Cameras, Proxy and Client locally.

This script will run cameras, a proxy and a client locally on a
single computer. It is helpfull for replaying saved images.
Given a folder where the camera's data was saved, it will
start the right number of cameras and point their home
folder to the corresponding stored data.
"""

from __future__ import division, absolute_import, print_function
import argparse
import CameraNetwork
from glob import glob
import os
import subprocess as sbp


def main(base_path, debug_mode=False, local_proxy=False):
    camera_paths = sorted(glob(os.path.join(base_path, '*')))
    camera_paths = filter(lambda p: os.path.isdir(p), camera_paths)

    #
    # Start the proxy.
    #
    if local_proxy:
        proxy = sbp.Popen(['python'] + ['../scripts_proxy/start_proxy.py'])

    #
    # Start the client.
    #
    if not debug_mode:
        client = sbp.Popen(['python'] +
                           ['../scripts_client/camera_client.py', '--local'])

    #
    # Start all cameras.
    #
    servers = []
    for path in camera_paths:

        servers.append(sbp.Popen(['python'] +
                                 ['../scripts/start_server.py', '--local_path', path] +
                                 (["--local_proxy"] if local_proxy else [])))

    for server in servers:
        server.wait()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Start a local session.')
    parser.add_argument(
        '--debug_mode', '-d', action='store_true',
        help="Do not start the client. The client will be started from a debugger.")
    parser.add_argument(
        '--local_proxy', '-l', action='store_true',
        help="Start a local proxy.")
    parser.add_argument('base_path', help='Base path of cameras data.')
    args = parser.parse_args()

    main(args.base_path, args.debug_mode, args.local_proxy)
