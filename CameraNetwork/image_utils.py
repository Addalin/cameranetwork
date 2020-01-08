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
General utilities for image processing.
"""
from __future__ import division, absolute_import, print_function
from CameraNetwork.utils import obj
import cv2
import logging
import numpy as np
import pymap3d

try:
    import futures
except:
    #
    # Support also python 2.7
    #
    from concurrent import futures

from tornado import gen
from tornado.concurrent import Future
from tornado.concurrent import run_on_executor


def calcHDR(img_arrays, img_exposures, low_limit=20, high_limit=230):
    """Calculate an HDR image.

    Args:
        img_arrays (list of images): List of uint8 images.
        img_exposures (list of floats): Corresponding exposures (preferably in
            ms).
        The images are expected to be sorted from shortest to longest exposure.
        low_limit (float[optional]): low RGB value below which values are
            ignored except in the longest exposure.
        high_limit (float[optional]): high RGB value above which values are
            ignored except in the shortest exposure.

    Returns:
        HDR image merged from the image arrays. There idea is that the shortest
        exposure is used for the high RGB values, and the longest for the low
        values. The other values are averaged from all images. The HDR image
        returned is float in units of [RGB/ms]

    """

    hdr_imgs = []
    for i, (img, exp) in enumerate(zip(img_arrays, img_exposures)):
        hdr_img = img.copy().astype(np.float) / exp
        if i == 0:
            mask_min, mask_max = low_limit, 255
        elif i == len(img_arrays)-1:
            mask_min, mask_max = 0, high_limit
        else:
            mask_min, mask_max = low_limit, high_limit

        mask = (img < mask_min) | (img > mask_max)
        hdr_img[mask] = np.nan
        hdr_imgs.append(hdr_img)

    return np.nanmean(hdr_imgs, axis=0)


def raw2RGB(img, dtype=None):
    """Convert a Raw image to its three RGB channels."""

    if dtype is None:
        dtype = img.dtype

    R = np.ascontiguousarray(img[::2, ::2])
    B = np.ascontiguousarray(img[1::2, 1::2])
    G1 = np.ascontiguousarray(img[1::2, 0::2]).astype(np.float)
    G2 = np.ascontiguousarray(img[0::2, 1::2]).astype(np.float)

    return R, ((G1+G2)/2).astype(R.dtype), B


def RGB2raw(R, G, B):
    """Convert RGB channels to Raw image."""

    h, w = R.shape
    raw = np.empty(shape=(2*h, 2*w), dtype=R.dtype)

    raw[::2, ::2] = R
    raw[1::2, 1::2] = B
    raw[1::2, 0::2] = G
    raw[0::2, 1::2] = G

    return raw


def str2array(string):
    """
    Convert a string of numbers to a numpy array.
    """

    return np.fromstring(string.strip(), sep=' ')


def load_ocam_model(calib_path):
    """
    Read the OCamCalib calibration data from a text file.
    """

    ocam_model = obj()

    with open(calib_path, 'r') as f:
        lines = f.readlines()

        ocam_model.pol = np.poly1d(str2array(lines[2])[:0:-1])
        ocam_model.invpol = np.poly1d(str2array(lines[6])[:0:-1])
        ocam_model.center = str2array(lines[10])
        ocam_model.affine = str2array(lines[14])
        ocam_model.img_shape = tuple(str2array(lines[18]).astype(np.int))

    return ocam_model


def cam2world(points2D, ocam_model):
    """
    Convert y, x camera positions to a x,y,z directions on the unit hemisphere.

    Parameters
    ----------
    points2D - [n, 2] array

    """

    points2D.shape = (-1, 2)

    xc = ocam_model.center[0]
    yc = ocam_model.center[1]
    c = ocam_model.affine[0]
    d = ocam_model.affine[1]
    e = ocam_model.affine[2]

    A = np.array(((c, e), (d, 1)))

    points25D = points2D - np.array(((xc, yc),))
    points25D = np.dot(points25D, np.linalg.inv(A).T)

    r = np.linalg.norm(points25D, axis=1).reshape(-1, 1)
    zp  = ocam_model.pol(r)

    points3D = np.hstack((points25D, zp))
    points3D = points3D / np.linalg.norm(points3D, axis=1).reshape(-1, 1)

    return points3D


def world2cam(points3D, ocam_model):
    """
    Convert x,y,z directions on the unit hemisphere to y, x camera positions.
    """

    points3D.shape = (-1, 3)

    xc = ocam_model.center[0]
    yc = ocam_model.center[1]
    c = ocam_model.affine[0]
    d = ocam_model.affine[1]
    e = ocam_model.affine[2]

    A = np.array(((c, e), (d, 1)))

    norm = np.linalg.norm(points3D[:, :2], axis=1)
    norm[norm == 0] = np.finfo(norm.dtype).eps
    thetas = np.arctan(points3D[:,2]/norm)
    rho = ocam_model.invpol(thetas)

    points2D = points3D[:, :2]/norm.reshape((-1, 1))*rho.reshape((-1, 1))
    points2D = np.dot(points2D, A) + np.array(((xc, yc),))

    return points2D


class FisheyeProxy(object):
    """Fisheye proxy class

    A wrapper that enables using the OcamCalib model and OpenCV fisheye model
    interchangeably.
    """
    def __init__(self, model):

        self._model = model

        #
        # Check type of fisheye model
        #
        self._ocamcalib_flag = type(model) == obj

    def __getattr__(self, name):
        """Delegate the attribute enquiry to the model"""

        return getattr(self._model, name)

    def projectPoints(self, XYZ):
        """Project 3D points on the 2D image"""

        if self._ocamcalib_flag:
            XYZ_ = np.empty_like(XYZ)
            XYZ_[:, 0] = XYZ[:, 1]
            XYZ_[:, 1] = XYZ[:, 0]
            XYZ_[:, 2] = -XYZ[:, 2]
            YXmap = world2cam(XYZ_, self._model)
        else:
            YXmap = self._model.projectPoints(XYZ)[..., ::-1]

        return YXmap


    def undistortDirections(self, distorted):
        """Undistorts 2D points using fisheye model.

        Args:
            distorted (array): nx2 array of distorted image coords (x, y).

        Retruns:
            Phi, Theta (array): Phi and Theta undistorted directions.
        """

        if self._ocamcalib_flag:
            #
            # Note:
            # mask is not used in the code that calls this function so I don't
            # implement it here in this code.
            #
            points3D = cam2world(distorted[:, ::-1], self._model)
            phi = np.arctan2(points3D[:, 1], points3D[:, 0])
            theta = np.arccos(-points3D[:, 2])
            mask = None
        else:
            phi, theta, mask = self._model.undistortDirections(distorted)

        return phi, theta, mask


class Normalization(object):
    """Normalized Image Class

    This class encapsulates the conversion between captured image and
    the normalized image.
    """

    def __init__(
        self,
        resolution,
        fisheye_model,
        Rot=np.eye(3),
        fov=np.pi/2
        ):

        self._fisheye_model = fisheye_model
        self._Rot = Rot
        self.fov = fov

        self.calc_normalization_map(resolution)

    def calc_normalization_map(self, resolution):
        """Calculate normalization map

        Calc mapping from original image to normalized image.
        """

        logging.debug('Calculating normalization map.')
        self.resolution = resolution

        #
        # Create a grid of directions.
        # The coordinates create a 'linear' fisheye, where the distance
        # from the center ranges between 0-pi/2 linearly.
        #
        X, Y = np.meshgrid(
            np.linspace(-1, 1, self.resolution),
            np.linspace(-1, 1, self.resolution)
        )

        self._PHI = np.arctan2(Y, X)
        self._PSI = self.fov * np.sqrt(X**2 + Y**2)
        self.mask = self._PSI <= self.fov
        self._PSI[~self.mask] = self.fov

        z = np.cos(self._PSI)
        x = np.sin(self._PSI) * np.cos(self._PHI)
        y = np.sin(self._PSI) * np.sin(self._PHI)

        self.XYZ = np.hstack((x.reshape(-1, 1), y.reshape(-1, 1), z.reshape(-1, 1)))

        self.update_rotation()

    def update_rotation(self):
        #
        # Rot is the rotation applied to the camera coordinates to turn into world coordinates.
        # So I multiply from the right (like multiplying with the inverse of the matrix).
        #
        logging.info(self._Rot)
        XYZ_rotated = np.dot(self.XYZ, self._Rot)
        YXmap = self._fisheye_model.projectPoints(XYZ_rotated)

        self._Xmap = np.array(YXmap[:, 1], dtype=np.float32).reshape((self.resolution, self.resolution))
        self._Ymap = np.array(YXmap[:, 0], dtype=np.float32).reshape((self.resolution, self.resolution))
        self._Xmap[~self.mask] = -1
        self._Ymap[~self.mask] = -1

    @property
    def R(self):
        return self._Rot

    @R.setter
    def R(self, R):
        if (self._Rot is not None) and np.array_equal(R, self._Rot):
            return

        self._Rot = R
        self.update_rotation()

    def normalize(self, img):
        """Normalize Image

        Apply normalization to image.
        Note:
        The fisheye model is adapted to the size of the image, therefore
        different sized images (1200x1600 or 600x800) can be normalized.
        """

        import cv2

        logging.debug('Applying normalization to image.')
        self._tight_mask = self.mask.copy()

        if img is None:
            return np.zeros((self.resolution, self.resolution, 3))

        #
        # 2D images are assumed to be RAW, and converted to RGB
        #
        if img.ndim == 2:
            logging.debug('Converting grey image to RGB')
            img = np.dstack(raw2RGB(img))

        logging.debug('Normalizing shape: {}'.format(img.shape))

        #
        # Check if there is need to scale the images
        #
        calib_shape = self._fisheye_model.img_shape
        if img.shape[:2] != calib_shape:
            x_scale = float(img.shape[1]) / float(calib_shape[1])
            y_scale = float(img.shape[0]) / float(calib_shape[0])
            Xmap = self._Xmap * x_scale
            Ymap = self._Ymap * y_scale
        else:
            Xmap = self._Xmap
            Ymap = self._Ymap

        img_dtype = img.dtype
        BORDER_MAP_VALUE = 100000
        normalized_img = cv2.remap(
            img.astype(np.float),
            Xmap,
            Ymap,
            cv2.INTER_AREA,
            borderMode=cv2.BORDER_CONSTANT,
            borderValue=BORDER_MAP_VALUE
        )

        if normalized_img.ndim == 2:
            self._tight_mask[normalized_img>(BORDER_MAP_VALUE-1)] = 0
        else:
            self._tight_mask[normalized_img[:,:, 0]>(BORDER_MAP_VALUE-1)] = 0

        #
        # Erod one pixel from the tight mask
        #
        self._tight_mask = cv2.erode(
            self._tight_mask.astype(np.uint8),
            kernel=np.ones((3, 3), dtype=np.uint8)
            ).astype(np.bool)

        #
        # TODO: Implement radiometric correction compare with RadiometricCalibration.applyRadiometric() in calibration.py
        #
        #normalized_img = radiometric_correction(normalized_img, self._radiometric_model).astype(np.uint8)
        normalized_img = normalized_img.astype(img_dtype)
        normalized_img[self._tight_mask==0] = 0

        return normalized_img

    def normalCoords2Direction(self, coords):
        """Convert Normal Coord to Direction Vector

        Convert a coordinate (even sub pixel) of normalized image to a
        direction vector in space.

        Args:
            coords (tuple): normalized coordinates (x, y), supports subpixels.
        """

        coords = np.array(coords).reshape(-1, 2)
        XY = coords/self.resolution*2 - 1
        #
        # Create a grid of directions.
        #
        X, Y = XY[..., 0], XY[..., 1]

        PHI = np.arctan2(Y, X)
        PSI = self.fov * np.sqrt(X**2 + Y**2)
        mask = PSI <= self.fov
        PSI[~mask] = self.fov

        z = np.cos(PSI)
        x = np.sin(PSI) * np.cos(PHI)
        y = np.sin(PSI) * np.sin(PHI)

        XYZ = np.hstack((x.reshape(-1, 1), y.reshape(-1, 1), z.reshape(-1, 1)))

        #
        # Rot is the rotation applied to the camera coordinates to turn into world coordinates.
        # So I multiply from the right (like multiplying with the inverse of the matrix).
        #
        XYZ_rotated = np.dot(XYZ, self._Rot)

        return XYZ_rotated

    def distorted2Directions(self, x, y):
        """Convert distorted pixels to directions."""

        X = np.hstack((x.reshape(-1, 1), y.reshape(-1, 1)))
        phi, theta = self._fisheye_model.undistortDirections(X)

        return phi, theta

    @property
    def normal_coords(self):
        """Normalized grid"""

        return self._Xmap, self._Ymap

    @property
    def normal_angles(self):
        """Normalized grid"""

        return self._PHI, self._PSI

    @property
    def tight_mask(self):
        """
        Return a tight mask of the normalized image. The tight mask takes into account the cropping by the sensor edges.
        """

        return self._tight_mask


def calcSunshaderMask(
    array_model,
    img_array,
    grabcut_threshold,
    dilate_size,
    values_range=40
    ):
    """Calculate a mask for the sunshader.

    Calculate a mask for the pixels covered by the sunshader.
    Uses the grabcut algorithm.

    Args:
        img_array (array): Image (float HDR).
        grabcut_threshold (float): Threshold used to set the seed for the
            background.
        dilate_size (int): Size of the dilate kernel.
        values_range (float): This value is used for normalizing the image.
            It is an empirical number that works for HDR images captured
            during the day.

    Note:
        The algorithm uses some "Magic" numbers that might need to be
        adapted to different lighting levels.
    """

    from enaml.application import deferred_call

    #
    # Apply the grabcut algorithm.
    #
    sunshader_mask = np.ones(img_array.shape[:2], np.uint8)*cv2.GC_PR_FGD
    sunshader_mask[img_array.max(axis=2) < grabcut_threshold] = cv2.GC_PR_BGD
    img_u8 = (255 * np.clip(img_array, 0, values_range) / values_range).astype(np.uint8)
    bgdModel = np.zeros((1, 65), np.float64)
    fgdModel = np.zeros((1, 65), np.float64)
    rect = (0, 0, 0, 0)
    try:
        cv2.grabCut(img_u8, sunshader_mask, rect, bgdModel, fgdModel, 5, cv2.GC_INIT_WITH_MASK)
        sunshader_mask = np.where(
            (sunshader_mask==cv2.GC_FGD) | (sunshader_mask==cv2.GC_PR_FGD),
            1,
            0).astype('uint8')
    except Exception, e:
        logging.error("Failed to calculate grabcut sunshader.")
        sunshader_mask = np.ones(img_array.shape[:2], np.uint8)*cv2.GC_PR_FGD

    #
    # Dilate the mask.
    # Note:
    # The actual action is ersion, as the mask is inversion of the sunshader.
    #
    if dilate_size > 1:
        kernel = cv2.getStructuringElement(
            cv2.MORPH_ELLIPSE,
            (dilate_size, dilate_size)
        )
        sunshader_mask = cv2.erode(sunshader_mask, kernel)

    #
    # Send the results back to the GUI thread.
    #
    deferred_call(setattr, array_model, 'sunshader_mask', sunshader_mask)


def projectECEFThread(
    array_model,
    GRID_ECEF,
    weights_shape
    ):
    """Project the ECEF grid on the camera axis.

    Args:
        img_array (array): Image (float HDR).
        GRID_ECEF (array): Grid to project.
        weights_shape (tuple): Shape of the camera image.
    """

    from enaml.application import deferred_call

    xs, ys, _ = array_model.projectECEF(
        GRID_ECEF,
        filter_fov=False
    )
    grid_2D = np.array((ys, xs)).T.astype(np.int)

    #
    # Map points outside the fov to 0, 0.
    #
    h, w = weights_shape
    grid_2D[grid_2D<0] = 0
    grid_2D[grid_2D[:, 0]>=h] = 0
    grid_2D[grid_2D[:, 1]>=w] = 0

    #
    # Send the results back to the GUI thread.
    #
    deferred_call(setattr, array_model, 'grid_2D', grid_2D)


def gaussian(center_x, center_y, height=1., width_x=0.25, width_y=0.25):
    """Returns a gaussian function with the given parameters"""

    return lambda x, y: height*np.exp(-(((center_x-x)/width_x)**2+((center_y-y)/width_y)**2)/2)


def calcSunMask(img_shape, sun_alt, sun_az, radius=0.25):
    """Calculate a mask for the sun.

    The sun pixels are weighted by a gaussian.
    """

    sun_r = (np.pi/2 - sun_alt) / (np.pi/2)
    sun_x = sun_r * np.sin(sun_az)
    sun_y = sun_r * np.cos(sun_az)

    X, Y = np.meshgrid(
        np.linspace(-1, 1, img_shape[1]),
        np.linspace(-1, 1, img_shape[0])
    )
    gau = gaussian(sun_x, sun_y, width_x=radius, width_y=radius)(X, Y)
    gau /= gau.max()

    sun_mask = 1 - gau

    return sun_mask


def projectGrid(
    array_model,
    grid_3D,
    weights_shape
    ):
    """Project the ECEF grid on the camera axis.

    Args:
        img_array (array): Image (float HDR).
        grid_3D (array): Grid to project.
        weights_shape (tuple): Shape of the camera image.
    """

    xs, ys, _ = array_model.projectECEF(
        grid_3D,
        filter_fov=False
    )
    grid_2D = np.array((ys, xs)).T.astype(np.int)

    #
    # Map points outside the fov to 0, 0.
    #
    h, w = weights_shape
    grid_2D[grid_2D<0] = 0
    grid_2D[grid_2D[:, 0]>=h] = 0
    grid_2D[grid_2D[:, 1]>=w] = 0

    #
    # Send the results back to the GUI thread.
    #
    return grid_2D


class VisualHull(object):
    #
    # Thread pull
    #
    executor = futures.ThreadPoolExecutor(4)

    @gen.coroutine
    def calc_visual_hull(
        self,
        result_callback,
        main_model,
        use_color_consistency,
        color_consistency_sigma,
        cloud_threshold,
        perturbations=10):
        """Calculate the space carving cloud grid."""

        from enaml.application import deferred_call

        #
        # Match array_models and array_views.
        #
        grid_scores = []
        grid_masks = []
        cloud_rgb = []

        #
        # Filter non exported views.
        #
        exported_views = []
        for array_view in main_model.arrays.array_views.values():
            if not array_view.export_flag.checked:
                logging.info(
                    "Space Carving: Camera {} ignored.".format(array_view.server_id)
                )
                continue
            exported_views.append(array_view)

        #
        # Run the space carving on all views using an process pool.
        #
        results = yield [
            self.space_carve_view(array_view, main_model, perturbations)
            for array_view in exported_views
        ]
        grid_scores, grid_masks, cloud_rgb = list(zip(*results))

        if len(grid_scores) == 0:
            #
            # No participating cameras.
            #
            return

        #
        # Calculate the collective clouds weight.
        #
        weights = np.array(grid_scores).prod(axis=0)
        # TODO: Consider to change the prod to other arithmethic calculation. (maybe OR ?) (I think this removes clouds edges from the space carving)
        #
        # voxels that are not seen (outside the fov/sun_mask) by at least two cameras
        # are zeroed.
        #
        grid_masks = np.array(grid_masks).sum(axis=0)
        weights[grid_masks<2] = 0

        #
        # More cameras viewing the voxel increase it's own score.
        #
        nzi = weights > 0
        weights[nzi] = weights[nzi]**(1/grid_masks[nzi])

        #
        # Calculate color consistency as described in the article. (see Equation 5.5 in p.54 , Amit's thesis)
        #
        std_rgb = np.dstack(cloud_rgb).std(axis=2).mean(axis=1)
        mean_rgb = np.dstack(cloud_rgb).mean(axis=2).mean(axis=1)
        color_consistency = np.exp(-(std_rgb/(mean_rgb + np.finfo(float).eps))/color_consistency_sigma)
        #
        # Take into account both the clouds weights and photo consistency.
        #
        if use_color_consistency:
            weights = color_consistency * weights

        #
        # Hack to get the ECEF grid in NED coords.
        #
        X, _, _ = np.meshgrid(*main_model.GRID_NED)

        clouds_score = weights.reshape(*X.shape)
        clouds_score = clouds_score[..., ::-1]

        deferred_call(result_callback, clouds_score, cloud_threshold)

    @run_on_executor
    def space_carve_view(self, array_view, main_model, perturbations):
        """Do the space carving of a view on a separate process."""

        logging.info(
            "Space Carving: Processing camera {}.".format(array_view.server_id)
        )

        #
        # Collect cloud weights from participating cameras.
        #
        server_id = array_view.server_id
        array_model = main_model.arrays.array_items[server_id]

        #
        # Get the masks.
        #
        manual_mask = array_view.image_widget.mask
        joint_mask = (manual_mask * array_model.sunshader_mask).astype(np.uint8)

        #
        # Get the masked grid scores for the specific camera.
        #
        mask_inds = joint_mask == 1
        grid_score = np.ones_like(array_model.cloud_weights)
        grid_score[mask_inds] = array_model.cloud_weights[mask_inds]
        weights_shape = array_model.cloud_weights.shape

        grid_scores_tmp = []
        grid_masks_tmp = []
        cloud_rgb_tmp = []

        for _ in range(perturbations):
            X, Y, Z = np.meshgrid(*main_model.GRID_NED)
            X = X + main_model.delx*np.random.random(size=X.shape)
            Y = Y + main_model.dely*np.random.random(size=Y.shape)
            Z = Z - main_model.delz*np.random.random(size=Z.shape)
            grid_3D = pymap3d.ned2ecef(
                X, Y, Z,
                main_model.latitude,
                main_model.longitude,
                main_model.altitude
            )

            grid_2D = projectGrid(array_model, grid_3D, weights_shape)

            grid_scores_tmp.append(
                grid_score[
                    grid_2D[:, 0],
                    grid_2D[:, 1]
                ]
            )
            grid_masks_tmp.append(
                joint_mask[
                    grid_2D[:, 0],
                    grid_2D[:, 1]
                ]
            )

            #
            # Collect RGB values at grid points.
            #
            cloud_rgb_tmp.append(
                array_model.img_array[
                    grid_2D[:, 0],
                    grid_2D[:, 1],
                    ...
                ]
            )

        return np.mean(grid_scores_tmp, axis=0), \
               np.mean(grid_masks_tmp, axis=0), \
               np.mean(cloud_rgb_tmp, axis=0)
