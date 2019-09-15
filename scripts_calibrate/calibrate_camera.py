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

"""Intrinsic and Vignetting calibration

This scripts does calibration of the camera. There are three steps:
1) Geometric (intrinsic) calibration.
2) Black image caputre. This is used only for the vignetting calibration step.
3) Vignetting calibration.

The results are stored in the repository under a folder named according to
the camera serial number. After successful run, the results should be added,
commited and pushed into the repository.
"""
from __future__ import print_function
from __future__ import division
import CameraNetwork
from CameraNetwork.calibration import VignettingCalibration
from CameraNetwork.cameras import IDSCamera
from CameraNetwork import Gimbal, findSpot
from CameraNetwork.image_utils import raw2RGB
import CameraNetwork.global_settings as gs
from datetime import datetime
import logging
import numpy as np
import matplotlib.pyplot as plt
import cv2
import time
import cPickle
import glob
import winsound
import ids
import sys
import os
import pkg_resources
import scipy.io as sio
import fisheye
from CameraNetwork.image_utils import FisheyeProxy, Normalization
import itertools
import traceback
import cPickle


#
# Geometric calibration
#
DO_GEOMETRIC_CALIBRATION = True
NX, NY = 9, 6
GEOMETRIC_EXPOSURE = 500000
GEOMETRIC_STEPS = 12
GEOMETRIC_XMIN, GEOMETRIC_XMAX = 40, 140
GEOMETRIC_YMIN, GEOMETRIC_YMAX = 40, 140
GEOMETRIC_SLEEP_TIME = 4
CHESSBOARD_DETECTION_THRESHOLD = 20
SHOW_REPROJECTION = True

#
# Black image capture.
#
DO_BLACK_IMG = True

#
# Vignetting calibration.
#
VIGNETTING_STEPS = 24
VIGNETTING_EXPOSURE = 60000
LED_POWER = {"BLUE(470)": 35, "GREEN(505)": 100, "RED(625)": 50}
VIGNETTING_SLEEP_TIME = 1


def safe_mkdirs(path):
    """Safely create path, warn in case of race."""

    if not os.path.exists(path):
        try:
            os.makedirs(path)
        except OSError as e:
            import errno
            if e.errno == errno.EEXIST:
                warnings.warn(
                    "Failed creating path: {path}, probably a race".format(path=path)
                )


def capture_callback(img, exp, gain):
    """Debug plot of the image."""

    cv2.imshow("image", img)
    cv2.waitKey(1)


