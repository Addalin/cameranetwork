from __future__ import division
from CameraNetwork.image_utils import FisheyeProxy
from CameraNetwork.image_utils import Normalization
from CameraNetwork.calibration import RadiometricCalibration
from CameraNetwork.calibration import VignettingCalibration
from CameraNetwork.cameras import IDSCamera
import CameraNetwork.global_settings as gs
from CameraNetwork.image_utils import calcHDR
from CameraNetwork.arduino_utils import ArduinoAPI
from CameraNetwork.utils import cmd_callback
from CameraNetwork.utils import find_camera_orientation_ransac
from CameraNetwork.utils import find_centroid
from CameraNetwork.utils import mean_with_outliers
from CameraNetwork.utils import name_time
from CameraNetwork.utils import object_direction
from CameraNetwork.utils import RestartException
import copy
import cPickle
import cv2
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
import Queue
from scipy import signal
import scipy.io as sio
import shutil
from sklearn import linear_model
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import PolynomialFeatures
import subprocess
import sys
import time
import thread
from tornado import gen
from tornado.concurrent import Future
from tornado.concurrent import run_on_executor
from tornado.queues import PriorityQueue as tPriorityQueue
import traceback
from zmq.eventloop import ioloop


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

    seconds = (dt.hour*60 + dt.minute)*60 + dt.second + dt.microsecond*1e-6
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
        self._offline = offline

        #
        # Try to load calibration data.
        #
        if os.path.exists(gs.CALIBRATION_SETTINGS_PATH):
            self._fe = fisheye.load_model(
                gs.CALIBRATION_SETTINGS_PATH, calib_img_shape=(1200, 1600))

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
        # Load todays celestial position measurements
        #
        if not os.path.exists(gs.SUN_POSITIONS_PATH):
            os.makedirs(gs.SUN_POSITIONS_PATH)
        else:
            #
            # Check past measurements.
            #
            past_measurements_paths = sorted(
                glob.glob(os.path.join(gs.SUN_POSITIONS_PATH, '*.csv')))

            if past_measurements_paths:
                angles = []
                for path in past_measurements_paths[-2:]:
                    print path
                    data = pd.read_csv(path, index_col=0, parse_dates=True)

                    #
                    # Limit the data to sun measurements only.
                    #
                    data = data[data['object']=='Sun']

                    #
                    # Limit the data to angles between a range of "valid"
                    # angles.
                    #
                    data = data[
                        (data['sunshader_angle']>gs.SUNSHADER_MIN_MEASURED)&\
                        (data['sunshader_angle']<gs.SUNSHADER_MAX_MEASURED)
                    ]

                    data.index = data.index.time
                    angles.append(data['sunshader_angle'])

                self.sunshader_angles_df = pd.concat(
                    angles, axis=1
                ).mean(axis=1).to_frame(name='angle')
            else:
                self.sunshader_angles_df = pd.DataFrame(dict(angle=[]))

        self.sunshader_angle_model = make_pipeline(
            PolynomialFeatures(2),
            linear_model.RANSACRegressor(random_state=0, residual_threshold=5)
        )

        #
        # Load dark images.
        #
        di_paths = sorted(glob.glob(os.path.join(gs.DARK_IMAGES_PATH, '*.mat')))
        if di_paths:
            self._dark_images = {
                False:{'exposures':[], 'images':[]},
                True:{'exposures':[], 'images':[]},
            }

            #
            # Load the dark images from disk
            #
            for path in di_paths:
                d = sio.loadmat(path)
                gain_boost =  d['gain_boost'][0][0] == 1
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

        #
        # Load vignetting settings.
        #
        try:
            self._vignetting = VignettingCalibration.load(gs.VIGNETTING_PATH)
        except:
            self._vignetting = VignettingCalibration()
            logging.error(
                "Failed loading vignetting data:\n{}".format(
                    traceback.format_exc()))

        #
        # Load radiometric calibration.
        #
        try:
            self._radiometric = RadiometricCalibration.load(gs.RADIOMETRIC_PATH)
        except:
            self._radiometric = RadiometricCalibration()
            logging.error(
                "Failed loading radiometric data:\n{}".format(
                    traceback.format_exc()))

        #
        # Set the last sunshader scan to "old" time.
        #
        self.last_sunshader_time = None
        self.sunshader_fit = False

        #
        # Sky mask
        #
        if os.path.exists(gs.MASK_PATH):
            self.sky_mask_base = sio.loadmat(gs.MASK_PATH)['mask_base']
        else:
            self.sky_mask_base = None

    def __del__(self):
        self.delete_camera()

    @property
    def cmd_queue(self):
        return self._in_queue

    def start(self):
        #
        # Start the loop of reading commands of the cmd queue.
        #
        ioloop.IOLoop.current().spawn_callback(self.process_cmds)

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

        captured = False
        retries = max_retries
        while not captured:
            try:
                img_array, real_exposure_us, real_gain_db = \
                    self._camera.capture(settings, frames_num)
                captured = True
            except Exception, e:
                if retries <= 0:
                    logging.exception(
                        'The camera failed too many consequtive times. Reboot.'
                    )
                    logging.shutdown()
                    os.system('sudo reboot')

                retries -= 1
                logging.error(
                    "The camera raised an Exception:\n{}".format(
                        traceback.format_exc()
                    )
                )

                self.delete_camera()
                time.sleep(gs.CAMERA_RESTART_PERIOD)
                self.start_camera()

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
            dt = timedelta(seconds=2*gs.SUNSHADER_SCAN_PERIOD_LONG)

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
                current_angle-gs.SUNSHADER_SCAN_DELTA_ANGLE, sunshader_min
            )
            sunshader_scan_max = min(
                current_angle+gs.SUNSHADER_SCAN_DELTA_ANGLE, sunshader_max
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
        if  dt > timedelta(seconds=sunshader_scan_period):
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
            # Update datebase with new measurment
            # First, add new measurment to dataframe of angles.
            #
            if measured_angle > gs.SUNSHADER_MIN_MEASURED and \
               measured_angle < gs.SUNSHADER_MAX_MEASURED:
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
        # If model fitting failed or there are not enough measurments for
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
            val = img[img>128].sum() / img.size
            print e, g, img.max(), img.min(), val
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

        N = min(8, int((len(saturated_array)-1)/3)-1)
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
            return angles, np.array(saturated_array), sun_signal, \
                measured_angle, centroid

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
        r = mat[...,0]
        g = mat[...,1]
        b = mat[...,2]
        new_mask = (b>30) & (b>1.5*r)

        #
        # Accomulate the mask factor
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
        self.sky_mask = cv2.drawContours(
            self.sky_mask, [contour], -1, 255, -1)

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
            self._arduino_api.setAngle(sunshader_min+2)
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
        self._fe.save(gs.CALIBRATION_SETTINGS_PATH)

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

        if angle<sunshader_min or angle>sunshader_max:
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
    def handle_extrinsic(self, date, save):
        """Handle extrinsic calibration"""

        #
        # Load sun measurments.
        #
        today_positions_path = os.path.join(
            gs.SUN_POSITIONS_PATH, "{}.csv".format(date))

        if not os.path.exists(today_positions_path):
            raise Exception('No sun positions for date: {}'.format(date))

        #
        # Calibratoin is done using the sun position.
        #
        positions_df = pd.read_csv(today_positions_path, index_col=0, parse_dates=True)
        positions_df = positions_df[positions_df['object']=='Sun']
        positions_df = positions_df.dropna()

        if positions_df.shape[0] < gs.EXTRINSIC_CALIBRATION_MIN_PTS:
            raise Exception('No enough sun positions: {}'.format(
                positions_df.shape[0]))

        #
        # Convert sun measurments to directions.
        #
        measured_postions = \
            positions_df[['pos_x', 'pos_y']].as_matrix()
        phi, theta, mask = self._fe.undistortDirections(
            measured_postions)

        measured_directions = np.array(
            (
                np.sin(theta)*np.cos(phi),
                -np.sin(theta)*np.sin(phi),
                np.cos(theta)
            )
        ).T

        #
        # Calculated direction (using the ephem package.)
        #
        calculated_directions = []
        for date in positions_df.index:
            calculated_directions.append(object_direction(celestial_class=ephem.Sun, date=date))
        calculated_directions = np.array(calculated_directions)

        #
        # Estimate oreintation
        #
        R, rotated_directions = find_camera_orientation_ransac(
            calculated_directions, measured_directions)

        #
        # Update normalization model.
        #
        self._normalization.R = R
        np.save(gs.EXTRINSIC_SETTINGS_PATH, R)

        #
        # Send back the analysis.
        #
        return rotated_directions, calculated_directions, R

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
    def handle_thumbnail(self, settings, normalize):

        #
        # Set the camera to small size.
        #
        self._camera.small_size()

        #
        # Capture the thumbnail.
        #
        img_array, exposure_us, gain_db = self._camera.capture(settings)
        np.save('/home/odroid/tmp.npy', img_array)

        print exposure_us, gain_db, [(img_array[..., i]==255).sum() for i in range(3)]

        #
        # Change camera back to large size.
        #
        self._camera.large_size()

        #
        # Check if there is a need to normalize
        #
        if normalize and self._normalization is not None:
            img_array = self._normalization.normalize(img_array)

        #
        # Send back the image.
        #
        return img_array, exposure_us, gain_db

    @cmd_callback
    @run_on_executor
    def handle_array(self, capture_settings, frames_num, normalize, resolution,
                     img_data):

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
            img_array = img_array.mean(axis=img_array.ndim-1)
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
            [img_array], [img_data], normalize, resolution)

        return img_array, img_data

    def preprocess_array(self, img_arrays, img_datas, normalize, resolution):
        """Apply preprocessing to the raw array:
        dark_image substraction, normalization, vignetting, HDR...

        Note:
        If multiple arrays/data are passed to the function, these are merged to
        an HDR image.
        """

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

        if len(img_arrays) == 1:
            img_array = \
                img_arrays[0].astype(np.float) / (img_datas[0].exposure_us / 1000)
        else:
            img_exposures = [img_data.exposure_us / 1000 for img_data in img_datas]
            img_array = calcHDR(img_arrays, img_exposures)

        #
        # Apply vignetting.
        #
        img_array = self._vignetting.applyVignetting(img_array)

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

        #
        # Scale to Watts.
        #
        img_array = self._radiometric.applyRadiometric(img_array)

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

                img_array = img_array.mean(axis=img_array.ndim-1)

                sio.savemat(
                    os.path.join(gs.DARK_IMAGES_PATH, '{}_{}.mat'.format(img_index, gain_boost)),
                    {'image': img_array, 'exposure': exposure_us, 'gain_boost':gain_boost},
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
                img_array = img_array.mean(axis=img_array.ndim-1)
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
            # Mutliply the next exposure for HDR.
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
                logging.debug("Controller received unkown command: {}".format(cmd))
                future.set_exception(
                    Exception("Controller received unkown command: {}".format(cmd)))
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
            logging.debug("Finished procesing cmd: {}".format(cmd))