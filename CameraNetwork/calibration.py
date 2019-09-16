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
Utilities used in the process of calibration.
"""

from __future__ import print_function
from __future__ import division
from CameraNetwork.image_utils import raw2RGB, RGB2raw
import cPickle
import os
import numpy as np
from sklearn.linear_model import RANSACRegressor
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import FunctionTransformer
from sklearn.preprocessing import PolynomialFeatures

__all__ = (
    'Gimbal',
    'GimbalCamera',
    'findSpot'
)

READY_REPLY = 'READY'


class Gimbal(object):
    """Calibration Gimbal

    This class encapsulates the use of the Calibration Gimbal. During
    vignetting calibration, a camera is connected to the gimbal and placed
    in front of a light source. The Gimbal rotates the camera in all directions.
    This way the spatial response of the camera (vignetting) is captured.

    Args:
        com (str, optional): The serial port of the Arduino that controls the
            gimbal.
        baudrate (int, optional): Baud rate of serial port.
        timeout (int, optional): timeout for trying to connect to the Arduino.

    Note:
        To use this class, one needs to first install the gimbal.ino file
        on the Arduino. The file is located in:

            <ROOT FOLDER>/arduino/gimbal/gimba.ino
    """

    def __init__(self, com='COM13', baudrate=9600, timeout=20):

        import serial
        self._port = serial.Serial(com, baudrate=baudrate, timeout=timeout)

        #
        # Wait for the motor to finish the reset move
        #
        self._waitReady()

    def _finalize(self):
        """Finalize the serial port
        """

        #
        # Finalize the serial port.
        #
        try:
            print(self._port.read(size=1000))
            self._port.close()
        except:
            pass

    def _waitReady(self):
        """Wait for a ready reply from the arduino
        """

        rep = self._port.readline()
        if rep.strip() != READY_REPLY:
            raise Exception('Did not get ready reply from the arduino')

    def __del__(self):
        """
        Finalize the serial port
        """

        self._finalize()

    def _checkEcho(self, cmd):
        """Check the answer echo validity.

        Args:
            cmd (str): Original command.
        """

        echo = self._port.readline()
        #
        # Check that the echo is correct
        #
        if cmd.strip() != echo.strip():
            raise Exception(
                'Echo error. cmd: "' + cmd.strip() + '" ans: "' + echo.strip() + '"'
                )

    def _sendCmd(self, cmd):
        """Send a command and test its echo.

        cmd (str): Command to send to the Arduino

        """

        self._port.write(cmd)

        self._checkEcho(cmd)
        #self._waitReady()

    def flush(self):
        """Empty the input & output buffers
        """

        #
        # Empty the input & output buffer.
        #
        self._port.flushInput()
        self._port.flushOutput()

    def resetPosition(self):
        """Move to root position
        """

        cmd = 'z\n'
        self._sendCmd(cmd)

    def move(self, x, y):
        """Move to x, y position

        Args:
            x, y (int): Corresponding positions of the x, y servo motors.

        Note:
            The servo motors support angles in the range 0-180.
        """

        x = int(x)
        y = int(y)

        if x < 0 or y < 0 or x > 9999 or y > 9999:
            raise Exception('Invalid coordinates: %d, %d' % (x, y))

        #
        # Create the command
        #
        x_str = ('0000' + str(x))[-4:]
        y_str = ('0000' + str(y))[-4:]
        cmd = 'm'+x_str+y_str+'\n'

        self._sendCmd(cmd)


class GimbalCamera(object):
    def __init__(self):
        import ids

        self._cam = ids.Camera(nummem=1)

        self._cam.auto_white_balance = False
        self._cam.continuous_capture = False
        self._cam.auto_exposure = False
        self._cam.exposure = 100
        self._cam.color_mode = ids.ids_core.COLOR_BAYER_8
        self._cam.gain_boost = False
        self._cam.gain_db = 0

    def measure(self, exposure=40, gain_db=0, average=10):
        required_framerate = 1e3/exposure
        self._cam.framerate = required_framerate

        self._cam.exposure = exposure
        self._cam.gain_db = gain_db

        self._cam.continuous_capture = True
        _, _ = self._cam.next()

        imgs = []
        for i in range(average):
            img_array, meta_data = self._cam.next()
            imgs.append(img_array)

        self._cam.continuous_capture = False

        return np.round(np.mean(imgs, axis=0), 0).astype(np.int32)


def meanColor(c):
    """Calculate mean of nnz values in array.

    Args:
        c (array): Array for which to calculate mean.

    Returns:
        Mean of non zero values in c.
    """
    nnz_total = (c > 0).sum()
    if nnz_total == 0:
        return 0

    return c.sum()/nnz_total


def findSpot(img, threshold=5):
    """Calculate spot value in image.

    Returns (height, x, y, width_x, width_y)
    the gaussian parameters of a 2D distribution by calculating its
    moments"""

    import cv2

    #
    # Mask threshold
    #
    img_median = img.max()/2
    if img_median < threshold:
        raise Exception('Median too small: {}'.format(img_median))

    #
    # Calculate a spot mask.
    #
    kernel = np.ones((3, 3),np.uint8)
    mask = (img > threshold)
    mask = cv2.dilate(mask.astype(np.uint8), kernel)
    mask = cv2.erode(mask.astype(np.uint8), kernel, iterations=2)

    #
    # Image values at spot.
    #
    img_filt = img.astype(np.float) * mask
    total = img_filt.sum()
    if total == 0:
        raise Exception('Empty Image')

    #
    # Calc the first momentum (center) of the spot
    #
    Y, X = np.indices(img.shape)
    x = (X*img_filt).sum()/total
    y = (Y*img_filt).sum()/total

    return int(x), int(y), [meanColor(c) for c in raw2RGB(img_filt)]


class VignettingCalibration():
    """Apply vignetting calibration.

    Note:
    The vignetting is applied to raw (1200x1600) images. Nonetheless the
    models are learnt on rgb (600x800) images.
    """

    COLORS = ['red', 'green', 'blue']

    def __init__(
        self,
        models=None,
        polynomial_degree=4,
        residual_threshold=20,
        min_samples=300
        ):

        if models is None:
            self._models = [
                make_pipeline(
                    PolynomialFeatures(degree=polynomial_degree),
                    RANSACRegressor(
                        residual_threshold=residual_threshold,
                        min_samples=min_samples
                    )
                    ) for i in range(3)
            ]
        else:
            self._models = models

        self._inliers = []
        self._stds = []
        self._errs = []

        #
        # By default the vignetting is identity.
        #
        self._ratio = np.ones((1200, 1600))

    def calibrate(self, color_measurements):
        """Calculate vignetting calibration."""

        for color_index, measurements in enumerate(color_measurements):
            #
            # Arrange the data.
            #
            x, y, z = [np.array(a) for a in zip(*measurements)]
            X = np.hstack([coord.reshape(-1, 1) for coord in (x, y)])

            #
            # Train the model
            #
            model = self._models[color_index]
            model.fit(X, z)

            #
            # Calculate the fitting error.
            #
            self._inliers.append(model.steps[1][1].inlier_mask_)
            z_estim = model.predict(X).reshape(y.shape)

            self._errs.append(z-z_estim)
            self._stds.append(np.std(self._errs[-1][self._inliers[-1]]))

        self._calcRatio()

    def _calcRatio(self):
        """Calc the vignetting ratios in each pixel of the image."""

        #
        # The models were learnt for RGB (600x800) images therefore
        # the sizes of the grids are set accordingly.
        #
        ygrid, xgrid = np.mgrid[0:600, 0:800]
        grid = np.hstack([coord.reshape(-1, 1) for coord in (xgrid, ygrid)])

        ratios = []
        for model in self._models:
            z = model.predict(grid).reshape(ygrid.shape)
            ratios.append(z/z.max())

        #
        # The vignetting is applied to raw (1200x1600) images.
        #
        self._ratio = RGB2raw(*ratios)

        #
        # For "Numerical stability" (i.e. exploding img radiance values at the
        # extreme of the image) the minimal ratio limited to 0.1.
        #
        self._ratio = np.clip(self._ratio, 0.1, 1)

    def applyVignetting(self, raw_img, dtype=None):
        """Apply vignetting to an image."""

        if dtype is None:
            #
            # By default the output dtype is the same as the input.
            #
            dtype = raw_img.dtype

        return (raw_img.astype(np.float) / self._ratio).astype(dtype)

    def save(self, file_path):
        """Save the model."""

        with open(file_path, 'wb') as f:
            cPickle.dump(
                dict(
                    models=self._models,
                ), f
            )

    @staticmethod
    def processSpotImages(base_path, color_index=None):
        """Process a set of mat file images.

        The images should contain light spots at different locations.
        """

        if color_index is None:
            colors = slice(3)
        else:
            colors = slice(color_index, color_index+1)

        path = os.path.join(base_path, "*.mat")
        imgs_paths = glob.glob(path)
        measurements = []
        for img_path in imgs_paths:
            img = sio.loadmat(img_path)["img"]
            try:
                measurements.append(
                    [findSpot(c) for c in raw2RGB(img)[colors]]
                )
            except:
                pass

        #
        # Arrange the measurements as a list of colors
        #
        measurements = zip(*measurements)

        return measurements

    @staticmethod
    def readMeasurements(base_path, *args, **kwds):
        """Read measurements and create a new Vignetting object.
        """

        measurements = []
        for color_index, color in enumerate(VignettingCalibration.COLORS):
            #
            # Read the measurements
            #
            path = os.path.join(base_path, 'measurements_{}.pkl'.format(color))
            if not os.path.exists(path):
                #
                # The measurements are not split to colors.
                #
                path = os.path.join(base_path, 'measurements.pkl')

            with open(path, 'rb') as f:
                data = cPickle.load(f)

            #
            # Arrange the data.
            # Note:
            # the radiometric model is fit on a 800 by 600 image. The measurements
            # are calculated on 1600 by 1200 image. So there is a need to divide the
            # x,y coords by 2.
            #
            x, y, vals = zip(*data)
            measurements.append(
                [(i/2, j/2, k[color_index]) for i, j, k in zip(x, y, vals) if i is not None]
            )

        vc = VignettingCalibration(*args, **kwds)
        vc.calibrate(measurements)

        return vc

    @staticmethod
    def load(file_path):
        """Load model from path."""

        with open(file_path, 'rb') as f:
            data = cPickle.load(f)

        obj = VignettingCalibration(models=data['models'])
        obj._calcRatio()

        return obj

    @property
    def ratio(self):
        if self._ratio is None:
            self._calcRatio()

        return self._ratio


class RadiometricCalibration():
    """Apply radiometric calibration.

    Note:
    The radiometric is applied to rgb images.
    """

    def __init__(self, ratios=None):

        if ratios is None:
            self._ratios = np.ones(3).reshape(1, 1, 3)
        else:
            self._ratios = np.array(ratios).reshape(1, 1, 3)

    @staticmethod
    def load(file_path):
        """Load model from path."""

        with open(file_path, 'rb') as f:
            data = cPickle.load(f)

        obj = RadiometricCalibration(ratios=data['ratios'])

        return obj

    def applyRadiometric(self, img, dtype=None):
        """Apply radiometric calibration to an image."""

        assert img.ndim == 3, \
               "Radiometric calibration should be applied to RGB images."

        if dtype is None:
            #
            # By default the output dtype is the same as the input.
            #
            dtype = img.dtype

        return (img.astype(np.float) * self._ratios).astype(dtype)
