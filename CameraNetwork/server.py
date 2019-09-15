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
from __future__ import division
from CameraNetwork.hg import Repository
import CameraNetwork.global_settings as gs
from CameraNetwork.internet import retrieve_proxy_parameters
from CameraNetwork.mdp import MDPWorker
from CameraNetwork.utils import DataObj
from CameraNetwork.utils import dict2buff
from CameraNetwork.utils import getImagesDF
from CameraNetwork.utils import handler
from CameraNetwork.utils import handler_no_answer
from CameraNetwork.utils import identify_server
from CameraNetwork.utils import IOLoop
from CameraNetwork.utils import load_camera_data
from CameraNetwork.utils import name_time
from CameraNetwork.utils import RestartException
from CameraNetwork.utils import save_camera_data
from CameraNetwork.utils import setup_reverse_ssh_tunnel
from CameraNetwork.utils import sync_time
from CameraNetwork.utils import sun_direction
import cPickle
from dateutil import parser as dtparser
from datetime import datetime
try:
    import futures
except:
    #
    # Support also python 2.7
    #
    from concurrent import futures
import glob
from PIL import Image
from PIL import ImageDraw
import json
import logging
import math
import numpy as np
import os
import pandas as pd
import pkg_resources
import Queue
import re
import scipy.io as sio
import StringIO
import subprocess
import sys
import thread
import time
from tornado import gen
from tornado.concurrent import Future
from tornado.concurrent import run_on_executor
import traceback
import zmq


def restart_program():
    """Restarts the current program.

    https://www.daniweb.com/programming/software-development/code/260268/restart-your-python-program
    """

    logging.info("Performing restart")
    logging.shutdown()
    python = sys.executable
    os.execl(python, python, *sys.argv)


def upload_thread(upload_queue):
    """A thread for uploading captured images."""

    while True:
        #
        # Wait for a new upload
        #
        capture_path, upload_path = upload_queue.get()

        #
        # Check if time to quit
        #
        if capture_path is None:
            logging.info('Upload thread stopped')
            break

        cmd_upload = gs.UPLOAD_CMD.format(capture_path=capture_path, upload_path=upload_path)
        logging.info('Uploading frame %s' % upload_path)

        #
        # Upload image
        #
        upload_log = subprocess.Popen(cmd_upload, shell=True).communicate()

        logging.debug(str(upload_log))

        #
        # Remove the frame from folder.
        #
        #logging.info('Removing frame %s' % capture_path)
        #os.remove(capture_path)


