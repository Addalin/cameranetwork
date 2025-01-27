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
# -*- coding: utf-8 -*-
from __future__ import print_function

"""
Created on Sat Jul 20 14:27:48 2013

@author: silvester

Demonstration of Point Cloud tool using OpenGL bindings in PyQtGraph

Based on GLScatterPlotItem.py example from PyQtGraph
License: MIT

"""
from PyQt4 import QtCore, QtGui
import pyqtgraph.opengl as gl
import numpy as np
import pyqtgraph as pg
import sys
from atom.api import Atom, Float, Value, observe, Coerced, Int, Typed


#: Cyclic guard flags
VIEW_SYNC_FLAG = 0x1
PLOT_CHANGE_FLAG = 0x2


class MyGLViewWidget(gl.GLViewWidget):
    """ Override GLViewWidget with enhanced behavior and Atom integration.
    
    """
    #: Fired in update() method to synchronize listeners.
    sigUpdate = QtCore.pyqtSignal()
    
    def mousePressEvent(self, ev):
        """ Store the position of the mouse press for later use.
        
        """
        super(MyGLViewWidget, self).mousePressEvent(ev)
        self._downpos = self.mousePos
            
    def mouseReleaseEvent(self, ev):
        """ Allow for single click to move and right click for context menu.
        
        Also emits a sigUpdate to refresh listeners.
        """
        super(MyGLViewWidget, self).mouseReleaseEvent(ev)
        if self._downpos == ev.pos():
            if ev.button() == 2:
                print('show context menu')
            elif ev.button() == 1:
                x = ev.pos().x() - self.width() / 2
                y = ev.pos().y() - self.height() / 2
                self.pan(-x, -y, 0, relative=True)
                print(self.opts['center'])
        self._prev_zoom_pos = None
        self._prev_pan_pos = None
        self.sigUpdate.emit()
                
    def mouseMoveEvent(self, ev):
        """ Allow Shift to Move and Ctrl to Pan.
        
        """
        shift = ev.modifiers() & QtCore.Qt.ShiftModifier
        ctrl = ev.modifiers() & QtCore.Qt.ControlModifier
        if shift:
            y = ev.pos().y()
            if not hasattr(self, '_prev_zoom_pos') or not self._prev_zoom_pos:
                self._prev_zoom_pos = y
                return
            dy = y - self._prev_zoom_pos
            def delta():
                return -dy * 5
            ev.delta = delta
            self._prev_zoom_pos = y
            self.wheelEvent(ev)
        elif ctrl:
            pos = ev.pos().x(), ev.pos().y()
            if not hasattr(self, '_prev_pan_pos') or not self._prev_pan_pos:
                self._prev_pan_pos = pos
                return
            dx = pos[0] - self._prev_pan_pos[0]
            dy = pos[1] - self._prev_pan_pos[1]
            self.pan(dx, dy, 0, relative=True)
            self._prev_pan_pos = pos
        else:
            super(MyGLViewWidget, self).mouseMoveEvent(ev)
        

class Scatter3DPlot(Atom):
    """ A Scatter3D Point Manager.

    Maintains a scatter plot.

    """
    #: (N,3) array of floats specifying point locations.
    pos = Coerced(np.ndarray, coercer=np.ndarray)
    
    #: (N,4) array of floats (0.0-1.0) specifying pot colors 
    #: OR a tuple of floats specifying a single color for all spots.
    color = Value([1.0, 1.0, 1.0, 0.5])
    
    #: (N,) array of floats specifying spot sizes or a value for all spots.
    size = Value(5)
    
     #: GLScatterPlotIem instance.
    _plot = Value()
    
    def _default_pos(self):
        return np.random.random(size=(512 * 256, 3))
    
    def _default__plot(self):
        """ Create a GLScatterPlot item with our current attributes.
        
        """
        return gl.GLScatterPlotItem(pos=self.pos, color=self.color, 
                                    size=self.size)
    
    @observe('color', 'pos', 'size')
    def _plot_change(self, change):
        """ Pass changes to point properties to the GLScatterPlot object.
        
        """        
        kwargs = {change['name']: change['value']}
        self._plot.setData(**kwargs)

        
