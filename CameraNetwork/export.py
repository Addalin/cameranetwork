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
"""Utilites for handling export to solver.
"""
from __future__ import division
from CameraNetwork.utils import sun_direction
import cPickle
import cv2
from enaml.application import deferred_call, is_main_thread
import logging
import math
import os
import numpy as np
import pymap3d
import traceback


def exportToShdom(
    base_path,
    array_items,
    grid,
    lat,
    lon,
    alt,
    progress_callback):
    """Process export of reconstruction data on separate thread.
    The export is intended for use in SHDOM.

    Args:
        base_path (str): Path to store export data.
        array_items (list): List of array items.
        grid (list): List of grid array. This is the grid to reconstruct.
        lat, lon, alt (float): The latitude, longitude and altitude of the center
            of the grid.
        progress_callback (function): Callback function to update the (GUI) with
            the progress of the export.

    Note:
        The directions in the Qt view are as follows:
        x axis (horizontal) goes from West (left) to East (right)
        y axis (vertical) goes from South (down) to North (up).
        this makes it a EN axis system
    """

    #
    # Reset the progress indicatort.
    #
    progress_cnt = len(array_items)
    deferred_call(progress_callback, 0)

    #
    # Convert the grid from NED to ECEF
    #
    GRID = np.meshgrid(*grid)
    ecef_grid = pymap3d.ned2ecef(GRID[0], GRID[1], GRID[2], lat, lon, alt)

    #
    # Loop on all servers and collect information.
    #
    export_data = {}
    for i, (server_id, (array_model, array_view)) in enumerate(array_items.items()):
        try:
            #
            # Store extra data like camera center, etc.
            #
            extra_data, sun_alt, sun_az = extraReconstructionData(
                array_model, array_view, lat0=lat, lon0=lon, h0=alt)

            img_array = array_model.img_array

            #
            # Calculate azimuth and elevation of each pixel.
            # Note:
            # The interpolation is done in the Y_shdom, X_shdom to avoid
            # the seam artifact of PHI at 180 degrees.
            #
            Y_shdom, X_shdom = np.meshgrid(
                np.linspace(-1, 1, array_model.img_array.shape[1]),
                np.linspace(-1, 1, array_model.img_array.shape[0])
            )
            Y_shdom = array_view.image_widget.getArrayRegion(Y_shdom)
            X_shdom = array_view.image_widget.getArrayRegion(X_shdom)
            PHI_shdom, PSI_shdom = getShdomDirections(Y_shdom, X_shdom, array_model.fov)

            #
            # Calculate Masks.
            # Note:
            # sunshader mask is calculate using grabcut. This is used for removing the
            # sunshader.
            # Manual mask is the (ROI) mask marked by the user.
            # sun mask is a mask the blocks the sun.
            #
            manual_mask = array_view.image_widget.mask
            joint_mask = (manual_mask * array_model.sunshader_mask).astype(np.uint8)

            #
            # Project the grid on the image and check viewed voxels.
            # Note:
            # This measurement is used for checking how many cameras see each voxel.
            # TODO:
            # This procedure is time expensive and can be cached.
            # This should probably be a method of the camera, and this method should
            # cache the result, or even be triggered by setting the grid.
            #
            visibility = projectGridOnCamera(ecef_grid, array_model, joint_mask)
        except Exception, e:
            logging.error(
                "Server {} ignored due to exception:\n{}".format(
                    server_id,
                    traceback.format_exc()
                )
            )
            continue

        export_data[server_id] = dict(
            extra_data=extra_data,
            R=array_view.image_widget.getArrayRegion(img_array[..., 0]),
            G=array_view.image_widget.getArrayRegion(img_array[..., 1]),
            B=array_view.image_widget.getArrayRegion(img_array[..., 2]),
            PHI=PHI_shdom,
            PSI=PSI_shdom,
            MASK=array_view.image_widget.getArrayRegion(joint_mask),
            SUN_MASK=array_view.image_widget.getArrayRegion(array_model.sun_mask),
            Visibility=visibility,
        )

        deferred_call(progress_callback, i / progress_cnt)

    #
    # Save the results.
    #
    with open(os.path.join(base_path, 'export_data.pkl'), 'wb') as f:
        cPickle.dump(export_data, f)

    deferred_call(progress_callback, 0)