class Server(MDPWorker):
    """The server of the network camera"""

    HB_LIVENESS = 10

    #
    # Thread pull
    #
    executor = futures.ThreadPoolExecutor(4)

    def __init__(self, controller, identity=None, offline=False, local_path=None, local_proxy=False):
        """
        Class that encapsulates the camera action.

        parameters:
        ===========
        """

        self._local_mode = local_path is not None
        self._local_proxy = local_proxy
        gs.initPaths(local_path)

        #
        # Sync the clock.
        # Not done in local mode.
        #
        if local_path is None:
            logging.info('Syncing time')
            sync_time()

        #
        # Verify/Create the path where captured data is stored
        #
        if not os.path.isdir(gs.CAPTURE_PATH):
            os.makedirs(gs.CAPTURE_PATH)

        #
        # Get the connection data from the settings server.
        #
        self.camera_settings, self.capture_settings = load_camera_data(
            gs.GENERAL_SETTINGS_PATH, gs.CAPTURE_SETTINGS_PATH
        )
        self.update_proxy_params()
        self.ctx = zmq.Context()

        #
        # Set up the MDPWorker
        #
        if identity == None:
            identity = str(self.camera_settings[gs.CAMERA_IDENTITY])
            if self._local_mode:
                identity = os.path.split(local_path)[1] + "L"

        super(Server, self).__init__(
            context=self.ctx,
            endpoint="tcp://{ip}:{proxy_port}".format(**self.proxy_params),
            hb_endpoint="tcp://{ip}:{hb_port}".format(**self.proxy_params),
            service=identity,
            endpoint_callback=self.endpoint_callback
        )

        self._offline = offline
        self.capture_state = False

        #
        # Start the upload thread.
        # Note:
        # I am using a separate thread for uploading in order to use an
        # upload queue. We had a problem where slow upload caused many upload
        # processes to start together and cause jamming in communication.
        #
        self.upload_queue = Queue.Queue()
        if not self._local_mode:
            self.upload_thread = thread.start_new_thread(upload_thread,
                                                         (self.upload_queue,))

        #
        # Tunneling related parameters.
        #
        self.tunnel_process = None
        self.tunnel_port = None

        #
        # link to the controller
        #
        self._controller = controller

    def __del__(self):

        #
        # Stop the upload queue.
        #
        logging.info('Stopping the upload thread')
        self.upload_queue.put((None, None))

        #
        # Stop the IOLoop
        #
        logging.info('Stopping the ioloop')
        IOLoop.instance().stop()

        #
        # Close the tunnel.
        #
        if self.tunnel_process is not None:
            try:
                self.tunnel_process.kill()
            except:
                pass

        #
        # Shutdown the worker
        #
        logging.info('Shutting down worker.')
        self.shutdown()
        self.ctx.term(0)

    def start(self, start_capture=False ):
        """Start the server activity."""

        if not self._offline:
            #
            # Start the different timers.
            # ---------------------------
            #
            # 1) Start the sunshader timer. This timer handles scanning for the
            #    position of the sun.
            #
            IOLoop.current().spawn_callback(self.sunshader_timer)

            #
            # 2) Setup a timer to activate the sprinklers.
            #
            IOLoop.current().spawn_callback(self.setup_sprinkler_timer)

            #
            # 3) Start the capture loop.
            #
            # If the start_loop is set, start the capture loop.
            #
            if start_capture or self.capture_settings[gs.START_LOOP]:
                self.capture_state = True

                IOLoop.current().spawn_callback(self.loop_timer)
        else:
            #
            # Start in offline mode.
            #
            logging.info("Starting in offline mode.")

    @gen.coroutine
    def setup_sprinkler_timer(self):
        """Setup the timer of the sprinkler.

        The sprinkler should clean the lens once a day in the morning.
        """

        logging.info("Setup the sprinkler timer.")

        #
        # Check if the sprinkler was used today.
        #
        try:
            if os.path.exists(gs.SPRINKLER_LAST_DAY_FILE):
                with open(gs.SPRINKLER_LAST_DAY_FILE, "rb") as f:
                    last_day = cPickle.load(f)

                if last_day == datetime.now().date():
                    #
                    # The sprinkler was already used today.
                    #
                    # Setup a timer for tommorow.
                    #
                    t = datetime.now()
                    IOLoop.current().call_at(
                        time.mktime(
                            datetime(
                                t.year, t.month, t.day+1, gs.SPRINKLER_TIMER_HOUR
                                ).timetuple()
                            ),
                        self.sprinkler_callback
                    )

                    logging.debug("Already sprinkled today. No need to sprinkle.")

                    return

        except Exception as e:
            logging.error('Error while checking last sprinkler tim:\n{}'.format(
                traceback.format_exc()))
            if os.path.exists(gs.SPRINKLER_LAST_DAY_FILE):
                os.remove(gs.SPRINKLER_LAST_DAY_FILE)

        #
        # The sprinkler was not used to, add a callback to use it in 1 min
        # (to let the sunshader finish its scan before the lens get wet).
        #
        IOLoop.current().call_later(60, self.sprinkler_callback)

    @gen.coroutine
    def sprinkler_callback(self):
        """Activate the sprinklers to clean the lens in the morning."""

        logging.info("Morning sprinkled happiness.")

        #
        # Setup a timer for tommorow.
        #
        t = datetime.now()
        IOLoop.current().call_at(
            time.mktime(
                datetime(
                    t.year, t.month, t.day+1, gs.SPRINKLER_TIMER_HOUR
                    ).timetuple()
                ),
            self.sprinkler_callback
        )

        #
        # Activate the sprinklers.
        #
        yield self.push_cmd(
            gs.SPRINKLER_CMD,
            priority=50,
            period=gs.SPRINKLER_PERIOD
        )

        #
        # Store that the sprinklers were used today.
        #
        with open(gs.SPRINKLER_LAST_DAY_FILE, "wb") as f:
            cPickle.dump(datetime.now().date(), f)

    def update_proxy_params(self):
        """Update the proxy params

        This function helps handling the case that the ip of the proxy changes.

        Note:
            Currently the IP is hardcoded, so there is not really much to
            update.
        """

        self.proxy_params = json.loads(gs.DEFAULT_PROXY_PARAMS)
        logging.info('Got proxy params: {params}'.format(params=self.proxy_params))

    def endpoint_callback(self):
        """This function is called by the MDP worker each time the connection is lost"""

        self.update_proxy_params()

        return ("tcp://{ip}:{proxy_port}".format(**self.proxy_params),
                "tcp://{ip}:{hb_port}".format(**self.proxy_params))

    ###########################################################
    # Low level message handling.
    ###########################################################

    def on_request(self, msg):
        """Callback for receiving a message."""

        IOLoop.current().spawn_callback(self._on_request, msg)

    @gen.coroutine
    def _on_request(self, msg):
        """Callback for receiving a message."""

        #
        # Get message payload.
        #
        cmd, args, kwds = cPickle.loads(msg[0])
        logging.debug('Received msg: {}'.format((cmd, args, kwds)))

        cb = getattr(self, 'handle_{}'.format(cmd), None)
        if cb is None:
            logging.debug('Unknown cmd: {}'.format(cmd))
            status, answer_args, answer_kwds = gs.MSG_STATUS_ERROR, ['Unknown cmd: {}'.format(cmd)], {}
        else:
            try:
                answer = yield cb(*args, **kwds)
                if answer is None:
                    answer = (), {}

                #
                # Note:
                # I comment this logging as in the case of array commands, the
                # log is very big.
                #
                #logging.debug('Callback answer: {}'.format(answer))

                status = gs.MSG_STATUS_OK
                answer_args, answer_kwds = answer
            except RestartException:
                #
                # Restart program.
                #
                logging.info("Server received RestartException.")
                restart_program()
            except Exception:
                status = gs.MSG_STATUS_ERROR
                answer_args, answer_kwds = \
                    ['Calling the cmd handler caused an error:\n{}'.format(traceback.format_exc())], \
                    {}

        #
        # Prepare the basic reply (includes the command)
        #
        msg_data_out = (
            status,
            cmd,
            answer_args,
            answer_kwds
        )

        msg = [cPickle.dumps(msg_data_out)]
        logging.debug('Answer status: {}'.format(status))

        self.reply(msg)

    def push_cmd(self, cmd, priority=100, **kwds):
        """Push a command into the command queue."""

        future = Future()
        self._controller.cmd_queue.put((priority, (future, cmd, kwds)))
        return future

    ###########################################################
    # Timers
    ###########################################################

    @gen.coroutine
    def sunshader_timer(self):
        while True:
            nxt = gen.sleep(gs.SUNSHADER_PERIOD)

            try:
                #
                # Select sunshader settings according day night.
                # The day/night status is determined by the altitude of the sun.
                #
                sun_alt, _ = sun_direction(
                    longitude=self.camera_settings[gs.CAMERA_LONGITUDE],
                    latitude=self.camera_settings[gs.CAMERA_LATITUDE],
                    altitude=self.camera_settings[gs.CAMERA_ALTITUDE],
                )

                if sun_alt > gs.SUN_ALTITUDE_SUNSHADER_THRESH:
                    #
                    # Day: Activate sunshader
                    #
                    yield self.push_cmd(
                        gs.SUNSHADER_UPDATE_CMD,
                        sunshader_min=self.camera_settings[gs.SUNSHADER_MIN_ANGLE],
                        sunshader_max=self.camera_settings[gs.SUNSHADER_MAX_ANGLE]
                    )
                else:
                    #
                    # Night: Reset sunshader. Measure moon.
                    #
                    yield self.push_cmd(
                        gs.MOON_CMD,
                        sunshader_min=self.camera_settings[gs.SUNSHADER_MIN_ANGLE]
                    )
            except Exception as e:
                logging.error('Error while processing the sunshder timer:\n{}'.format(
                    traceback.format_exc()))
                raise

            yield nxt

    @gen.coroutine
    def loop_timer(self):
        while self.capture_state:
            #
            # Store time here so that hopefully it will be as synchronized
            # as possible.
            #
            name_time=datetime.utcnow()

            #
            # Select capture settings according day night.
            # The day/night status is determined by the altitude of the sun.
            #
            sun_alt, _ = sun_direction(
                longitude=self.camera_settings[gs.CAMERA_LONGITUDE],
                latitude=self.camera_settings[gs.CAMERA_LATITUDE],
                altitude=self.camera_settings[gs.CAMERA_ALTITUDE],
            )

            if sun_alt > gs.SUN_ALTITUDE_DAY_THRESH:
                #
                # Day shot
                #
                capture_settings = self.capture_settings[gs.DAY_SETTINGS]

                #
                # Exposure during the day is set according to the sun altitude
                #
                sin_a = math.sin(max(sun_alt, gs.SUN_ALTITUDE_EXPOSURE_THRESH))
                exposure = int(capture_settings['exposure_us'] / sin_a)
            else:
                #
                # Night shot
                #
                capture_settings = self.capture_settings[gs.NIGHT_SETTINGS]

                #
                # Exposure during the night is hardcoded.
                #
                exposure = capture_settings['exposure_us']

            #
            # Create next time timer.
            #
            capture_delay = max(1, capture_settings[gs.LOOP_DELAY])

            next_capture_time = (
                int(time.time() / capture_delay) + 1
                ) * capture_delay - time.time()

            nxt = gen.sleep(next_capture_time)

            logging.debug('Capturing using exposure: {} uS'.format(exposure))

            #
            # Create the image data object.
            #
            img_data = DataObj(
                longitude=self.camera_settings[gs.CAMERA_LONGITUDE],
                latitude=self.camera_settings[gs.CAMERA_LATITUDE],
                altitude=self.camera_settings[gs.CAMERA_ALTITUDE],
                name_time=name_time
            )

            #
            # Schedule img/s capture.
            #
            jpg_names, mat_names, data_names = \
                yield self.push_cmd(
                    gs.LOOP_CMD,
                    priority=1,
                    capture_settings=dict(
                        exposure_us=exposure,
                        gain_db=capture_settings['gain_db'],
                        gain_boost=capture_settings['gain_boost'],
                        color_mode=capture_settings['color_mode'],
                        ),
                    frames_num=capture_settings[gs.FRAMES_NUM],
                    hdr_mode=capture_settings[gs.HDR_MODE],
                    img_data=img_data
                )

            #
            # Setup upload of files to dropbox
            #
            upload_list = []
            if self.capture_settings[gs.UPLOAD_JPG_FILE]:
                upload_list.extend(jpg_names)
            if self.capture_settings[gs.UPLOAD_MAT_FILE]:
                upload_list.extend(mat_names)
            upload_list.extend(data_names)

            operation = gs.DROPBOX_LOOP_PATH
            subfolder = img_data.name_time.strftime("%Y_%m_%d")

            for filepath in upload_list:
                upload_path = gs.UPLOAD_PATH.format(
                    operation=operation,
                    camera_identity=self.camera_settings[gs.CAMERA_IDENTITY],
                    subfolder=subfolder,
                    filename=os.path.split(filepath)[1]
                )
                logging.debug('Putting file %s in queue' % filepath)
                self.upload_queue.put((filepath, upload_path))

            yield nxt


    ###########################################################
    # Message handlers
    ###########################################################

    @gen.coroutine
    def handle_confirm(self):
        pass

    @gen.coroutine
    def handle_halt(self):
        """Halt all capture"""

        self.capture_state = False
        self.capture_settings[gs.START_LOOP] = False
        save_camera_data(gs.GENERAL_SETTINGS_PATH, gs.CAPTURE_SETTINGS_PATH,
            None, self.capture_settings)

    @gen.coroutine
    def handle_status(self):
        """Handle get status command.

        Returns general status log on the camera.
        """

        #
        # Get memory status
        #
        p = subprocess.Popen(
            "df -h", stderr=subprocess.PIPE, stdout=subprocess.PIPE, shell=True
        )
        mem_result = p.communicate()

        #
        # Get git version.
        #
        p = subprocess.Popen(
            "cd ~/code/cameranetwork;git log -n 1 HEAD --decorate",
            stderr=subprocess.PIPE, stdout=subprocess.PIPE, shell=True
        )
        git_result = p.communicate()

        raise gen.Return(
            (
                (),
                {
                    'git_result': git_result,
                    'memory_result': mem_result
                }
            )
        )

    @gen.coroutine
    def handle_loop(self):
        """Start capture"""

        #
        # Check the current state of the camera.
        #
        if self.capture_state:
            raise Exception('Camera is already capturing')

        self.capture_state = True
        self.capture_settings[gs.START_LOOP] = True
        save_camera_data(gs.GENERAL_SETTINGS_PATH, gs.CAPTURE_SETTINGS_PATH,
            None, self.capture_settings)

        IOLoop.current().spawn_callback(self.loop_timer)

    @gen.coroutine
    def handle_get_settings(self):
        """Get camera settings"""

        raise gen.Return(
            (
                (),
                {
                    'camera_settings': self.camera_settings,
                    'capture_settings': self.capture_settings
                }
            )
        )

    @gen.coroutine
    def handle_set_settings(self, camera_settings, capture_settings):
        """Set camera settings

        Args:
            camera_settings (dict): Dictionary of general camera settings. If
                None, the settings will not be changed.
            capture_settings (dict): Dictionary of capture settings. If None,
                the settings will not be changed.
        """

        #
        # Copy the input settings camera data without the CAMERA_IDENTITY field.
        #
        if camera_settings is not None:
            del camera_settings[gs.CAMERA_IDENTITY]
            self.camera_settings.update(camera_settings.copy())

        if capture_settings is not None:
            self.capture_settings.update(capture_settings.copy())

        #
        # Save the updated settings
        #
        save_camera_data(gs.GENERAL_SETTINGS_PATH, gs.CAPTURE_SETTINGS_PATH,
            self.camera_settings, self.capture_settings)

    @gen.coroutine
    def handle_update(self, rev):
        """Pull and update the code version"""

        repo = Repository(
            os.path.abspath(pkg_resources.resource_filename(__name__, '')))

        pull_msg = yield repo.pull()
        update_msg = yield repo.update('-r', rev)

        ret_msg = 'pull:\n{pull}\nupdate:\n{update}'.format(pull=pull_msg, update=update_msg)

        raise gen.Return(((ret_msg,), {}))

    @run_on_executor
    def handle_thumbnails(
            self,
            query_date,
            time_period="30T",
            resolution=201,
            hdr_index="2",
            jpeg=True):
        """Download thumbnails of a specific days

        Args:
            query_date (datetime object or string): The date for which
                to query for images. If string is give, dateutil.parser
                will be used for guessing the right date.
            time_period (string, optional): The sampling period. Defaults
                to every half an hour.
            resolution (int, optional): The resolution of the thumbnail.
            hdr_index (int, optional): The hdr index of the thumbnail.
            jpeg (bool/int): Whether to return an array or compressed JPEG. If int,
                then it will be used as quality of the JPEG.

        Return:
            List of thumbnails, dataframe describing the thumbnails.
        """

        hdr_index = str(hdr_index)

        #
        # Form file names.
        #
        if type(query_date) == str:
            query_date = dtparser.parse(query_date)

        def custom_resampler(array_like):
            if array_like.shape[0] == 0:
                return None
            return array_like.iloc[-1]

        query_df = getImagesDF(query_date, force=True)
        thumbs_df = query_df.xs(hdr_index, level="hdr").resample(
            rule=time_period,
            label="right").apply(custom_resampler).dropna()

        new_inds, new_rows = [], []
        thumbnails = []
        for ind, row in thumbs_df.iterrows():
            try:
                _, thumb_array = self._controller.seekImageArray(
                    query_df,
                    seek_time=ind,
                    hdr_index=hdr_index,
                    normalize=True,
                    resolution=resolution,
                    jpeg=True,
                    camera_settings=self.camera_settings
                )
                new_inds.append(ind)
                new_rows.append(row)
                thumbnails.append(thumb_array)
            except:
                #
                # Sampling the "right" time creates a time sample
                # that might not be in the data frame (e.g. hdr=0).
                #
                pass

        #
        # Send reply on next ioloop cycle.
        # The array is sent as mat file to save
        # band width
        #
        matfile = dict2buff(dict(thumbnails=thumbnails, jpeg=True))

        return (), dict(
            thumbnails=matfile,
            thumbs_df=pd.DataFrame.from_records(new_rows, index=new_inds)
        )

    @gen.coroutine
    def handle_array(
            self,
            exposure_us=None,
            gain_db=0,
            gain_boost=False,
            color_mode=gs.COLOR_RAW,
            frames_num=1,
            normalize=True,
            jpeg=False,
            resolution=gs.DEFAULT_NORMALIZATION_SIZE):

        """Capture a new array.

        Args:
            exposure_us (int, optional): Exposure time in micro seconds. Default Auto.
            gain_db (int, optional): Digital gain (0-100). Default 0.
            gain_boost (bool, optional): Analog gain boost. Default False.
            color_mode (int, optional): Color mode of image. Default RAW.
            frames_num (int, optional): Number of frames to capture. Default 1.
            jpeg (bool, optional): Whether to whether to compress as jpeg. Default False.
            normalize (bool, optional): Whether to normalize the image. Default True.

        Returns:
            Compressed mat file in the form of a string.
        """

        #
        # Create the image data object.
        #
        img_data = DataObj(
            longitude=self.camera_settings[gs.CAMERA_LONGITUDE],
            latitude=self.camera_settings[gs.CAMERA_LATITUDE],
            altitude=self.camera_settings[gs.CAMERA_ALTITUDE],
            name_time=datetime.utcnow()
        )

        #
        # Schedule capture of the thumbnail.
        #
        img_array, img_data = \
            yield self.push_cmd(
                gs.ARRAY_CMD,
                priority=50,
                capture_settings={
                    gs.IMAGE_EXPOSURE: exposure_us,
                    gs.IMAGE_GAIN: gain_db,
                    gs.GAIN_BOOST: gain_boost,
                    gs.COLOR_MODE: color_mode,
                    },
                frames_num=frames_num,
                normalize=normalize,
                jpeg=jpeg,
                resolution=resolution,
                img_data=img_data
            )

        #
        # Send reply on next ioloop cycle.
        # The array is sent as mat file to save
        # band width
        #
        matfile = dict2buff(dict(img_array=img_array, jpeg=jpeg))

        raise gen.Return(((), dict(matfile=matfile, img_data=img_data)))

    @gen.coroutine
    def handle_days(self):
        """Check for available days.

        Returns:
            The list of captured days.
        """

        days_paths = sorted(glob.glob(os.path.join(gs.CAPTURE_PATH, "*")))
        days = [os.path.split(p)[1] for p in days_paths]

        #
        # Send reply on next ioloop cycle.
        #
        raise gen.Return(((), dict(days_list=days)))

    @gen.coroutine
    def handle_sun_days(self):
        """Check for available sun position days.

        Returns:
            The list of days for which there is sun position data.
        """

        days_paths = sorted(glob.glob(os.path.join(gs.SUN_POSITIONS_PATH, "*.csv")))
        days = [os.path.split(p)[1][:-4] for p in days_paths]

        #
        # Send reply on next ioloop cycle.
        #
        raise gen.Return(((), dict(days_list=days)))

    @gen.coroutine
    def handle_query(self, query_date, force=False):
        """Seek for a previously captured (loop) array.

        Args:
            query_date (datetime object or string): The date for which
                to query for images. If string is give, dateutil.parser
                will be used for guessing the right date.
            force (bool, optional): Force calculating the images database
                for this day.

        Returns:
            A list of mat file names from the requested date.
        """

        #
        # Form file names.
        #
        if type(query_date) == str:
            query_date = dtparser.parse(query_date)

        query_df = getImagesDF(query_date, force)

        #
        # Send reply on next ioloop cycle.
        #
        raise gen.Return(((), dict(images_df=query_df)))

    @gen.coroutine
    def handle_seek(
            self,
            seek_time,
            hdr_index=0,
            normalize=False,
            jpeg=False,
            resolution=gs.DEFAULT_NORMALIZATION_SIZE,
            correct_radiometric=True,
            ignore_date_extrinsic=False,
            timedelta_threshold=60
            ):
        """Seek for a previously captured (loop) array.

        Args:
            seek_time (pd.Timestamp, datetime or string): The datetime to use as
                index into the dataframe.
            hdr_index (int, optional): The hdr index of the frame. If hdr_index<0, all
                images of the seek_time will be merged into an HDR image.
            normalize (bool, optional): Whether to normalize the frame. Default True.
            jpeg (bool, optional): Whether to compress as jpeg stream. Default False.
            resolution (int, optional): The resolution of the normalized frame.
            correct_radiometric (bool): Whether to apply radiometric correction.
                When calculating radiometric correction, it is important NOT to
                fix the measurements.
            ignore_date_extrinsic (bool, optional): Ignore the extrinsic calibration
                settings in the image folder (if exists).
            timedelta_threshold (int, optional): Allow for time delta between
                seeked time to returned index (in seconds).

        Return:
            Compressed mat file in the form of a string.

        """

        if type(seek_time) == str:
            query_date = dtparser.parse(seek_time).date()
        else:
            query_date = seek_time.date()

        query_df = getImagesDF(query_date, force=False)

        img_datas, img_array = self._controller.seekImageArray(
            query_df,
            seek_time,
            hdr_index,
            normalize,
            resolution,
            jpeg,
            self.camera_settings,
            correct_radiometric=correct_radiometric,
            ignore_date_extrinsic=ignore_date_extrinsic,
            timedelta_threshold=timedelta_threshold
        )

        #
        # Send reply on next ioloop cycle.
        # The array is sent as mat file to save
        # band width
        #
        matfile = dict2buff(dict(img_array=img_array, jpeg=jpeg))

        raise gen.Return(((), dict(matfile=matfile, img_data=img_datas[0])))

    @gen.coroutine
    def handle_sprinkler(self, period):
        """Activate the sprinkler for a given period."""

        #
        # Send command to the controller.
        #
        yield self.push_cmd(
            gs.SPRINKLER_CMD,
            priority=50,
            period=period
        )

    @gen.coroutine
    def handle_sunshader(self, angle):
        """Control the sunshade angle."""

        #
        # Send command to the controller.
        #
        yield self.push_cmd(
            gs.SUNSHADER_CMD,
            priority=50,
            angle=angle,
            sunshader_min=self.camera_settings[gs.SUNSHADER_MIN_ANGLE],
            sunshader_max=self.camera_settings[gs.SUNSHADER_MAX_ANGLE]
        )

    @gen.coroutine
    def handle_moon(self):
        """Record moon position."""

        #
        # Send command to the controller.
        #
        yield self.push_cmd(
            gs.MOON_CMD,
            priority=50
        )

    @gen.coroutine
    def handle_reset_camera(self):
        """Reset the camera."""

        #
        # Send command to the controller.
        #
        yield self.push_cmd(
            gs.RESET_CAMERA_CMD,
            priority=50,
        )

    @gen.coroutine
    def handle_dark_images(self):
        """Measure dark images."""

        #
        # Send command to the controller.
        #
        yield self.push_cmd(
            gs.DARK_IMAGES_CMD,
            priority=50,
        )

    @gen.coroutine
    def handle_sunshader_scan(self):
        """Activate the sunshader scan."""

        #
        # Send command to the controller.
        #
        angles, saturated_array, sun_signal, required_angle, centroid = \
            yield self.push_cmd(
                gs.SUNSHADER_SCAN_CMD,
                reply=True,
                sunshader_min=self.camera_settings[gs.SUNSHADER_MIN_ANGLE],
                sunshader_max=self.camera_settings[gs.SUNSHADER_MAX_ANGLE]
            )

        #
        # Send reply on next ioloop cycle.
        #
        raise gen.Return(((), {
            'angles': angles,
            'saturated_array': saturated_array,
            'sun_signal': sun_signal,
            'required_angle':required_angle,}))

    @gen.coroutine
    def handle_extrinsic(self, date, residual_threshold, save, default_position=False):
        """Handle extrinsic calibration.

        Args:
            date (datetime object): date to use for extrinsic calibration.
            residual_threshold (float): residual threshold for the ransac
                algorithm.
            save (bool): Whether to save the calibration.
            default_position (bool): Use the default (EE) Lon/Lat/Alt coords. This
                is useful before doing radiometric calibration.
        """

        if default_position:
            latitude = gs.DEFAULT_LATITUDE
            longitude = gs.DEFAULT_LONGITUDE
            altitude = gs.DEFAULT_ALTITUDE
        else:
            latitude = self.camera_settings[gs.CAMERA_LATITUDE]
            longitude = self.camera_settings[gs.CAMERA_LONGITUDE]
            altitude = self.camera_settings[gs.CAMERA_ALTITUDE]

        if type(date) == str:
            date = dtparser.parse(date)

        #
        # Send command to the controller.
        #
        rotated_directions, calculated_directions, R = \
            yield self.push_cmd(
                gs.EXTRINSIC_CMD,
                date=date.strftime("%Y_%m_%d"),
                latitude=latitude,
                longitude=longitude,
                altitude=altitude,
                residual_threshold=residual_threshold,
                save=save)

        #
        # Send reply on next ioloop cycle.
        #
        raise gen.Return(((), {
            'rotated_directions': rotated_directions,
            'calculated_directions': calculated_directions,
            'R': R,}))

    @gen.coroutine
    def handle_save_extrinsic(self, date):
        """Handle save extrinsic calibration.

        Args:
            date (datetime object): date to save the extrinsic calibration in.
        """

        if type(date) == str:
            date = dtparser.parse(date)

        #
        # Send command to the controller.
        #
        yield self.push_cmd(
            gs.SAVE_EXTRINSIC_CMD,
            date=date.strftime("%Y_%m_%d")
        )

    @gen.coroutine
    def handle_radiometric(self, date, time_index, residual_threshold, save):
        """Handle radiometric calibration.

        Args:
            date (datetime object): date to use for radiometric calibration.
            time_index (int): Index of the sunphotometer measurement of the
                day "date".
            residual_threshold (float): residual threshold for the ransac
                algorithm.
            save (bool): Whether to save the calibration.
        """

        if type(date) == str:
            date = dtparser.parse(date)

        #
        # Send command to the controller.
        #
        angles, measurements, estimations, ratios = \
            yield self.push_cmd(
                gs.RADIOMETRIC_CMD,
                date=date,
                time_index=time_index,
                residual_threshold=residual_threshold,
                save=save,
                camera_settings=self.camera_settings
            )

        #
        # Send reply on next ioloop cycle.
        #
        raise gen.Return(((), {
            'angles': angles,
            'measurements': measurements,
            'estimations': estimations,
            'ratios': ratios,
        }))

    @gen.coroutine
    def handle_calibration(self, nx, ny, imgs_num, delay, exposure_us,
                           gain_db, gain_boost):
        """Initiate the calibration process."""

        #
        # Send command to the controller.
        #
        img_array, K, D, rms, rvecs, tvecs = \
            yield self.push_cmd(
                gs.CALIBRATION_CMD,
                priority=50,
                nx=nx,
                ny=ny,
                imgs_num=imgs_num,
                delay=delay,
                exposure_us=exposure_us,
                gain_db=gain_db,
                gain_boost=gain_boost,
                sunshader_min=self.camera_settings[gs.SUNSHADER_MIN_ANGLE]
            )

        #
        # Convert the image to PIL and compress it to png.
        #
        img = Image.fromarray(img_array)
        draw = ImageDraw.Draw(img)
        draw.text(
            (0, 0),
            "rms: {rms}".format(rms=rms),
            (255,255,255) if img_array.ndim == 3 else 255
        )
        f = StringIO.StringIO()
        img.save(f, format="JPEG")

        #
        # Send reply on next ioloop cycle.
        #
        raise gen.Return(((), {
            'img_array': f.getvalue(),
            'K': K,
            'D': D,
            'rms': rms,
            'rvecs': rvecs,
            'tvecs': tvecs}))

    @gen.coroutine
    def handle_tunnel(self, tunnel_state):
        """Start a reverse ssh tunnel."""

        #
        # Check if the tunnel is already on.
        #
        if self.tunnel_process is not None:
            if tunnel_state:
                raise Exception("Tunnel is already on.")

            try:
                logging.debug(
                    "pid of process {}".format(self.tunnel_process.pid)
                )
                logging.debug("killing tunnel process")
                self.tunnel_process.kill()
            except:
                pass

            self.tunnel_process = None

        if tunnel_state:
            #
            # Turned on the tunnel.
            #
            self.tunnel_process, self.tunnel_port = \
                setup_reverse_ssh_tunnel(**self.proxy_params)

    @gen.coroutine
    def handle_tunnel_details(self):
        """Get the tunnel details"""

        if self.tunnel_process is None:
            #
            # Probably somebody shut the tunnel
            #
            raise Exception("No tunnel open")

        if self.tunnel_process.poll() is not None:
            #
            # Trying to open the tunnel caused an error..
            #
            logging.debug("getting the error message")
            err = self.tunnel_process.communicate()
            logging.debug("error message: {}".format(err))

            raise Exception(str(err))

        #
        # The tunnel process has not ended therefore it is still alive.
        #
        raise gen.Return(((), dict(
            tunnel_port=self.tunnel_port,
            tunnel_ip=self.proxy_params[gs.PROXY_IP],
            tunnel_user=gs.PI_USER if identify_server() == gs.PI_SERVER else gs.ODROID_USER,
            tunnel_pw=gs.PI_PW if identify_server() == gs.PI_SERVER else gs.ODROID_PW)))

    @gen.coroutine
    def handle_local_ip(self):
        """Get the local ip"""

        eth0 = subprocess.check_output(["sudo", "ifconfig", "eth0"])
        ip = re.compile('inet addr:([\d.]+)').search(eth0).group(1)

        if ip == '':
            raise Exception("Could not find local ip.")

        raise gen.Return(((), dict(
            port=22,
            ip=ip,
            user=gs.PI_USER if identify_server() == gs.PI_SERVER else gs.ODROID_USER,
            pw=gs.PI_PW if identify_server() == gs.PI_SERVER else gs.ODROID_PW)))

    @gen.coroutine
    def handle_restart(self):
        """Handle restart message."""

        if self.tunnel_process is not None:
            try:
                self.tunnel_process.kill()
            except:
                pass

        logging.info("Handling restart message")

        #
        # Send command to the controller.
        #
        yield self.push_cmd(
            gs.RESTART_CMD,
            priority=50,
        )

        logging.info("Finished restarting camera")

        #
        # Note:
        # I don't raise an exception becuase for some reason it
        # doesn't reach the main script.
        #
        restart_program()

    @gen.coroutine
    def handle_reboot(self):
        """Handle reboot message."""

        if self.tunnel_process is not None:
            try:
                self.tunnel_process.kill()
            except:
                pass

        logging.info("Performing reboot")
        logging.shutdown()

        os.system('sudo reboot')
