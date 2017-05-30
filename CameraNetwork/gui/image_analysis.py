"""PyQtGraph widget for the enaml framework

Based on the example by blink1073:
https://gist.github.com/blink1073/7411284
"""
from atom.api import Dict, Instance, Signal, Str, Int, observe, List, Bool, ForwardTyped, Typed, Tuple, Instance, Atom
from enaml.core.declarative import d_
from enaml.qt import QtCore, QtGui
from enaml.qt.qt_control import QtControl
from enaml.widgets.control import Control, ProxyControl
import logging
import math
import numpy as np
import pickle
import pyqtgraph as pg

pg.setConfigOptions(imageAxisOrder='row-major')

import CameraNetwork.global_settings as gs

MASK_INIT_RESOLUTION = 20
DEFAULT_IMG_SHAPE = (301, 301, 3)


class ProxyImageAnalysis(ProxyControl):
    #: A reference to the ImageAnalysis declaration.
    declaration = ForwardTyped(lambda: ImageAnalysis)

    def set_server_id(self, server_id):
        raise NotImplementedError

    def set_arrays_model(self, arrays_model):
        raise NotImplementedError

    def set_img_array(self, img_array):
        raise NotImplementedError

    def set_Almucantar_coords(self, almucantar_coords):
        raise NotImplementedError

    def set_PrincipalPlane_coords(self, principalplane_coords):
        raise NotImplementedError

    def set_Epipolar_coords(self, epipolar_coords):
        raise NotImplementedError

    def set_GRID_coords(self, grid_coords):
        raise NotImplementedError

    def set_show_almucantar(self, show):
        raise NotImplementedError

    def set_show_principalplane(self, show):
        raise NotImplementedError

    def set_show_grid(self, show):
        raise NotImplementedError

    def set_show_mask(self, show):
        raise NotImplementedError

    def set_show_ROI(self, show):
        raise NotImplementedError

    def set_gamma(self, apply):
        raise NotImplementedError

    def set_intensity(self, intensity):
        raise NotImplementedError

    def set_ROI_state(self, state):
        raise NotImplementedError

    def set_mask_ROI_state(self, state):
        raise NotImplementedError

    def set_mask(self, mask):
        raise NotImplementedError

    def getArrayRegion(self, data):
        raise NotImplementedError


class ImageAnalysis(Control):
    """A base for PyQtGraph Widgets for enaml.

    It implement different widgets that help in analyzing the images.

    Attributes:
        img_array (array): Displayed image.
        epipolar_scatter (pg.ScatterPlotItem): Projection of selected pixel on
            different images.
        grid_scatter (pg.ScatterPlotItem): Projection of reconstruction grid on
            different images.
        almucantar_scatter (pg.ScatterPlotItem): Projection of the Almucantar on
            the image.
        principalplane_scatter (pg.ScatterPlotItem): Projection of the Principal
            Plane on the image.
        ROI (pg.RectROI): Rectangle ROI that is extracted for reconstruction.
        mask_ROI (pg.PolyLineROI): Polygonal ROI that is used for masking out
            obstructing objects.
    """

    #: A reference to the ProxyImageAnalysis object.
    proxy = Typed(ProxyImageAnalysis)

    #
    # The ID of the current server.
    #
    server_id = d_(Str())
    arrays_model = d_(Instance(Atom))

    #
    # The displayed image as numpy array.
    #
    img_array = d_(Instance(np.ndarray))

    #
    # Coordinates of the Almucantar and PrinciplePlane and Epipolar visualizations.
    #
    Almucantar_coords = d_(List(default=[]))
    PrincipalPlane_coords = d_(List(default=[]))
    Epipolar_coords = d_(Tuple(default=()))
    GRID_coords = d_(Tuple(default=()))

    #
    # Flags that control the display
    #
    gamma = d_(Bool(False))
    intensity = d_(Int(100))
    show_grid = d_(Bool(False))
    show_mask = d_(Bool(False))
    show_ROI = d_(Bool(False))
    show_almucantar = Bool(False)
    show_principalplane = Bool(False)

    #
    # State of the ROIs
    #
    ROI_state = d_(Dict())
    mask_ROI_state = d_(Dict())

    #
    # Manual mask.
    #
    mask = d_(Typed(np.ndarray))

    def getArrayRegion(self, data):
        if not self.proxy_is_active:
            return

        return self.proxy.getArrayRegion(data)

    def _default_mask(self):
        return np.ones((301, 301), dtype=np.uint8)

    #--------------------------------------------------------------------------
    # Observers
    #--------------------------------------------------------------------------
    @observe('img_array', 'server_id', 'Almucantar_coords', 'PrincipalPlane_coords',
             'show_almucantar', 'show_principalplane', 'show_ROI', 'show_mask',
             'show_grid', 'gamma', 'intensity', 'Epipolar_coords', "GRID_coords",
             'ROI_state', 'mask_ROI_state')
    def _update_proxy(self, change):
        """ Update the proxy widget when the Widget data changes.

        This method only updates the proxy when an attribute is updated;
        not when it is created or deleted.

        """
        # The superclass implementation is sufficient.
        super(ImageAnalysis, self)._update_proxy(change)


