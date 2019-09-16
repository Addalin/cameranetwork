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
import cv2
import matplotlib.mlab as ml
import numpy as np
import pymap3d
import os
import platform

def loadMapData():
    """Load height data for map visualization."""

    relpath = os.path.dirname(os.path.realpath(__file__))
    path1 = os.path.abspath(os.path.join(relpath, r'..', r'data', r'reconstructions', r'N32E034.hgt'))
    path2 = os.path.abspath(os.path.join(relpath, r'..', r'data', r'reconstructions', r'N32E035.hgt'))
    path3 = os.path.abspath(os.path.join(relpath, r'..', r'data', r'reconstructions', r'haifa_map.jpg'))
    

    with open(path1) as hgt_data:
        hgt1 = np.fromfile(hgt_data, np.dtype('>i2')).reshape((1201, 1201))[:1200, :1200]
    with open(path2) as hgt_data:
        hgt2 = np.fromfile(hgt_data, np.dtype('>i2')).reshape((1201, 1201))[:1200, :1200]
    hgt = np.hstack((hgt1, hgt2)).astype(np.float32)
    lon, lat = np.meshgrid(np.linspace(34, 36, 2400, endpoint=False), np.linspace(32, 33, 1200, endpoint=False)[::-1])

    map_texture = cv2.cvtColor(cv2.imread(path3), cv2.COLOR_BGR2RGB)

    return \
           lat[100:400, 1100:1400], lon[100:400, 1100:1400], \
           hgt[100:400, 1100:1400], map_texture[100:400, 1100:1400, ...]


def calcSeaMask(hgt_array):
    """Calc a masking to the sea.

    Note:
        This code is uses empirical magic number, and should be adjusted if
        grid sizes change.
    """

    hgt_u8 = (255 * (hgt_array - hgt_array.min()) / (hgt_array.max() - hgt_array.min())).astype(np.uint8)

    mask = (hgt_u8 > 7).astype(np.uint8)*255
    kernel_open = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel_open)
    kernel_close = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (16, 16))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel_close)
    mask[250:, 250:] = 255

    return mask < 255


def convertMapData(lat, lon, hgt, map_texture, lat0=32.775776, lon0=35.024963, alt0=229):
    """Convert lat/lon/height data to grid data."""

    n, e, d = pymap3d.geodetic2ned(
        lat, lon, hgt,
        lat0=lat0, lon0=lon0, h0=alt0)

    x, y, z = e, n, -d

    xi = np.linspace(-10000, 10000, 300)
    yi = np.linspace(-10000, 10000, 300)
    X, Y = np.meshgrid(xi, yi)

    Z = ml.griddata(y.flatten(), x.flatten(), z.flatten(), yi, xi, interp='linear')
    R = ml.griddata(y.flatten(), x.flatten(), map_texture[..., 0].flatten(), yi, xi, interp='linear')
    G = ml.griddata(y.flatten(), x.flatten(), map_texture[..., 1].flatten(), yi, xi, interp='linear')
    B = ml.griddata(y.flatten(), x.flatten(), map_texture[..., 2].flatten(), yi, xi, interp='linear')

    Z_mask = calcSeaMask(Z)

    return X, Y, Z, Z_mask, np.dstack((R, G, B))