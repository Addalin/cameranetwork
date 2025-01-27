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

from PyQt4 import QtCore
from PyQt4 import QtGui
#.QtCore import Qt, QRectF
#from PyQt4.QtGui import QApplication, QHBoxLayout, QLabel, QSizePolicy, QSlider, QSpacerItem, \
    #QVBoxLayout, QWidget
#import QtCore.QString.fromUtf8 as asdf

import glob
import numpy as np
import os
import pandas as pd
import pymap3d
import pyqtgraph as pg
pg.setConfigOptions(imageAxisOrder='row-major')
import skimage.io as io
import sys


def convertMapData(lat, lon, hgt, lat0=32.775776, lon0=35.024963, alt0=229):
    """Convert lat/lon/height data to grid data."""

    n, e, d = pymap3d.geodetic2ned(
        lat, lon, hgt,
        lat0=lat0, lon0=lon0, h0=alt0)

    x, y, z = e, n, -d

    return x, y


class Slider(QtGui.QWidget):
    def __init__(self, maximum, parent=None):
        super(Slider, self).__init__(parent=parent)

        #
        # Create the Slider (centered)
        #
        self.horizontalLayout = QtGui.QHBoxLayout(self)
        spacerItem = QtGui.QSpacerItem(0, 20, QtGui.QSizePolicy.Expanding, QtGui.QSizePolicy.Minimum)
        self.horizontalLayout.addItem(spacerItem)
        self.slider = QtGui.QSlider(self)
        self.slider.setOrientation(QtCore.Qt.Vertical)
        self.horizontalLayout.addWidget(self.slider)
        spacerItem1 = QtGui.QSpacerItem(0, 20, QtGui.QSizePolicy.Expanding, QtGui.QSizePolicy.Minimum)
        self.horizontalLayout.addItem(spacerItem1)
        self.resize(self.sizeHint())

        self.slider.setMaximum(maximum)

    def value(self):
        return self.slider.value()


class MainWindow(QtGui.QWidget):
    """main widget."""

    def __init__(self, parent=None):
        super(MainWindow, self).__init__(parent=parent)

        #
        # Create the main window
        #
        self.verticalLayout = QtGui.QVBoxLayout(self)
        self.label = QtGui.QLabel(self)
        self.verticalLayout.addWidget(self.label)

        self.cameras_view = pg.GraphicsWindow(title="Basic plotting examples")
        self.horizontalLayout = QtGui.QHBoxLayout()
        self.horizontalLayout.addWidget(self.cameras_view)
        self.view = self.cameras_view.addViewBox()
        self.verticalLayout.addLayout(self.horizontalLayout)

        #
        # lock the aspect ratio so pixels are always square
        #
        self.view.setAspectLocked(True)

        #
        # Load the thumbnails dataframes
        #
        dfs = pd.read_pickle(r"..\ipython\system\thumbnails_downloaded.pkl")
        self.thumbs = {}
        self.image_items = {}
        server_id_list, df_list = [], []
        for server_id, df in dfs.items():
            server_id_list.append(server_id)

            #
            # Load all the images.
            #
            print("Processing camera {}".format(server_id))
            images, indices = [], []
            index = 0
            for _, row in df.iterrows():
                try:
                    images.append(io.imread(os.path.join(r"..\ipython\system", row["thumbnail"])))
                    indices.append(index)
                    index += 1
                except:
                    indices.append(None)

            self.thumbs[server_id] = images
            df["thumb_index"] = indices
            df_list.append(df)

            #
            # Create image widgets
            #
            image_item = pg.ImageItem()
            image_label = pg.LabelItem(text=server_id)
            image_label.scale(1, -1)
            self.view.addItem(image_item)
            self.view.addItem(image_label)
            self.image_items[server_id] = (image_item, image_label)

        self.df = pd.concat(df_list, axis=1, keys=server_id_list)

        #
        # Create the thumbnail slider
        #
        self.w1 = Slider(len(self.df)-1)
        self.horizontalLayout.addWidget(self.w1)
        self.w1.slider.valueChanged.connect(lambda: self.update())

        self.update()

    def update(self):
        #
        # Get the current image time/index.
        #
        img_index = int(self.w1.value())
        row = self.df.iloc[img_index]
        self.label.setText(repr(row.name))

        for server_id, (image_item, image_label) in self.image_items.items():
            server_data = row[server_id]
            if not np.isfinite(server_data["thumb_index"]):
                image_item.hide()
                image_label.hide()
                continue

            x, y = convertMapData(server_data["latitude"], server_data["longitude"], 0)
            x = int(x/10)
            y = int(y/10)

            image_item.show()
            image_label.show()
            image_item.setImage(self.thumbs[server_id][int(server_data["thumb_index"])])
            image_item.setRect(QtCore.QRectF(x, y, 100, 100))
            image_label.setX(x)
            image_label.setY(y+120)

if __name__ == '__main__':
    app = QtGui.QApplication(sys.argv)
    w = MainWindow()
    w.show()
    sys.exit(app.exec_())