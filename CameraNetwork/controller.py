#
# Copyright (C) 2017, Amit Aides, all rights reserved.
#
# This file is part of Camera Network
# (see https://bitbucket.org/amitibo/cameranetwork_git).
#
# Redistribution and use in source and binary forms, with or without modification,
# are permitted provided that the following conditions are met:
#
# 1)  The software is provided under the terms of this license strictly for
#     academic, non-commercial, not-for-profit purposes.
# 2)  Redistributions of source code must retain the above copyright notice, this
#     list of conditions (license) and the following disclaimer.
# 3)  Redistributions in binary form must reproduce the above copyright notice,
#     this list of conditions (license) and the following disclaimer in the
#     documentation and/or other materials provided with the distribution.
# 4)  The name of the author may not be used to endorse or promote products derived
#     from this software without specific prior written permission.
# 5)  As this software depends on other libraries, the user must adhere to and keep
#     in place any licensing terms of those libraries.
# 6)  Any publications arising from the use of this software, including but not
#     limited to academic journal and conference publications, technical reports and
#     manuals, must cite the following works:
#     Dmitry Veikherman, Amit Aides, Yoav Y. Schechner and Aviad Levis,
#     "Clouds in The Cloud" Proc. ACCV, pp. 659-674 (2014).
#
# THIS SOFTWARE IS PROVIDED BY THE AUTHOR "AS IS" AND ANY EXPRESS OR IMPLIED
# WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF
# MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO
# EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT,
# INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING,
# BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE,
# DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF
# LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE
# OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF
# ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

from __future__ import division
import bisect
from CameraNetwork.arduino_utils import ArduinoAPI
from CameraNetwork.calibration import RadiometricCalibration
from CameraNetwork.calibration import VignettingCalibration
from CameraNetwork.cameras import IDSCamera
import CameraNetwork.global_settings as gs
from CameraNetwork.image_utils import calcHDR
from CameraNetwork.image_utils import FisheyeProxy
from CameraNetwork.image_utils import Normalization
import CameraNetwork.sunphotometer as spm
from CameraNetwork.utils import cmd_callback
from CameraNetwork.utils import DataObj
from CameraNetwork.utils import find_camera_orientation_ransac
from CameraNetwork.utils import find_centroid
from CameraNetwork.utils import getImagesDF
from CameraNetwork.utils import IOLoop
from CameraNetwork.utils import mean_with_outliers
from CameraNetwork.utils import name_time
from CameraNetwork.utils import object_direction
from CameraNetwork.utils import RestartException
import copy
import cPickle
import cv2
from dateutil import parser as dtparser
from datetime import datetime
from datetime import timedelta
import ephem
import fisheye

try:
    import futures
except:
    #
    # Support also python 2.7
    #
    from concurrent import futures
import glob
import json
import Image
import logging
import numpy as np
import os
import pandas as pd
import pkg_resources
import Queue
from scipy import signal
import scipy.io as sio
import shutil
from sklearn import linear_model
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import PolynomialFeatures
import StringIO
import subprocess
import sys
import time
import thread
from tornado import gen
from tornado.concurrent import Future
from tornado.concurrent import run_on_executor
from tornado.queues import PriorityQueue as tPriorityQueue
import traceback


def interpolate_dark_images(exposure, exposures, dark_images):
    """Interpolate the corresponding dark image."""

    ri = np.searchsorted(exposures, exposure)

    #
    # Check if index in bounds
    #
    if ri == len(exposures):
        return dark_images[-1]
    elif ri == 0:
        return dark_images[0]

    re = exposures[ri]

    #
    # Check if we measured exactly the same exposure.
    #
    if exposure == re:
        return dark_images[ri]

    li = ri - 1
    le = exposures[li]

    #
    # Estimate dark image using linear interpolation.
    #
    dark_image = dark_images[li] + (dark_images[ri] - dark_images[li]) * (exposure - le) / (re - le)

    return dark_image


def time2seconds(dt):
    """Convert datetime object to seconds."""

    seconds = (dt.hour * 60 + dt.minute) * 60 + dt.second + dt.microsecond * 1e-6
    return seconds