def main():
    """Run the calibration of the cameras."""

    #
    # Initialize the calibration setup.
    #
    CameraNetwork.initialize_logger(log_level=logging.DEBUG)

    import oceanoptics
    spec = oceanoptics.get_a_random_spectrometer()

    p = Gimbal(com="COM3")

    #
    # Put here a break point if you want to adjust the focus.
    # Note: You will have to stop the program to be able to turn on the IDS cockpit.
    #
    p.move(90, 90)

    cv2.namedWindow("image", flags=cv2.WINDOW_AUTOSIZE)
    cam = IDSCamera(callback=capture_callback)

    #
    # All the results of the calibration are stored on disk.
    #
    date_sign = datetime.now().strftime("%Y_%m_%d")
    results_path = os.path.join('vignetting_calibration', cam.info['serial_num'], date_sign)
    safe_mkdirs(results_path)
    safe_mkdirs(os.path.join(results_path, 'images'))

    #
    # Calibration data is stored in repo.
    #
    data_path = pkg_resources.resource_filename(__name__, '../data/calibration/')
    data_path = os.path.join(data_path, cam.info['serial_num'], date_sign)
    safe_mkdirs(data_path)

    #
    # Capture settings.
    #
    settings = {
        "exposure_us": GEOMETRIC_EXPOSURE,
        "gain_db": 0,
        "gain_boost": False,
        "color_mode": gs.COLOR_RGB
    }

    #
    # Store the exposure time
    #
    with open(os.path.join(results_path, 'settings.pkl'), 'wb') as f:
        cPickle.dump(settings, f)

    ############################################################################
    # Perform geometric Calibration
    ############################################################################
    if DO_GEOMETRIC_CALIBRATION:
        #imgs = sorted(glob.glob(os.path.join(results_path, 'geometric/*.jpg')))
        #imgs = [cv2.imread(img) for img in imgs]
        safe_mkdirs(os.path.join(results_path, 'geometric'))

        X_grid, Y_grid = np.meshgrid(
            np.linspace(GEOMETRIC_XMIN, GEOMETRIC_XMAX, GEOMETRIC_STEPS),
            np.linspace(GEOMETRIC_YMIN, GEOMETRIC_YMAX, GEOMETRIC_STEPS),
            indexing='xy')
        X_grid = X_grid.astype(np.int32)
        Y_grid = Y_grid.astype(np.int32)

        imgs = []
        img_index = 0
        raw_input("Put the chessboard and press any key")
        for _ in range(5):
            for (x, y) in zip(
                np.random.choice(X_grid.ravel(), size=20),
                np.random.choice(Y_grid.ravel(), size=20)
                ):
                logging.debug("Moved gimbal to position: ({})".format((int(x), int(y))))
                p.move(int(x), int(y))
                time.sleep(GEOMETRIC_SLEEP_TIME)

                img, _, _ = cam.capture(settings, frames_num=1)

                #
                # Save image for debuging the calibration process.
                #
                cv2.imwrite(
                    os.path.join(results_path, 'geometric', 'img_{:03}.jpg'.format(img_index)),
                    img
                )

                img_index += 1
                imgs.append(img)

        #
        # Use the fisheye model
        #
        fe = fisheye.FishEye(nx=NX, ny=NY, verbose=True)

        rms, K, D, rvecs, tvecs, mask = fe.calibrate(
            imgs=imgs,
            show_imgs=True,
            calibration_flags=cv2.fisheye.CALIB_RECOMPUTE_EXTRINSIC+cv2.fisheye.CALIB_FIX_SKEW,
            return_mask=True
        )

        if SHOW_REPROJECTION:
            cv2.namedWindow("Reprojected img", cv2.WINDOW_AUTOSIZE)
            vec_cnt = 0
            safe_mkdirs(os.path.join(results_path, 'reprojection'))
            for img_index, m in enumerate(mask):
                if not m:
                    continue
                rep_img = cv2.drawChessboardCorners(
                    imgs[img_index].copy(),
                    (NX, NY),
                    fe.projectPoints(
                        rvec=rvecs[vec_cnt],
                        tvec=tvecs[vec_cnt]
                        ),
                    1
                )
                cv2.imshow("Reprojected img", rep_img)
                cv2.waitKey(500)
                cv2.imwrite(
                    os.path.join(results_path, 'reprojection', 'img_{:03}.jpg'.format(img_index)),
                    rep_img
                )
                vec_cnt += 1

            cv2.destroyAllWindows()

        normalization = Normalization(1001, FisheyeProxy(fe))
        img_normalized = normalization.normalize(imgs[0])
        plt.imshow(img_normalized, cmap='gray')
        plt.show()

        #
        # Save the geometric calibration
        #
        fe.save(os.path.join(results_path, 'fisheye.pkl'))
        fe.save(os.path.join(data_path, 'fisheye.pkl'))
    else:
        fe = fisheye.load_model(os.path.join(results_path, 'fisheye.pkl'))

    settings["color_mode"] = gs.COLOR_RAW
    settings["exposure_us"] = VIGNETTING_EXPOSURE

    ############################################################################
    # Measure dark noise
    ############################################################################
    if DO_BLACK_IMG:
        p.move(90, 90)
        time.sleep(GEOMETRIC_SLEEP_TIME)
        raw_input("Turn off the lights and press any key")
        winsound.Beep(5000, 500)

        black_img = np.mean(cam.capture(settings, frames_num=10)[0], axis=2)
        winsound.Beep(5000, 500)
        np.save(os.path.join(results_path, 'black_img.npy'), black_img)
    else:
        black_img = np.load(os.path.join(results_path, 'black_img.npy'))

    #
    # Print the COLIBRI LED POWER
    #
    raw_input("Set COLIBRI LEDS: {}, and press any key.".format(
        [(k, v) for k, v in LED_POWER.items()]))

    ############################################################################
    # Measure the spectrum
    ############################################################################
    spec.integration_time(0.3)
    measurements = []
    for i in range(10):
        s = spec.spectrum()
        measurements.append(s[1])

    with open(os.path.join(results_path, 'spec.pkl'), 'wb') as f:
        cPickle.dump((s[0], np.mean(measurements, axis=0)), f)
    with open(os.path.join(data_path, 'spec.pkl'), 'wb') as f:
        cPickle.dump((s[0], np.mean(measurements, axis=0)), f)

    #
    # Verify no saturation.
    #
    p.move(90, 90)
    time.sleep(GEOMETRIC_SLEEP_TIME)
    img, exp, gain = cam.capture(settings, frames_num=1)
    if img.max() == 255:
        raise Exception('Saturation')

    print("Maximal color values: {}".format([c.max() for c in raw2RGB(img)]))

    ############################################################################
    # Perform Vignetting calibration.
    ############################################################################
    X_grid, Y_grid = np.meshgrid(
        np.linspace(0, 180, VIGNETTING_STEPS),
        np.linspace(0, 180, VIGNETTING_STEPS),
        indexing='xy')
    X_grid = X_grid.astype(np.int32)
    Y_grid = Y_grid.astype(np.int32)

    measurements = []
    for i, (x, y) in enumerate(zip(X_grid.ravel(), Y_grid.ravel())):
        sys.stdout.write('x={}, y={}...\t'.format(x, y))

        p.move(x, y)
        time.sleep(VIGNETTING_SLEEP_TIME)
        img = np.mean(cam.capture(settings, frames_num=10)[0], axis=2)
        winsound.Beep(8000, 500)
        try:
            measurement = findSpot(np.clip(img-black_img, 0, 255))
        except:
            print('FAIL')
            print(traceback.format_exc())
            measurement = None, None, None

        measurements.append(measurement)
        print(measurement)

        #
        # Store the measurement image.
        #
        img_path = os.path.join(results_path, 'images', 'img_{:03}.mat'.format(i))
        sio.savemat(img_path, {'img':img}, do_compression=True)

    img = np.zeros(shape=(1200, 1600, 3))

    for x, y, val in measurements:
        if val is None:
            continue
        img[y, x, ...] = val

    #
    # Save the results
    #
    sio.savemat(os.path.join(results_path, 'results.mat'), {'img':img}, do_compression=True)
    with open(os.path.join(results_path, 'measurements.pkl'), 'wb') as f:
        cPickle.dump(measurements, f)

    #
    # Calculate Vignetting correction.
    #
    vc = VignettingCalibration.readMeasurements(results_path)
    vc.save(os.path.join(results_path, gs.VIGNETTING_SETTINGS_FILENAME))
    vc.save(os.path.join(data_path, gs.VIGNETTING_SETTINGS_FILENAME))
    print("The STD Vignetting Error per color is: {}".format(vc._stds))

    #
    # Visualize the vingetting
    #
    normalization = Normalization(
        gs.DEFAULT_NORMALIZATION_SIZE, FisheyeProxy(fe)
    )
    d = normalization.normalize(np.dstack(raw2RGB(vc.ratio)))
    plt.imshow(d)
    plt.show()
    sio.savemat(
        os.path.join(results_path, "vignetting_norm.mat"),
        dict(img=d),
        do_compression=True
    )
    cv2.destroyAllWindows()


if __name__ == '__main__':
    main()