class Scatter3DScene(Atom):
    """ A Scatter3D Scene Manager.
    
    Maintains a scatter plot and its scene.
    
    """
    __slots__ = '__weakref__'
    
    #: Scatter3D Point manager
    plot = Typed(Scatter3DPlot, ())
    
    #: Camera FOV center 
    center = Value(pg.Vector(0, 0, 0)) 
    
    #: Distance of camera from center
    distance = Float(100.) 
    
    #: Horizontal field of view in degrees
    fov = Float(60.)   
    
    #: Camera's angle of elevation in degrees.
    elevation = Coerced(float, (30.,)) 
    
    #: Camera's azimuthal angle in degrees.
    azimuth = Float(45.)  
    
    #: MyGLViewWidget instance.
    _widget = Typed(MyGLViewWidget, ())
    
    #: GLGridItem instance
    _grid = Value()
    
    #: GLAxisItem instance.
    _orientation_axes = Value()
    
    #: Cyclic notification guard flags.
    _guard = Int(0)
    
    def _default__widget(self, parent=None):
        """ Create a GLViewWidget and add plot, grid, and orientation axes.
        
        """
        w = MyGLViewWidget(parent)
        self._grid = g = gl.GLGridItem()
        w.addItem(g)
        self._orientation_axes = ax = gl.GLAxisItem(size=pg.Vector(5, 5, 5))
        w.addItem(ax)
        w.addItem(self.plot._plot)
        w.sigUpdate.connect(self._update_model)
        for attr in ['azimuth', 'distance', 'fov', 'center', 'elevation']:
            w.opts[attr] = getattr(self, attr)
        return w
    
    def show(self, title=''):
        """ Show the view in a graphics window.
        """
        if not title:
            title = 'pyqtgraph Atom example: GLScatterPlotItem'
        app = pg.mkQApp()
        self._widget.show()
        app.exec_()
    
    def _observe_plot(self, change):
        """ Synchronize plot changing with the scene.
        
        """
        if change['type'] == 'create':
            return
        self._guard |= PLOT_CHANGE_FLAG
        self._widget.removeItem(change['oldvalue']._plot)
        self._widget.addItem(change['value']._plot)
        self._guard &= ~PLOT_CHANGE_FLAG
    
    @observe('azimuth', 'distance', 'fov', 'center', 'elevation')
    def _update_view(self, change):
        """ Synchronize model attributes to the view.
        
        """
        if self._guard & (VIEW_SYNC_FLAG) or change['type'] == 'create':
            return
        self._widget.opts[change['name']] = change['value']
        self._widget.update()
    
    def _update_model(self):
        """ Synchronize view attributes to the model.
        
        """
        if self._guard & PLOT_CHANGE_FLAG:
            return
        self._guard &= VIEW_SYNC_FLAG
        for (key, value) in self._widget.opts.items():
            setattr(self, key, value)
        self._guard &= ~VIEW_SYNC_FLAG
        

def main():    
    """ Create some scatter3d points and show a demo window.
    
    """
    pos = np.random.random(size=(512*256,3))
    pos *= [10,-10,10]
    d2 = (pos**2).sum(axis=1)**0.5
    pos[:, 2] = d2
    color = [1, 0, 0, 0.5]
    size = 5
    
    plot = Scatter3DPlot(pos=pos, size=size, color=color)
    scene = Scatter3DScene(plot=plot, distance=80.)
    scene.show()


if __name__ == '__main__':
    main()
    # Start Qt event loop unless running in interactive mode.
    if (sys.flags.interactive != 1) or not hasattr(QtCore, 'PYQT_VERSION'):
        QtGui.QApplication.instance().exec_()