class Controller(object):
    #
    # Thread pull
    #
    executor = futures.ThreadPoolExecutor(4)

    def __init__(self, offline=False, local_path=None):

        gs.initPaths(local_path)

        #
        # Queues for communicating with the server.
        #
        self._in_queue = tPriorityQueue()

        #
        # Hardware
        #
        if not offline:
            self.start_camera()
            self._arduino_api = ArduinoAPI()
        else:
            self._camera = None

        self._offline = offline

        #
        # Set the last calibration path.
        # Note:
        # The calibration path handles the case of multiple calibration dates.
        #
        self._last_calibration_path = None

        #
        # Load the camera calibration information.
        #
        if self._camera is not None:
            self.loadCameraCalibration()

        #
        # Load dark images.
        #
        self.loadDarkImages()

        #
        # Load today's celestial position measurements
        #
        if not os.path.exists(gs.SUN_POSITIONS_PATH):
            os.makedirs(gs.SUN_POSITIONS_PATH)
        else:
            self.loadSunMeasurements()

        self.sunshader_angle_model = make_pipeline(
            PolynomialFeatures(2),
            linear_model.RANSACRegressor(random_state=0, residual_threshold=5)
        )

        #
        # Set the last sunshader scan to "old" time.
        #
        self.last_sunshader_time = None
        self.sunshader_fit = False

        #
        # Sky mask
        #
        if os.path.exists(gs.MASK_PATH):
            try:
                self.sky_mask_base = sio.loadmat(gs.MASK_PATH)['mask_base']
            except Exception, e:
                logging.error("Failed loading sky mask.")
                logging.error("{}".format(traceback.print_exc()))
                self.sky_mask_base = None
        else:
            self.sky_mask_base = None

    def loadCameraCalibration(self, capture_date=None, serial_num=None):
        """Load camera calibration data

        Load the intrinsic and radiometric calibration data.

        Args:
            capture_date (datetime object, optional): Date of capture. If
                None (default), now will be assumed.
            serial_num (str, optional): serial number of sensor. If None
                (default), will be taken directly from the sensor.
        """

        logging.debug("Loading Camera Calibration.")

        if serial_num is None:
            logging.debug("Serial number not given.")
            if capture_date is not None:
                #
                # Read the serial number from an arbitrary image from the
                # requested dat.
                #
                day_path = os.path.join(gs.CAPTURE_PATH, capture_date.strftime("%Y_%m_%d"))
                datas_list = sorted(glob.glob(os.path.join(day_path, '*.pkl')))

                #
                # I search the inverted datas_list for the case that the
                # function was called to handle intrinsic calibration. This
                # handles the case that the sensor was replaced during the day.
                #
                for data_path in datas_list[::-1]:
                    try:
                        with open(data_path, "rb") as f:
                            data = cPickle.load(f)
                        serial_num = data.camera_info["serial_num"]
                        logging.debug(
                            "Serial number {} taken from: {}".format(serial_num, data_path)
                        )
                        break
                    except:
                        pass
            else:
                #
                # Not loading a previously saved image use the camera sensor num.
                #
                serial_num = self._camera.info['serial_num']
                logging.debug(
                    "Serial number {} taken from Camera sensor.".format(serial_num)
                )

        self.base_calibration_path = os.path.join(
            pkg_resources.resource_filename(__name__, '../data/calibration/'),
            serial_num
        )

        #
        # Get the list of calibration dates.
        #
        calibration_dates_paths = sorted(glob.glob(os.path.join(self.base_calibration_path, "20*")))
        if len(calibration_dates_paths) == 0:
            calibration_path = self.base_calibration_path
        else:
            calibration_dates = [os.path.split(cdp)[-1] for cdp in calibration_dates_paths]
            calibration_dates = [datetime.strptime(d, "%Y_%m_%d") for d in calibration_dates]

            #
            # Check the relevant calibration date.
            #
            if capture_date is None:
                #
                # Live capture, take the most updated index.
                #
                calibration_index = -1
            else:
                calibration_index = bisect.bisect(calibration_dates, capture_date) - 1

            calibration_path = calibration_dates_paths[calibration_index]

        logging.debug("Calibration path is: {}".format(calibration_path))

        if self._last_calibration_path is not None and \
                self._last_calibration_path == calibration_path:
            #
            # No need to load new calibration data.
            #
            logging.debug("Calibration data previously loaded.")

            return

        self._last_calibration_path = calibration_path

        #
        # Check if the data exists in the data folder of the code.
        # If so, the data is copied to the home folder.
        # Note:
        # This is done to support old cameras that were not calibrated
        # using the test bench.
        #
        if os.path.exists(self.base_calibration_path):
            for base_path, file_name, dst_path in zip(
                    (calibration_path, calibration_path, self.base_calibration_path),
                    (gs.INTRINSIC_SETTINGS_FILENAME, gs.VIGNETTING_SETTINGS_FILENAME, gs.RADIOMETRIC_SETTINGS_FILENAME),
                    (gs.INTRINSIC_SETTINGS_PATH, gs.VIGNETTING_SETTINGS_PATH, gs.RADIOMETRIC_SETTINGS_PATH)
            ):
                try:
                    shutil.copyfile(
                        os.path.join(base_path, file_name),
                        dst_path)
                except Exception as e:
                    logging.error("Failed copying calibration data: {}\n{}".format(
                        file_name, traceback.format_exc()))

        #
        # Try to load calibration data.
        #
        self._fe = None
        ocam_path = os.path.join(calibration_path, "ocamcalib.pkl")
        print("Searching for ocam path: {}".format(ocam_path))
        logging.info("Will search for ocamcalib in path: ".format(ocam_path))
        if os.path.exists(ocam_path):
            #
            # Found an ocamcalib model load it.
            #
            print("Found ocam path: {}".format(ocam_path))
            logging.info("Loading an ocamcalib model from:".format(ocam_path))
            with open(ocam_path, "rb") as f:
                self._fe = cPickle.load(f)
        elif os.path.exists(gs.INTRINSIC_SETTINGS_PATH):
            #
            # Found an opencv2 fisheye model.
            #
            logging.info("Loading a standard opencv fisheye model")
            self._fe = fisheye.load_model(
                gs.INTRINSIC_SETTINGS_PATH, calib_img_shape=(1200, 1600))

        if self._fe is not None:
            #
            # Creating the normalization object.
            #
            self._normalization = Normalization(
                gs.DEFAULT_NORMALIZATION_SIZE, FisheyeProxy(self._fe)
            )
            if os.path.exists(gs.EXTRINSIC_SETTINGS_PATH):
                self._normalization.R = np.load(
                    gs.EXTRINSIC_SETTINGS_PATH
                )
        else:
            self._normalization = None

        #
        # Load vignetting settings.
        #
        try:
            self._vignetting = VignettingCalibration.load(gs.VIGNETTING_SETTINGS_PATH)
        except:
            self._vignetting = VignettingCalibration()
            logging.error(
                "Failed loading vignetting data:\n{}".format(
                    traceback.format_exc()))

        #
        # Load radiometric calibration.
        #
        try:
            self._radiometric = RadiometricCalibration.load(gs.RADIOMETRIC_SETTINGS_PATH)
        except:
            self._radiometric = RadiometricCalibration(gs.DEFAULT_RADIOMETRIC_SETTINGS)
            logging.debug("Failed loading radiometric data. Will use the default values.")

    def loadDarkImages(self):
        """Load dark images from disk.

        Dark images are used for reducing dark current noise.
        """

        di_paths = sorted(glob.glob(os.path.join(gs.DARK_IMAGES_PATH, '*.mat')))
        if di_paths:
            self._dark_images = {
                False: {'exposures': [], 'images': []},
                True: {'exposures': [], 'images': []},
            }

            #
            # Load the dark images from disk
            #
            for path in di_paths:
                d = sio.loadmat(path)
                gain_boost = d['gain_boost'][0][0] == 1
                self._dark_images[gain_boost]['exposures'].append(d['exposure'][0][0])
                self._dark_images[gain_boost]['images'].append(d['image'])

            #
            # Sort the images according to exposures.
            #
            for gain_boost in (False, True):
                exposures = np.array(self._dark_images[gain_boost]['exposures'])
                indices = np.argsort(exposures)
                self._dark_images[gain_boost]['exposures'] = exposures[indices]
                dark_images = self._dark_images[gain_boost]['images']
                self._dark_images[gain_boost]['images'] = [dark_images[i] for i in indices]
        else:
            logging.info("No dark images available")
            self._dark_images = None

    def loadSunMeasurements(self):
        """Load previously stored sun measurements."""

        try:
            #
            # Check past measurements.
            # TODO:
            # Add filtering based on date (i.e. not look too further back).
            #
            past_measurements_paths = sorted(
                glob.glob(os.path.join(gs.SUN_POSITIONS_PATH, '*.csv')))

            if past_measurements_paths:
                angles = []
                for path in past_measurements_paths[-2:]:
                    try:
                        data = pd.read_csv(path, index_col=0, parse_dates=True)
                    except Exception as e:
                        logging.error('Error parsing sun measurements file. The file will be deleted:\n{}'.format(
                            traceback.format_exc()))
                        os.remove(path)
                        continue

                    #
                    # Limit the data to sun measurements only.
                    #
                    data = data[data['object'] == 'Sun']

                    #
                    # Limit the data to angles between a range of "valid"
                    # angles.
                    #
                    data = data[
                        (data['sunshader_angle'] > gs.SUNSHADER_MIN_MEASURED) &
                        (data['sunshader_angle'] < gs.SUNSHADER_MAX_MEASURED)
                        ]

                    data.index = data.index.time
                    angles.append(data['sunshader_angle'])

                self.sunshader_angles_df = pd.concat(
                    angles, axis=1
                ).mean(axis=1).to_frame(name='angle')
            else:
                self.sunshader_angles_df = pd.DataFrame(dict(angle=[]))

        except Exception as e:
            logging.error('Error while loading past sun measurements:\n{}'.format(
                traceback.format_exc()))
            self.sunshader_angles_df = pd.DataFrame(dict(angle=[]))

    def __del__(self):
        self.delete_camera()

    @property
    def cmd_queue(self):
        return self._in_queue

    def start(self):
        #
        # Start the loop of reading commands of the cmd queue.
        #
        IOLoop.current().spawn_callback(self.process_cmds)

    def start_camera(self):
        logging.info("Starting camera")
        self._camera = IDSCamera()

    def delete_camera(self):
        if hasattr(self, '_camera'):
            logging.info("Deleting camera")
            self._camera.close()
            del self._camera

    def safe_capture(self, settings, frames_num=1,
                     max_retries=gs.MAX_CAMERA_RETRIES):
        """A wrapper around the camera capture.

        It will retry to capture a frame handling
        a predetermined amount of failures before
        raising an error.
        """

        retries = max_retries
        while True:
            try:
                img_array, real_exposure_us, real_gain_db = \
                    self._camera.capture(settings, frames_num)
                break
            except Exception as e:
                if retries <= 0:
                    logging.exception(
                        'The camera failed too many consecutive times. Reboot.'
                    )
                    logging.shutdown()
                    os.system('sudo reboot')

                retries -= 1
                logging.error(
                    "The camera raised an Exception:\n{}".format(
                        traceback.format_exc()
                    )
                )

                try:
                    self.delete_camera()
                    time.sleep(gs.CAMERA_RESTART_PERIOD)
                    self.start_camera()
                except Exception as e:
                    logging.exception(
                        'The camera failed restarting. Reboot.'
                    )
                    logging.shutdown()
                    time.sleep(120)
                    os.system('sudo reboot')

        return img_array, real_exposure_us, real_gain_db

    @cmd_callback
    @gen.coroutine
    def handle_sunshader_update(self, sunshader_min, sunshader_max):
        """Update the sunshader position."""

        current_time = datetime.utcnow()
        if self.last_sunshader_time is not None:
            #
            # Calculate time from last scan.
            #
            dt = (current_time - self.last_sunshader_time)
        else:
            #
            # Take value large enough to force scan
            #
            dt = timedelta(seconds=2 * gs.SUNSHADER_SCAN_PERIOD_LONG)

        #
        # current_time_only is without date, and used for interpolating
        # sunshader position.
        #
        current_time_only = datetime.time(current_time)

        #
        # Set some parameters according to whether the model is already
        # fitting.
        #
        if self.sunshader_fit:
            #
            # The model is already fitting.
            #
            current_angle = self._arduino_api.getAngle()
            sunshader_scan_min = max(
                current_angle - gs.SUNSHADER_SCAN_DELTA_ANGLE, sunshader_min
            )
            sunshader_scan_max = min(
                current_angle + gs.SUNSHADER_SCAN_DELTA_ANGLE, sunshader_max
            )
            sunshader_scan_period = gs.SUNSHADER_SCAN_PERIOD_LONG
        else:
            sunshader_scan_min = sunshader_min
            sunshader_scan_max = sunshader_max
            sunshader_scan_period = gs.SUNSHADER_SCAN_PERIOD

        #
        # Is it time to do a scan?
        #
        measured_angle = None
        if dt > timedelta(seconds=sunshader_scan_period):
            self.last_sunshader_time = current_time

            logging.info('Time to scan')

            #
            # Do a scan.
            #
            future = Future()
            yield self.handle_sunshader_scan(future, reply=False,
                                             sunshader_min=sunshader_scan_min,
                                             sunshader_max=sunshader_scan_max
                                             )
            measured_angle, _ = future.result()
            logging.info("Measured angle: {}".format(measured_angle))

            #
            # Update database with new measurement
            # First, add new measurement to dataframe of angles.
            #
            if gs.SUNSHADER_MIN_MEASURED < measured_angle < gs.SUNSHADER_MAX_MEASURED:
                self.sunshader_angles_df.loc[current_time_only] = measured_angle
                self.sunshader_angles_df = self.sunshader_angles_df.sort_index()

            #
            # Refit model.
            #
            if len(self.sunshader_angles_df) >= 10:
                X = np.array(
                    [time2seconds(dt) for dt in self.sunshader_angles_df.index]
                ).reshape(-1, 1)
                y = self.sunshader_angles_df['angle'].values
                try:
                    self.sunshader_angle_model.fit(X, y)
                    self.sunshader_fit = True
                except Exception as e:
                    logging.info('Sunshader failed to fit:\n{}'.format(e))
                    self.sunshader_fit = False

        #
        # If model fitting failed or there are not enough measurements for
        # interpolation angle use measured angle.
        #
        if (not self.sunshader_fit) or \
                len(self.sunshader_angles_df) < gs.SUNSHADER_MIN_ANGLES:
            logging.info("Either failed fitting or not enough measurements")
            if measured_angle is not None:
                logging.info("Using measured angle: {}".format(measured_angle))
                self._arduino_api.setAngle(measured_angle)
            else:
                logging.debug("Sunshader not moved.")
            return

        #
        # Interpolate angle.
        #
        X = np.array((time2seconds(current_time_only),)).reshape(-1, 1)
        estimated_angle = self.sunshader_angle_model.predict(X)[0]

        logging.info("Interpolating angle: {}".format(estimated_angle))
        self._arduino_api.setAngle(estimated_angle)

    @cmd_callback
    @run_on_executor
    def handle_sunshader_scan(self, reply, sunshader_min, sunshader_max):
        """Scan with the sunshader to find sun position."""

        #
        # Change camera to small size.
        #
        self._camera.small_size()

        #
        # 'Reset' the sunshader.
        #
        self._arduino_api.setAngle(sunshader_min)
        time.sleep(1)

        #
        # Capture an image for the sky mask.
        #
        img, _, _ = self.safe_capture(
            settings={
                "exposure_us": 500,
                "gain_db": None,
                "gain_boost": False,
                "color_mode": gs.COLOR_RGB
            }
        )
        self.update_sky_mask(img)

        #
        # Sunshader scan loop.
        #
        saturated_array = []
        centers = []
        for i in range(sunshader_min, sunshader_max):
            self._arduino_api.setAngle(i)
            time.sleep(0.1)
            img, e, g = self.safe_capture(
                settings={
                    "exposure_us": 200,
                    "gain_db": None,
                    "gain_boost": False,
                    "color_mode": gs.COLOR_RGB
                }
            )
            # TODO CONST 128 and why 128 and not something else?
            val = img[img > 128].sum() / img.size

            logging.debug(
                "Exp.: {}, Gain: {}, image range: [{}, {}], Value: {}".format(
                    e, g, img.min(), img.max(), val
                )
            )

            if np.isnan(val):
                np.save('/home/odroid/nan_img.npy', img)

            saturated_array.append(val)
            centers.append(find_centroid(img))

        #
        # Change camera back to large size.
        #
        self._camera.large_size()

        #
        # Calculate centroid of sun in images.
        #
        centers = np.array(centers)
        centroid = mean_with_outliers(centers)[0] * 4
        logging.debug("Centroid of suns: {}".format(centroid))

        #
        # Calculate the required sunshader angle.
        # Note:
        # The saturated_array is smoothed with a butterworth filter. The order
        # of the filter is set so that it will not cause filtfilt to throw the
        # error:
        # ValueError: The length of the input vector x must be at least padlen, which is 27.
        #
        saturated_array = pd.Series(saturated_array).fillna(method='bfill').values

        N = min(8, int((len(saturated_array) - 1) / 3) - 1)
        if N >= 4:
            b, a = signal.butter(N, 0.125)
            sun_signal = signal.filtfilt(b, a, saturated_array)
        else:
            sun_signal = saturated_array

        measured_angle = sunshader_min + np.argmin(sun_signal)

        #
        # Update sun positions file
        #
        today_positions_path = os.path.join(
            gs.SUN_POSITIONS_PATH,
            datetime.utcnow().strftime("%Y_%m_%d.csv"))
        if os.path.exists(today_positions_path):
            positions_df = pd.read_csv(today_positions_path, index_col=0)
        else:
            positions_df = pd.DataFrame(columns=('object', 'pos_x', 'pos_y', 'sunshader_angle'))

        positions_df.loc[datetime.utcnow()] = ('Sun', centroid[0], centroid[1], measured_angle)
        positions_df.to_csv(today_positions_path)

        #
        # Set the new angle of the sunshader.
        #
        self._arduino_api.setAngle(measured_angle)

        #
        # Send back the analysis.
        #
        if reply:
            angles = np.arange(sunshader_min, sunshader_max)
            return angles, np.array(saturated_array), sun_signal, measured_angle, centroid

        return measured_angle, centroid

    def update_sky_mask(self, img):
        """Update the sky mask.

        Args:
            img (array): RGB image.
        """

        #
        # Calculate the mask factor
        #
        mat = img.astype(np.float)
        r = mat[..., 0]
        g = mat[..., 1]
        b = mat[..., 2]
        new_mask = (b > 30) & (b > 1.5 * r)

        #
        # Accumulate the mask factor
        #
        if self.sky_mask_base is None:
            self.sky_mask_base = new_mask
        else:
            tmp = np.dstack((self.sky_mask_base, new_mask))
            self.sky_mask_base = tmp.max(axis=2)

        #
        # Calculate the mask.
        #
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (15, 15))
        mask = cv2.morphologyEx(
            self.sky_mask_base.astype(np.uint8), cv2.MORPH_OPEN,
            kernel, iterations=1)
        _, contours, _ = cv2.findContours(
            mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        if len(contours) == 0:
            logging.info('No sky mask contours found.')
            return

        contour = sorted(contours, key=cv2.contourArea, reverse=True)[0]

        self.sky_mask = np.zeros_like(mask)
        self.sky_mask = cv2.drawContours(self.sky_mask, [contour], -1, 255, -1)

        #
        # Store the masks
        #
        logging.info('Updating the sun mask.')
        sio.savemat(
            gs.MASK_PATH,
            dict(mask_base=self.sky_mask_base, mask=self.sky_mask),
            do_compression=True)

    @cmd_callback
    @run_on_executor
    def handle_calibration(self, nx, ny, imgs_num, delay, exposure_us,
                           gain_db, gain_boost, sunshader_min):
        """Start the geometric calibration."""

        logging.debug(
            "Handling calibration: nx: {}, ny: {}, imgs_num: {}, delay: {}".format(
                nx, ny, imgs_num, delay
            )
        )

        #
        # Create debug imgs folder.
        #
        DEBUG_IMGS_PATH = os.path.expanduser('~/calibration_imgs')
        if os.path.exists(DEBUG_IMGS_PATH):
            shutil.rmtree(DEBUG_IMGS_PATH)
        os.makedirs(DEBUG_IMGS_PATH)

        logging.debug("Setting the sunshader away")

        #
        # Put the sunshader away.
        #
        self._arduino_api.setAngle(sunshader_min)
        time.sleep(1)

        #
        # Capture the calibration images.
        #
        imgs = []
        for i in range(imgs_num):
            self._arduino_api.setAngle(sunshader_min + 2)
            img, real_exposure_us, real_gain_db = self._camera.capture(
                settings={
                    "exposure_us": exposure_us,
                    "gain_db": gain_db,
                    "gain_boost": gain_boost,
                    "color_mode": gs.COLOR_RGB
                }
            )
            self._arduino_api.setAngle(sunshader_min)

            imgs.append(img)
            logging.debug(
                "dtype: {}, min: {}, max: {}, shape: {}, exposure: {}, gain_db: {}".format(
                    img.dtype, img.min(), img.max(), img.shape,
                    real_exposure_us, real_gain_db
                )
            )
            cv2.imwrite(
                os.path.join(DEBUG_IMGS_PATH, 'img_{}.jpg'.format(i)), img
            )
            time.sleep(delay)

        #
        # Calibrate the camera
        #
        logging.debug("Starting calibration")
        self._fe = fisheye.FishEye(nx=nx, ny=ny, verbose=True)
        rms, K, D, rvecs, tvecs = self._fe.calibrate(
            imgs=imgs,
            show_imgs=False
        )
        logging.debug("Finished calibration. RMS: {}.".format(rms))
        self._fe.save(gs.INTRINSIC_SETTINGS_PATH)

        #
        # Creating the normalization object.
        #
        self._normalization = Normalization(
            gs.DEFAULT_NORMALIZATION_SIZE, FisheyeProxy(self._fe)
        )
        normalized_img = self._normalization.normalize(img)

        #
        # Send back calibration results and normalized image example.
        #
        return normalized_img, K, D, rms, rvecs, tvecs

    @cmd_callback
    @gen.coroutine
    def handle_sunshader(self, angle, sunshader_min, sunshader_max):
        """Set the sunshader to an angle"""

        if angle < sunshader_min or angle > sunshader_max:
            raise ValueError(
                "Sunshader angle ({}) not in range ({},{})".format(
                    angle, sunshader_min, sunshader_max
                )
            )

        self._arduino_api.setAngle(angle)

    @cmd_callback
    @gen.coroutine
    def handle_sprinkler(self, period):
        """Activate the sprinkler for a given period."""

        self._arduino_api.setSprinkler(True)
        yield gen.sleep(period)
        self._arduino_api.setSprinkler(False)

    @cmd_callback
    @run_on_executor
    def handle_moon(self, sunshader_min):
        """Measure Moon position"""

        self._arduino_api.setAngle(sunshader_min)
        time.sleep(0.1)
        img, _, _ = self.safe_capture(
            settings={
                "exposure_us": 1000000,
                "gain_db": None,
                "gain_boost": True,
                "color_mode": gs.COLOR_RGB
            }
        )
        centroid = find_centroid(img)

        #
        # Update positions file
        #
        today_positions_path = os.path.join(
            gs.SUN_POSITIONS_PATH,
            datetime.utcnow().strftime("%Y_%m_%d.csv"))
        if os.path.exists(today_positions_path):
            positions_df = pd.read_csv(today_positions_path, index_col=0)
        else:
            positions_df = pd.DataFrame(columns=('object', 'pos_x', 'pos_y', 'sunshader_angle'))

        positions_df.loc[datetime.utcnow()] = ('Moon', centroid[0], centroid[1], -1)
        positions_df.to_csv(today_positions_path)

    @cmd_callback
    @run_on_executor
    def handle_extrinsic(
            self,
            date,
            latitude,
            longitude,
            altitude,
            residual_threshold,
            save):
        """Handle extrinsic calibration"""

        #
        # Update the calibration data.
        #
        try:
            self.loadCameraCalibration(
                capture_date=datetime.strptime(date, "%Y_%m_%d")
            )
        except:
            logging.warn(
                "Failed loading calibration for extrinsic date {}\n{}".format(
                    date, traceback.format_exc())
            )

        #
        # Load sun measurements.
        #
        today_positions_path = os.path.join(
            gs.SUN_POSITIONS_PATH, "{}.csv".format(date))

        if not os.path.exists(today_positions_path):
            raise Exception('No sun positions for date: {}'.format(date))

        #
        # Calibration is done using the sun position.
        #
        positions_df = pd.read_csv(today_positions_path, index_col=0, parse_dates=True)
        positions_df = positions_df[positions_df['object'] == 'Sun']
        positions_df = positions_df.dropna()

        if positions_df.shape[0] < gs.EXTRINSIC_CALIBRATION_MIN_PTS:
            raise Exception('No enough sun positions: {}'.format(
                positions_df.shape[0]))

        #
        # Convert sun measurements to directions.
        #
        measured_positions = positions_df[['pos_x', 'pos_y']].as_matrix()
        phi, theta, mask = self._normalization._fisheye_model.undistortDirections(measured_positions)

        measured_directions = np.array(
            (
                np.sin(theta) * np.cos(phi),
                -np.sin(theta) * np.sin(phi),
                np.cos(theta)
            )
        ).T

        #
        # Calculated direction (using the ephem package.)
        #
        calculated_directions = []
        for d in positions_df.index:
            calculated_directions.append(
                object_direction(
                    celestial_class=ephem.Sun,
                    date=d,
                    latitude=latitude,
                    longitude=longitude,
                    altitude=altitude
                )
            )
        calculated_directions = np.array(calculated_directions)

        #
        # Estimate orientation
        #
        R, rotated_directions = find_camera_orientation_ransac(
            calculated_directions, measured_directions, residual_threshold)

        #
        # Update normalization model.
        #
        self._normalization.R = R
        if save:
            np.save(gs.EXTRINSIC_SETTINGS_PATH, R)
            #
            # Save a copy in the calibration day.
            #
            calibration_day_path = os.path.join(gs.CAPTURE_PATH, date)
            if os.path.exists(calibration_day_path):
                np.save(
                    os.path.join(
                        calibration_day_path,
                        gs.EXTRINSIC_SETTINGS_FILENAME
                    ),
                    R
                )
            else:
                logging.warn(
                    "Cannot save extrinsic data in capture day (missing?)."
                )

        #
        # Send back the analysis.
        #
        return rotated_directions, calculated_directions, R

    @cmd_callback
    @gen.coroutine
    def handle_save_extrinsic(self, date):
        """Handle save extrinsic calibration command

        This command saves the current extrinsic calibration on a specific
        date.
        """

        #
        # Update normalization model.
        #
        np.save(
            os.path.join(
                gs.CAPTURE_PATH,
                date,
                gs.EXTRINSIC_SETTINGS_FILENAME
            ),
            self._normalization.R
        )

    @cmd_callback
    @run_on_executor
    def handle_radiometric(
            self,
            date,
            time_index,
            residual_threshold,
            save,
            camera_settings):
        """Handle radiometric calibration"""

        #
        # Get almucantar file.
        #
        base_path = pkg_resources.resource_filename(
            'CameraNetwork',
            '../data/aeronet/{}/*.alm'.format(date.strftime("%Y_%m"))
        )
        path = glob.glob(base_path)
        if path == []:
            raise Exception(
                "No sunphotometer data for date: {}".format(
                    date.strftime("%Y-%m-%d")
                )
            )

        #
        # Parse the sunphotometer file.
        #
        df = spm.parseSunPhotoMeter(path[0])
        spm_df = df[date.strftime("%Y-%m-%d")]
        spm_df = [spm_df[spm_df["Wavelength(um)"] == wl] for wl in (0.6744, 0.5000, 0.4405)]

        #
        # Get the image list for this day.
        #
        cam_df = getImagesDF(date)

        #
        # Fit radiometric models.
        #
        models = []
        measurements = []
        estimations = []
        for i in range(3):
            t = spm_df[i].index[time_index]
            angles, values, samples = \
                self.sampleAlmucantarData(spm_df[i], t, cam_df, camera_settings)
            model = make_pipeline(
                PolynomialFeatures(degree=1),
                linear_model.RANSACRegressor(residual_threshold=residual_threshold)
            )
            model.fit(samples[:, i].reshape((-1, 1)), values)
            models.append(model)

            measurements.append(values)
            estimations.append(model.predict(samples[:, i].reshape((-1, 1))))

        #
        # Save the radiometric calibration.
        #
        ratios = [model.steps[1][1].estimator_.coef_[1] for model in models]
        if save:
            logging.info("Save radiometric calibration in home folder.")

            with open(gs.RADIOMETRIC_SETTINGS_PATH, 'wb') as f:
                cPickle.dump(dict(ratios=ratios), f)

            #
            # serial_num
            #
            if self.base_calibration_path is not None:
                logging.info("Save radiometric calibration in repo.")
                #
                # Store the radiometric data in the repo folder.
                #
                shutil.copyfile(
                    gs.RADIOMETRIC_SETTINGS_PATH,
                    os.path.join(self.base_calibration_path, gs.RADIOMETRIC_SETTINGS_FILENAME),
                )
            self._radiometric = RadiometricCalibration(ratios)

        #
        # Send back the analysis.
        #
        return angles, measurements, estimations, ratios

    def sampleAlmucantarData(self, spm_df, t, camera_df, camera_settings, resolution=301):
        """Samples almucantar rgb values of some camera at specific time."""

        angles, values = spm.readSunPhotoMeter(spm_df, t)
        closest_time = spm.findClosestImageTime(camera_df, t, hdr='2')
        img_datas, img = self.seekImageArray(
            camera_df,
            closest_time,
            hdr_index=-1,
            normalize=True,
            resolution=resolution,
            jpeg=False,
            camera_settings=camera_settings,
            correct_radiometric=False
        )
        almucantar_samples, almucantar_angles, almucantar_coords, \
        _, _, _ = spm.sampleImage(img, img_datas[0], almucantar_angles=angles)

        return angles, values, almucantar_samples

    @cmd_callback
    @gen.coroutine
    def handle_reset_camera(self):
        """Reset the camera. Hopefully help against bug in wrapper."""

        self.delete_camera()
        yield gen.sleep(gs.CAMERA_RESTART_PERIOD)
        self.start_camera()

    @cmd_callback
    @gen.coroutine
    def handle_restart(self):
        """Restart the software. We first release the camera."""

        logging.info("Deleting camera")
        self.delete_camera()
        yield gen.sleep(gs.CAMERA_RESTART_PERIOD)

    @cmd_callback
    @run_on_executor
    def handle_array(self, capture_settings, frames_num, normalize, jpeg,
                     resolution, img_data):

        #
        # Change camera to large size.
        # Note:
        # Nothing should be done in case the camera is already in large size.
        self._camera.large_size()

        #
        # Capture the array.
        #
        img_array, exposure_us, gain_db = self._camera.capture(
            capture_settings, frames_num)

        #
        # update image data object.
        #
        img_data.capture_time = datetime.utcnow()
        img_data.exposure_us = exposure_us
        img_data.gain_db = gain_db
        img_data.gain_boost = capture_settings[gs.GAIN_BOOST]
        img_data.color_mode = capture_settings[gs.COLOR_MODE]
        img_data.camera_info = self._camera.info

        #
        # Average the images.
        #
        if frames_num > 1:
            img_array = img_array.mean(axis=img_array.ndim - 1)
            logging.debug('Averaged %d arrays' % frames_num)

        #
        # Save the array and its data so that it can be later retrieved
        # using seek.
        #
        self.save_array(img_array, img_data, 0)

        #
        # Preprocess the array before sending it.
        #
        img_array = self.preprocess_array(
            [img_array],
            [img_data],
            img_data.capture_time,
            normalize,
            resolution,
            jpeg)

        return img_array, img_data

    def seekImageArray(
            self,
            df,
            seek_time,
            hdr_index,
            normalize,
            resolution,
            jpeg,
            camera_settings,
            correct_radiometric=True,
            ignore_date_extrinsic=False,
            timedelta_threshold=60
    ):
        """Seek an image array.

        Args:
            df (DataFrame): Pandas DataFrame holding all paths to images captured at
                some day. It is created using `CameraNetwork.utils.getImagesDF`
            seek_time (datetime): Time of required image.
            hdr_index (int): Index of hdr exposure. If <0 , then and HDR image will
                be returned.
            normalize (bool): Normalize the image.
            resolution (int): Resolution of the normalized image.
            jpeg (bool/int): Whether to return an array or compressed JPEG. If int,
                then it will be used as quality of the JPEG.
            camera_settings (DataObj): Object holding camera information.
            correct_radiometric (bool): Whether to apply radiometric correction.
                When calculating radiometric correction, it is important NOT to
                fix the measurements.
            ignore_date_extrinsic (bool, optional): Ignore the extrinsic calibration
                settings in the image folder (if exists).
            timedelta_threshold (int, optional): Allow for time delta between
                seeked time to returned index (in seconds).
        """

        logging.debug("Seeking time: {} and hdr: {}".format(seek_time, hdr_index))

        #
        # Convert the seeked time to Timestamp type.
        #
        original_seek_time = seek_time
        if type(seek_time) == str:
            seek_time = dtparser.parse(seek_time)

        if type(seek_time) == datetime:
            seek_time = pd.Timestamp(seek_time)

        if type(seek_time) != pd.Timestamp:
            raise ValueError("Cannot translate seek_time: {}}".format(
                original_seek_time))

        #
        # Get the closest time index.
        #
        checked_hdr = '0' if hdr_index < 0 else hdr_index
        dts = np.abs(df.xs(checked_hdr, level='hdr').index.to_pydatetime() - seek_time)

        if not (dts < timedelta(seconds=timedelta_threshold)).any():
            raise ValueError("Seeked time not available - seek_time: {}}".format(
                original_seek_time))

        seek_time = df.xs(checked_hdr, level='hdr').index[np.argmin(dts)]

        #
        # Either get a specific hdr index or all exposures.
        #
        if hdr_index < 0:
            mat_paths = df["path"].loc[seek_time].values.flatten()
        else:
            mat_paths = [df["path"].loc[seek_time, hdr_index]]

        img_arrays, img_datas = [], []
        for mat_path in mat_paths:
            print("Seeking: {}".format(mat_path))
            assert os.path.exists(mat_path), "Non existing array: {}".format(mat_path)
            img_array = sio.loadmat(mat_path)['img_array']

            base_path = os.path.splitext(mat_path)[0]
            if os.path.exists(base_path + '.json'):
                #
                # Support old json data files.
                #
                img_data = DataObj(
                    longitude=camera_settings[gs.CAMERA_LONGITUDE],
                    latitude=camera_settings[gs.CAMERA_LATITUDE],
                    altitude=camera_settings[gs.CAMERA_ALTITUDE],
                    name_time=seek_time.to_datetime()
                )

                data_path = base_path + '.json'
                with open(data_path, mode='rb') as f:
                    img_data.update(**json.load(f))

            elif os.path.exists(base_path + '.pkl'):
                #
                # New pickle data files.
                #
                with open(base_path + '.pkl', 'rb') as f:
                    img_data = cPickle.load(f)

            img_arrays.append(img_array)
            img_datas.append(img_data)

        img_array = self.preprocess_array(
            img_arrays,
            img_datas,
            seek_time,
            normalize,
            resolution,
            jpeg,
            correct_radiometric,
            ignore_date_extrinsic
        )

        return img_datas, img_array

    def preprocess_array(
            self,
            img_arrays,
            img_datas,
            img_time,
            normalize,
            resolution,
            jpeg=False,
            correct_radiometric=True,
            ignore_date_extrinsic=False
    ):
        """Apply preprocessing to the raw array:
        dark_image subtraction, normalization, vignetting, HDR...

        Args:
            ...
            jpeg (bool/int): Whether to return an array or compressed JPEG. If int,
                then it will be used as quality of the JPEG.
            correct_radiometric (bool): Whether to apply radiometric correction.
                When calculating radiometric correction, it is important NOT to
                fix the measurements.
            ignore_date_extrinsic (bool, optional): Ignore the extrinsic calibration
                settings in the image folder (if exists).

        Note:
            If multiple arrays/data are passed to the function, these are merged to
            an HDR image.
        """

        #
        # Check if there is a need to update the calibration settings.
        # Note:
        # This handles the case that the same server_id was used with
        # different cameras.
        #
        serial_num = img_datas[0].camera_info["serial_num"]
        capture_date = img_datas[0].capture_time
        self.loadCameraCalibration(capture_date=capture_date, serial_num=serial_num)

        #
        # Check if there a need to update the extrinsic calibration.
        #
        extrinsic_path = os.path.join(
            gs.CAPTURE_PATH,
            img_time.strftime("%Y_%m_%d"),
            gs.EXTRINSIC_SETTINGS_FILENAME
        )
        if not ignore_date_extrinsic and os.path.exists(extrinsic_path):
            try:
                self._normalization.R = np.load(extrinsic_path)
            except:
                logging.error(
                    "Failed loading extrinsic data from {}\n{}".format(
                        extrinsic_path, traceback.format_exc())
                )

        #
        # if raw image, subtract the dark image and apply vignetting.
        #
        if img_datas[0].color_mode == gs.COLOR_RAW and self._dark_images is not None:
            dark_images = self._dark_images[img_datas[0].gain_boost]
            tmp_arrays = []
            for img_array, img_data in zip(img_arrays, img_datas):
                dark_image = interpolate_dark_images(
                    img_data.exposure_us,
                    dark_images['exposures'],
                    dark_images['images'])

                logging.debug(
                    'Applying dark image, exposure: {} boost: {} shape: {}'.format(
                        img_data.exposure_us, img_data.gain_boost, dark_image.shape)
                )
                img_array = img_array.astype(np.float) - dark_image
                img_array[img_array < 0] = 0
                tmp_arrays.append(img_array)

            img_arrays = tmp_arrays

        #
        # Check the type of the jpeg argument. If it is int, handle it as quality.
        #
        if type(jpeg) is int:
            jpeg_quality = min(100, max(jpeg, gs.MIN_JPEG_QUALITY))
            jpeg = True
        else:
            jpeg_quality = gs.MIN_JPEG_QUALITY

        if jpeg:
            #
            # When sending jpeg, the image is not scaled by exposure.
            #
            img_array = img_arrays[0].astype(np.float)
        else:
            if len(img_arrays) == 1:
                img_array = \
                    img_arrays[0].astype(np.float) / (img_datas[0].exposure_us / 1000)
            else:
                img_exposures = [img_data.exposure_us / 1000 for img_data in img_datas]
                img_array = calcHDR(img_arrays, img_exposures)

        #
        # Apply vignetting.
        #
        logging.info('IMAGE SHAPE: {}'.format(img_array.shape))
        img_array = self._vignetting.applyVignetting(img_array)
        logging.info('IMAGE SHAPE: {}'.format(img_array.shape))

        #
        # Check if there is a need to normalize
        #
        if normalize and self._normalization is not None:
            if self._normalization.resolution != resolution:
                #
                # Recalculate normalization mapping for new resolution.
                #
                self._normalization.calc_normalization_map(resolution)

            img_array = self._normalization.normalize(img_array)

        if jpeg:
            #
            # Apply JPEG compression.
            # Note:
            # The jpeg stream is converted back to numpy array
            # to allow sending as matfile.
            #
            img_array = img_array.clip(0, 255)
            img = Image.fromarray(img_array.astype(np.uint8))
            f = StringIO.StringIO()
            img.save(f, format="JPEG", quality=jpeg_quality)
            img_array = np.fromstring(f.getvalue(), dtype=np.uint8)
        else:
            if correct_radiometric:
                #
                # Scale to Watts.
                #
                img_array = \
                    self._radiometric.applyRadiometric(img_array).astype(np.float32)

        return np.ascontiguousarray(img_array)

    @cmd_callback
    @run_on_executor
    def handle_dark_images(self):
        """Capturing dark images."""

        # Change camera back to large size.
        #
        self._camera.large_size()

        if not os.path.exists(gs.DARK_IMAGES_PATH):
            os.makedirs(gs.DARK_IMAGES_PATH)

        EXPOSURES = (
            10, 100, 500, 1000, 2000, 10000, 100000, 500000,
            1000000, 3000000, 5000000, 8000000
        )
        FRAMES_NUM = 10

        img_index = 0
        dark_images = {}
        for gain_boost in (False, True):
            for exp in EXPOSURES:
                #
                # Capture the array.
                #
                logging.debug(
                    "Capturing dark image exposure: {}, gain: {}".format(
                        exp, gain_boost))
                img_array, exposure_us, _ = self._camera.capture(
                    settings={
                        "exposure_us": exp,
                        "gain_db": 0,
                        "gain_boost": gain_boost,
                        "color_mode": gs.COLOR_RAW
                    },
                    frames_num=FRAMES_NUM
                )

                img_array = img_array.mean(axis=img_array.ndim - 1)

                sio.savemat(
                    os.path.join(gs.DARK_IMAGES_PATH, '{}_{}.mat'.format(img_index, gain_boost)),
                    {'image': img_array, 'exposure': exposure_us, 'gain_boost': gain_boost},
                    do_compression=True
                )
                img_index += 1

    @cmd_callback
    @run_on_executor
    def handle_loop(self, capture_settings, frames_num, hdr_mode, img_data):

        #
        # Change camera to large size.
        # Note:
        # Nothing should be done in case the camera is already in large size.
        self._camera.large_size()

        img_arrays = []
        img_datas = []
        capture_settings = capture_settings.copy()
        for hdr_i in range(hdr_mode):
            #
            # Capture the array.
            #
            img_array, exposure_us, gain_db = self.safe_capture(
                capture_settings, frames_num
            )

            #
            # update image data object.
            #
            img_data.capture_time = datetime.utcnow()
            img_data.exposure_us = exposure_us
            img_data.gain_db = gain_db
            img_data.gain_boost = capture_settings[gs.GAIN_BOOST]
            img_data.color_mode = capture_settings[gs.COLOR_MODE]
            img_data.camera_info = self._camera.info

            #
            # Average the images.
            #
            if frames_num > 1:
                img_array = img_array.mean(axis=img_array.ndim - 1)
                logging.debug('Averaged %d arrays' % frames_num)

            #
            #
            # Copy the array and its data for a later saving.
            #
            img_arrays.append(img_array)
            img_datas.append(copy.copy(img_data))

            if hdr_mode < 2:
                #
                # In some situations (calibration) exposure_us is None
                #
                break

            #
            # Multiply the next exposure for HDR.
            #
            if capture_settings['exposure_us'] >= 6000000:
                break

            capture_settings['exposure_us'] = capture_settings['exposure_us'] * 2

        mat_names = []
        jpg_names = []
        data_names = []
        for img_array, img_data, hdr_i in zip(img_arrays, img_datas, range(hdr_mode)):
            #
            # Save the array and its data.
            #
            mat_path, jpg_path, data_path = self.save_array(
                img_array, img_data, hdr_i)

            mat_names.append(mat_path)
            jpg_names.append(jpg_path)
            data_names.append(data_path)

        #
        # Send back the image.
        #
        return jpg_names, mat_names, data_names

    def save_array(self, img_array, img_data, hdr_i):

        #
        # Form file names.
        #
        _, base_path, base_name = name_time(img_data.name_time)

        if not os.path.isdir(base_path):
            os.makedirs(base_path)

        #
        # Save as mat
        #
        mat_path = '{base}_{i}.mat'.format(base=base_name, i=hdr_i)
        mat_path = os.path.join(base_path, mat_path)
        sio.savemat(
            mat_path,
            dict(
                img_array=img_array,
            ),
            do_compression=True
        )
        logging.debug('Saved mat file %s' % mat_path)

        #
        # Save as jpeg thumbnail
        #
        jpg_path = '{base}_{i}.jpg'.format(base=base_name, i=hdr_i)
        jpg_path = os.path.join(base_path, jpg_path)
        img = Image.fromarray(img_array.astype(np.uint8))
        img.thumbnail((400, 300), Image.ANTIALIAS)
        img.save(jpg_path)
        logging.debug('Saved jpg file %s' % jpg_path)

        #
        # Save the image data
        #
        data_path = '{base}_{i}.pkl'.format(base=base_name, i=hdr_i)
        data_path = os.path.join(base_path, data_path)
        with open(data_path, mode='wb') as f:
            cPickle.dump(img_data, f)

        logging.debug('Saved data file %s' % data_path)

        return mat_path, jpg_path, data_path

    @gen.coroutine
    def process_cmds(self):
        while True:
            #
            # Wait for a new cmd on the queue.
            #
            p, msg = yield self._in_queue.get()
            future, cmd, kwds = msg

            #
            # Call the corresponding callback.
            #
            cb = getattr(self, 'handle_{}'.format(cmd), None)
            if cb is None:
                logging.debug("Controller received unknown command: {}".format(cmd))
                future.set_exception(
                    Exception("Controller received unknown command: {}".format(cmd)))
            else:
                try:
                    try:
                        #
                        # Execute the command.
                        #
                        logging.debug("Processing cmd: {}, {}".format(cmd, kwds))
                        yield cb(future, **kwds)
                    except RestartException:
                        self.delete_camera()
                        raise
                except Exception as e:
                    logging.error('Error while processing a callback:\n{}'.format(
                        traceback.format_exc()))
                    future.set_exc_info(sys.exc_info())
                    future.set_exception(e)

            self._in_queue.task_done()
            logging.debug("Finished processing cmd: {}".format(cmd))
