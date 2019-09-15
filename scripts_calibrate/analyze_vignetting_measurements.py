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

from __future__ import print_function
from __future__ import division
import numpy as np
from numpy.polynomial import polynomial
from mayavi import mlab
import matplotlib.pyplot as plt
import cv2
import time
from mpl_toolkits.mplot3d import Axes3D
import scipy.interpolate as interp
from skimage.morphology import convex_hull_image
import fisheye
from CameraNetwork.image_utils import FisheyeProxy, Normalization
import cPickle
import os
from sklearn import linear_model
from sklearn.neighbors import KNeighborsRegressor
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import PolynomialFeatures
import math
import itertools

COLORS = ('blue', 'green', 'red')
COLOR_INDICES = {'blue': 2, 'green': 1, 'red': 0}


def skFit(base_path, x, y, z, img_shape):
    model = make_pipeline(
        PolynomialFeatures(2),
        linear_model.RANSACRegressor(random_state=0, residual_threshold=5)
    )

    #
    # Interpolate a second order polynomial
    #
    X = np.hstack([coord.reshape(-1, 1) for coord in (x, y)])
    model.fit(X, z)

    #
    # Visualize the error
    #
    ygrid, xgrid = np.mgrid[0:img_shape[0]:10, 0:img_shape[1]:10]
    grid = np.hstack([coord.reshape(-1, 1) for coord in (xgrid, ygrid)])

    zgrid = model.predict(grid).reshape(ygrid.shape)

    fig = plt.figure()
    ax = fig.add_subplot(111, projection='3d')
    ax.plot_surface(xgrid, ygrid, zgrid)
    ax.scatter(x, y, z)


    z_estim = model.predict(X).reshape(y.shape)
    fig = plt.figure()
    ax = fig.add_subplot(111, projection='3d')
    ax.scatter(x, y, z-z_estim)

    plt.show()

    #
    # Visualize the error in the normalized image.
    #
    z_err = np.abs(z-z_estim)

    img_out = np.ones(shape=img_shape) * 100

    for threshold in np.logspace(-2, 2, 20)[::-1]:
        print(threshold)
        img_tmp = np.zeros(shape=img_shape)
        indices = z_err < threshold
        if not np.any(indices):
            break
        img_tmp[y[indices], x[indices]] = threshold
        chull = convex_hull_image(img_tmp)

        img_out[chull] = threshold

    plt.figure()
    plt.imshow(img_out)
    plt.colorbar()

    fe = fisheye.load_model(os.path.join(base_path, 'fisheye.pkl'))
    normalization = Normalization(1001, FisheyeProxy(fe))
    img_normalized = normalization.normalize(img_out)

    plt.figure()
    plt.imshow(img_normalized)
    plt.colorbar()
    plt.show()


def main(base_path):
    #
    # Load the measurements
    #
    base_path1 = r'vignetting_calibration\4102820388'

    color = 'blue'
    color_index = COLOR_INDICES[color]

    with open(os.path.join(base_path1, 'measurements_{}.pkl'.format(color)), 'rb') as f:
        measurements1 = cPickle.load(f)
    with open(os.path.join(base_path1, 'spec_{}.pkl'.format(color)), 'rb') as f:
        spec1 = cPickle.load(f)

    x1, y1, z1 = [np.array(a) for a in zip(*measurements1)]

    plt.figure()
    for c in COLORS:
        plt.plot(spec1[0], spec1[1], label='camera1')
        plt.legend()
    plt.show()

    for c in COLORS:
        mlab.figure(bgcolor=(1, 1, 1), )
        mlab.points3d(x1, y1, z1[..., COLOR_INDICES[c]], mode='sphere', scale_mode='none', scale_factor=5, color=(0, 0, 1))
        mlab.outline(color=(0, 0, 0), extent=(0, 1600, 0, 1200, 0, 255))
        mlab.title(c, color=(0, 0, 0))
    mlab.show()

    img_rgb = np.zeros(shape=(1200, 1600, 3))
    for x, y, val in measurements1:
        img_rgb[y, x, ...] = val

    img = img_rgb[..., 2][1::2, 1::2]
    y, x = np.nonzero(img)
    z = img[np.nonzero(img)]

    skFit(base_path, x, y, z, img.shape)