class QtImageAnalysis(QtControl, ProxyImageAnalysis):
    #: A reference to the widget created by the proxy.
    widget = Typed(pg.GraphicsLayoutWidget)

    server_id = Str()
    arrays_model = Instance(Atom)

    #
    # Different controls that can be displayed on the GUI
    #
    almucantar_scatter = Instance(pg.ScatterPlotItem)
    epipolar_scatter = Instance(pg.ScatterPlotItem)
    grid_scatter = Instance(pg.ScatterPlotItem)
    principalplane_scatter = Instance(pg.ScatterPlotItem)
    plot_area = Instance(pg.PlotItem)

    #
    # ROI - Rectangle ROI that can be set by the user.
    #
    ROI = Instance(pg.RectROI)
    ROI_signal = Signal()

    #
    # mask_ROI - Polygon ROI that can be use to mask buildings and other
    # obstacles.
    #
    mask_ROI = Instance(pg.PolyLineROI)

    #
    # Signals to notify the main model of modifications
    # that need to be broadcast to the rest of the cameras.
    #
    LOS_signal = Signal()

    #
    # The image itself, a PyQtGraph ImageItem.
    #
    img_item = Instance(pg.ImageItem)

    #
    # For internal use.
    # Note:
    # These are used for avoiding double updates of the ROI and mask_ROI
    # states. The updates are caused when declaration.xxx is set (to update
    # view) which calls the 'set_' callback. These double update cause an
    # exception in the pyqtgraph (don't know why).
    #
    _internal_ROI_update = Bool(False)
    _internal_mask_ROI_update = Bool(False)

    def initEpiploarPoints(self, epipolar_coords):
        """Initialize the epipolar points on the plot."""

        self.epipolar_scatter = pg.ScatterPlotItem(
            size=5, pen=pg.mkPen(None), brush=pg.mkBrush(255, 255, 0, 120)
        )
        self.epipolar_scatter.addPoints(
            pos=np.array(epipolar_coords).T
        )

        self.epipolar_scatter.setZValue(100)


        self.plot_area.addItem(self.epipolar_scatter)

    def initGrid(self, grid_coords):
        """Initialize the grid (scatter points) on the plot."""

        with open("grid.pkl", "wb") as f:
            pickle.dump(grid_coords, f)

        self.grid_scatter = pg.ScatterPlotItem(
            size=1, pen=pg.mkPen(None), brush=pg.mkBrush(255, 255, 255, 120)
        )
        self.grid_scatter.addPoints(
            pos=np.array(grid_coords).T
        )
        self.grid_scatter.setZValue(100)

        self.plot_area.addItem(self.grid_scatter)
        self.grid_scatter.setVisible(False)

    def initAlmucantar(self, almucantar_coords):
        """Initialize the Almucantar marker"""

        self.almucantar_scatter = pg.ScatterPlotItem(
            size=3, pen=pg.mkPen(None), brush=pg.mkBrush(255, 0, 0, 120)
        )
        self.almucantar_scatter.addPoints(
            pos=np.array(almucantar_coords)
        )
        self.almucantar_scatter.setZValue(99)
        self.almucantar_scatter.setVisible(False)

        self.plot_area.addItem(self.almucantar_scatter)

    def initPrincipalPlane(self, PrincipalPlane_coords):
        """Initialize the Principal Plane marker"""

        self.principalplane_scatter = pg.ScatterPlotItem(
            size=3, pen=pg.mkPen(None), brush=pg.mkBrush(255, 0, 0, 120)
        )
        self.principalplane_scatter.addPoints(
            pos=np.array(PrincipalPlane_coords)
        )
        self.principalplane_scatter.setZValue(98)
        self.principalplane_scatter.setVisible(False)

        self.plot_area.addItem(self.principalplane_scatter)

    def initROIs(self, img_shape):
        """Initialize the ROI markers"""

        #
        # Mask ROI
        #
        angles = np.linspace(0, 2*np.pi, MASK_INIT_RESOLUTION)
        xs = img_shape[0] * (1 + 0.9 * np.cos(angles)) / 2
        ys = img_shape[1] * (1 + 0.9 * np.sin(angles)) / 2
        mask_positions = np.vstack((xs, ys)).T
        self.mask_ROI = pg.PolyLineROI(mask_positions, closed=True)
        self.mask_ROI.setVisible(False)

        self.plot_area.vb.addItem(self.mask_ROI)

        #
        # Reconstruction ROI
        #
        self.ROI = pg.RectROI([20, 20], [20, 20], pen=(0,9))
        self.ROI.addRotateHandle([1, 0], [0.5, 0.5])
        self.ROI.setVisible(False)

        #
        # Callback when the user stopsmoving a ROI.
        #
        self.ROI.sigRegionChangeFinished.connect(self._ROI_updated)
        self.mask_ROI.sigRegionChangeFinished.connect(self._mask_ROI_updated)

        self.plot_area.vb.addItem(self.ROI)

    def mouseClicked(self, evt):
        """Callback of mouse click (used for updating the epipolar lines)."""
        #
        # Get the click position.
        #
        pos = evt.scenePos()

        if self.plot_area.sceneBoundingRect().contains(pos):
            #
            # Map the click to the image.
            #
            mp = self.plot_area.vb.mapSceneToView(pos)
            h, w = self.img_item.image.shape[:2]
            x, y = np.clip((mp.x(), mp.y()), 0, h-1).astype(np.int)

            #
            # Update the LOS points.
            #
            self.LOS_signal.emit(
                {'server_id': self.server_id, 'pos': (x, y)}
            )

    def create_widget(self):
        """Create the PyQtGraph widget"""

        self.widget = pg.GraphicsLayoutWidget(self.parent_widget())

        self.plot_area = self.widget.addPlot()
        self.plot_area.hideAxis('bottom')
        self.plot_area.hideAxis('left')

        #
        # Connect the click callback to the plot.
        #
        self.plot_area.scene().sigMouseClicked.connect(self.mouseClicked)

        #
        # Item for displaying image data
        #
        self.img_item = pg.ImageItem()
        self.plot_area.addItem(self.img_item)

        self.img_item.setImage(np.zeros(DEFAULT_IMG_SHAPE))

        #
        # Setup the ROIs
        #
        self.initROIs(DEFAULT_IMG_SHAPE)

        self.widget.resize(400, 400)

        return self.widget

    def init_widget(self):
        """ Initialize the widget.

        """
        super(QtImageAnalysis, self).init_widget()
        d = self.declaration
        self.set_server_id(d.server_id)
        self.set_arrays_model(d.arrays_model)
        self.set_img_array(d.img_array)
        self.set_Almucantar_coords(d.Almucantar_coords)
        self.set_PrincipalPlane_coords(d.PrincipalPlane_coords)
        self.set_Epipolar_coords(d.Epipolar_coords)
        self.set_GRID_coords(d.GRID_coords)
        self.set_show_almucantar(d.show_almucantar)
        self.set_show_principalplane(d.show_principalplane)
        self.set_show_grid(d.show_grid)
        self.set_show_mask(d.show_mask)
        self.set_show_ROI(d.show_ROI)
        self.set_gamma(d.gamma)
        self.set_intensity(d.intensity)
        self.set_ROI_state(d.ROI_state)
        self.set_mask_ROI_state(d.mask_ROI_state)
        self.set_mask(d.mask)
        self.observe('LOS_signal', self.arrays_model.updateLOS)

    def set_server_id(self, server_id):

        self.server_id = server_id

    def set_arrays_model(self, arrays_model):

        self.arrays_model = arrays_model

    def set_img_array(self, img_array):
        """Update the image array."""

        self.img_item.setImage(img_array.astype(np.float))

    def set_Almucantar_coords(self, Almucantar_coords):
        """Update the Almucantar coords."""

        if self.almucantar_scatter is None:
            self.initAlmucantar(Almucantar_coords)
            return

        self.almucantar_scatter.setData(
            pos=np.array(Almucantar_coords)
        )

    def set_PrincipalPlane_coords(self, PrincipalPlane_coords):
        """Update the Almucantar coords."""

        if self.principalplane_scatter is None:
            self.initPrincipalPlane(PrincipalPlane_coords)
            return

        self.principalplane_scatter.setData(
            pos=np.array(PrincipalPlane_coords)
        )

    def set_Epipolar_coords(self, Epipolar_coords):
        """Update the Epipolar coords."""

        if self.epipolar_scatter is None:
            self.initEpiploarPoints(Epipolar_coords)
            return

        self.epipolar_scatter.setData(
            pos=np.array(Epipolar_coords).T
        )

    def set_GRID_coords(self, GRID_coords):
        """Update the grid coords."""

        if self.grid_scatter is None:
            self.initGrid(GRID_coords)
            return

        self.grid_scatter.setData(
            pos=np.array(GRID_coords).T
        )

    def set_show_almucantar(self, show):
        """Control the visibility of the Almucantar widget."""

        if self.almucantar_scatter is None:
            return

        self.almucantar_scatter.setVisible(show)

    def set_show_principalplane(self, show):
        """Control the visibility of the PrincipalPlane widget."""

        if self.principalplane_scatter is None:
            return

        self.principalplane_scatter.setVisible(show)

    def set_show_grid(self, show):
        """Control the visibility of the grid widget."""

        if self.grid_scatter is None:
            return

        self.grid_scatter.setVisible(show)

    def set_show_mask(self, show):
        """Control the visibility of the mask ROI widget."""

        self.mask_ROI.setVisible(show)

    def set_show_ROI(self, show):
        """Control the visibility of the ROI widget."""

        self.ROI.setVisible(show)

    def set_gamma(self, apply_flag):
        """Apply Gamma correction."""

        if apply_flag:
            lut = np.array([((i / 255.0) ** 0.4) * 255
                            for i in np.arange(0, 256)]).astype(np.uint8)
        else:
            lut = np.arange(0, 256).astype(np.uint8)

        self.img_item.setLookupTable(lut)

    def set_intensity(self, intensity):
        """Set the intensity of the image."""

        self.img_item.setLevels((0, intensity))

    def set_ROI_state(self, state):
        """Set the ROI."""

        if state == {}:
            return

        if self._internal_ROI_update:
            self._internal_ROI_update = False
            return

        self.ROI.setState(state)

    def set_mask(self, mask):
        """Set the manual mask."""
        #
        # The mask should be calculated internally.
        #
        pass

    def set_mask_ROI_state(self, state):
        """Set the mask ROI."""

        if state == {}:
            return

        if self._internal_mask_ROI_update:
            self._internal_mask_ROI_update = False
            return

        self.mask_ROI.setState(state)
        self._update_mask()

    def _ROI_updated(self):
        """Callback of ROI udpate."""

        #
        # Propagate the state of the ROI (used for saving).
        #
        self._internal_ROI_update = True
        self.declaration.ROI_state = self.ROI.saveState()
        _, tr = self.ROI.getArraySlice(
            self.img_item.image,
            self.img_item
        )

        size = self.ROI.state['size']

        #
        # Calculate the bounds.
        #
        pts = np.array(
            [tr.map(x, y) for x, y in \
             ((0, 0), (size.x(), 0), (0, size.y()), (size.x(), size.y()))]
        )

        #
        # Signal change of ROI (used by the map3d)
        #
        self.ROI_signal.emit(
            {'server_id': self.server_id, 'pts': pts, 'shape': self.img_item.image.shape}
        )

    def _mask_ROI_updated(self):
        """Callback of mask ROI udpate."""

        #
        # Propagate the state of the mask_ROI (used for saving).
        #
        self._internal_mask_ROI_update = True
        self.declaration.mask_ROI_state = self.mask_ROI.saveState()

        self._update_mask()

    def _update_mask(self):
        """Update the mask itself."""

        #
        # Get mask ROI region.
        #
        data = np.ones(self.img_item.image.shape[:2], np.uint8)
        sl, _ = self.mask_ROI.getArraySlice(data, self.img_item, axes=(0, 1))
        sl_mask = self.mask_ROI.getArrayRegion(data, self.img_item)

        #
        # The new version of pyqtgraph has some rounding problems.
        # Fix it the slices accordingly.
        #
        fixed_slices = (
            slice(sl[0].start, sl[0].start+sl_mask.shape[0]),
            slice(sl[1].start, sl[1].start+sl_mask.shape[1])
        )

        mask = np.zeros(self.img_item.image.shape[:2], np.uint8)
        mask[fixed_slices] = sl_mask

        #
        # Propagate the mask.
        #
        self.declaration.mask = mask

    def update_ROI_resolution(self, old_shape):
        """Update the ROI_resolution.

        Used to fix the save ROI resolution to new array resolution.
        """

        s = float(self.img_item.image.shape[0]) / float(old_shape[0])
        c = np.array(old_shape)/2
        t = np.array(self.img_item.image.shape[:2])/2 -c
        self.ROI.scale(s, center=c)
        self.ROI.translate((t[0], t[1]))
        self.mask_ROI.scale(s, center=c)
        self.mask_ROI.translate((t[0], t[1]))

    def getArrayRegion(self, data):
        """Get the region selected by ROI.

        The function accepts an array in the size of the image.
        It crops a region marked by the ROI.

        Args:
            data (array): The array to crop the ROI from.

        TODO:
            Fix the bug that creates artifacts.
        """

        #
        # Get ROI region.
        #
        roi = self.ROI.getArrayRegion(data, self.img_item)

        return roi


def image_analysis_factory():
    return QtImageAnalysis