def getShdomDirections(Y_shdom, X_shdom, fov=math.pi/2):
    """Calculate the (SHDOM) direction of each pixel.

    Directions are calculated in SHDOM convention where the direction is
    of the photons.
    """

    PHI_shdom = np.pi + np.arctan2(Y_shdom, X_shdom)
    PSI_shdom = -np.pi + fov * np.sqrt(X_shdom**2 + Y_shdom**2)
    return PHI_shdom, PSI_shdom


def extraReconstructionData(array_model, array_view, lat0, lon0, h0):
    """Get extra data for the reconstruction

    This includes camera position, sun angle, time etc.

    Note:
        The coordinates are given in the following conventions:
        1) Camera position is given in NEU.
        2) sun_mu, sun_az are given in the SHDOM convention
           of photons directions.
    """

    #
    # Calculate the center of the camera.
    # Note that the coords are stored as NEU (in contrast to NED)
    #
    n, e, d = pymap3d.geodetic2ned(
        array_model.latitude, array_model.longitude, array_model.altitude,
        lat0=lat0, lon0=lon0, h0=h0)

    #
    # Calculate bounding coords (useful for debug visualization)
    #
    #bounding_phi, bounding_psi = calcROIbounds(array_model, array_view)

    #
    # Sun azimuth and altitude
    #
    sun_alt, sun_az = sun_direction(
        latitude=str(array_model.latitude),
        longitude=str(array_model.longitude),
        altitude=array_model.altitude,
        at_time=array_model.img_data.name_time)

    #
    # Note:
    # shdom_mu = cos(pi/2-alt-pi)=cos(-alt-pi/2)=cos(alt+pi/2)
    #
    extra_data = \
        dict(
            at_time=array_model.img_data.name_time,
            sun_mu=math.cos(float(sun_alt)+np.pi/2),
            sun_az=float(sun_az)-np.pi,
            x=n,
            y=e,
            z=-d,
            #bounding_phi=bounding_phi,
            #bounding_psi=bounding_psi
        )
    return extra_data, sun_alt, sun_az


def projectGridOnCamera(ecef_grid, array_model, joint_mask):
    """Project reconstruction grid on camera.

    This is used to estimate the visibility of each voxel by the camera.
    """

    xs, ys, fov_mask = array_model.projectECEF(ecef_grid, filter_fov=False)
    xs = xs.astype(np.uint32).flatten()
    ys = ys.astype(np.uint32).flatten()

    grid_visibility = np.zeros_like(xs, dtype=np.uint8)
    grid_visibility[fov_mask] = \
        joint_mask[ys[fov_mask], xs[fov_mask]].astype(np.uint8)

    return grid_visibility.reshape(*ecef_grid[0].shape)


def calcROIbounds(array_model, array_view):
    """Calculate bounds of ROI in array_view

    Useful for debug visualization.
    """

    #
    # Get the ROI size
    #
    size = array_model.ROI_state['size']

    #
    # Get the transform from the ROI to the data.
    #
    _, tr = roi.getArraySlice(array_model.img_array, array_view.image_widget.img_item)

    #
    # Calculate the bounds.
    #
    center = float(array_model.img_array.shape[0])/2
    pts = np.array(
        [tr.map(x, y) for x, y in \
         ((0, 0), (size.x(), 0), (0, size.y()), (size.x(), size.y()))]
    )
    pts = (pts - center) / center
    X, Y = pts[:, 1], pts[:, 0]
    bounding_phi = np.arctan2(X, Y)
    bounding_psi = array_model.fov * np.sqrt(X**2 + Y**2)

    return bounding_phi, bounding_psi


