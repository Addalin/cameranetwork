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
#----------------------------------------------------------------------------
#
#  Copyright (c) 2014, Enthought, Inc.
#  All rights reserved.
#
#  This software is provided without warranty under the terms of the BSD
#  license included in /LICENSE.txt and may be redistributed only
#  under the conditions described in the aforementioned license.  The license
#  is also available online at http://www.enthought.com/licenses/BSD.txt
#
#  Thanks for using Enthought open source!
#
#----------------------------------------------------------------------------
import numpy as np
from pandas import DataFrame, datetime

from atom.api import Typed, set_default, observe, Int
from enaml.core.declarative import d_
from enaml.widgets.api import RawWidget
from enaml.qt.QtCore import QAbstractTableModel, QModelIndex, Qt
# fixing import from enaml.qt.QtGui. During the transision from Qt4 to Qt5: QtGui was split into QtGui and QtWidgets
#from enaml.qt.QtGui import (
    #QTableView, QHeaderView, QAbstractItemView, QFontMetrics) #  ADI - fixing import of QTableView in Qt5. This line was relevant to Qt4
from enaml.qt.QtGui import QFontMetrics                                    #  ADI - preserving include of QFontMetrics in Qt5
from enaml.qt.QtWidgets import (QTableView,QHeaderView, QAbstractItemView) #  ADI - fixing import of  (QTableView,QHeaderView, QAbstractItemView) in Qt5
from traits_enaml.utils import get_unicode_string, format_value


class ColumnCache(object):
    """ Pull out a view for each column for quick element access.

    """

    def __init__(self, data_frame):
        self.reset(data_frame)

    def __getitem__(self, ij):
        i, j = ij
        return self.columns[j][i]

    def reset(self, new_data_frame=None):
        """ Reset the cache.

        """
        if new_data_frame is not None:
            self.data_frame = new_data_frame
        ncols = len(self.data_frame.columns)
        self.columns = [None] * ncols
        for data_block in self.data_frame._data.blocks:
            for i, ref_loc in enumerate(data_block.mgr_locs):
                self.columns[ref_loc] = data_block.values[i, :]


class QDataFrameModel(QAbstractTableModel):
    def __init__(self, data_frame, *args, **kwds):
        self.data_frame = data_frame
        self.cache = ColumnCache(data_frame)
        self.argsort_indices = None
        self.default_decimals = 6
        super(QDataFrameModel, self).__init__(*args, **kwds)

    def headerData(self, section, orientation, role):
        if role == Qt.TextAlignmentRole:
            if orientation == Qt.Horizontal:
                return int(Qt.AlignHCenter | Qt.AlignVCenter)
            return int(Qt.AlignRight | Qt.AlignVCenter)
        elif role == Qt.DisplayRole:
            if orientation == Qt.Horizontal:
                return get_unicode_string(self.data_frame.columns[section])
            else:
                if self.argsort_indices is not None:
                    section = self.argsort_indices[section]
                return get_unicode_string(self.data_frame.index[section])
        else:
            return None

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid() or \
                not (0 <= index.row() < len(self.data_frame)):
            return None
        if role == Qt.DisplayRole:
            formatted = self._get_formatted_value(index.row(), index.column())
            return formatted
        elif role == Qt.TextAlignmentRole:
            return int(Qt.AlignRight | Qt.AlignVCenter)
        else:
            return None

    def columnCount(self, index=QModelIndex()):
        if not index.isValid():
            return len(self.data_frame.columns)
        else:
            return 0

    def rowCount(self, index=QModelIndex()):
        if not index.isValid():
            return len(self.data_frame.index)
        else:
            return 0

    def sort(self, column, order=Qt.AscendingOrder):
        if column == -1:
            # Return to unsorted.
            if self.argsort_indices is not None:
                self.argsort_indices = None
                self._emit_all_data_changed()
            return

        if len(self.cache.columns) == 0:
            return

        ascending = (order == Qt.AscendingOrder)
        data = self.cache.columns[column]
        # If things are currently sorted, we will try to be stable
        # relative to that order, not the original data's order.
        if self.argsort_indices is not None:
            data = data[self.argsort_indices]
        if ascending:
            indices = np.argsort(data, kind='mergesort')
        else:
            # Do the double-reversing to maintain stability.
            indices = (len(data) - 1 -
                       np.argsort(data[::-1], kind='mergesort')[::-1])
            if np.issubdtype(data.dtype, np.dtype(np.floating)):
                # The block of NaNs is now at the beginning. Move it to
                # the bottom.
                num_nans = np.isnan(data).sum()
                if num_nans > 0:
                    indices = np.roll(indices, -num_nans)
        if self.argsort_indices is not None:
            indices = self.argsort_indices[indices]
        self.argsort_indices = indices
        self._emit_all_data_changed()

    def _emit_all_data_changed(self):
        """ Emit signals to note that all data has changed, e.g. by sorting.

        """
        self.dataChanged.emit(
            self.index(0, 0),
            self.index(
                len(self.data_frame.index) - 1,
                len(self.data_frame.columns) - 1),)
        self.headerDataChanged.emit(
            Qt.Vertical, 0, len(self.data_frame.index) - 1)

    def _get_formatted_value(self, i, j):
        if self.argsort_indices is not None:
            i = self.argsort_indices[i]
        value = self.cache[i, j]
        return format_value(value)


class QDataFrameTableView(QTableView):
    """ View a pandas DataFrame in a table."""

    def __init__(self, df_model, parent=None, **kwds):
        super(QDataFrameTableView, self).__init__(parent=parent, **kwds)
        self.df_model = df_model
        self.setModel(df_model)
        self._setup_sorting()
        self._setup_selection()
        self._setup_scrolling()
        self._setup_headers()
        self._setup_style()

    @classmethod
    def from_data_frame(cls, df, **kwds):
        """ Instantiate a DataFrameTableView directly from a DataFrame.

        """
        df_model = QDataFrameModel(df)
        self = cls(df_model, **kwds)
        return self

    def _setup_sorting(self):
        self.sortByColumn(-1, Qt.AscendingOrder)
        self.setSortingEnabled(True)

    def _setup_selection(self):
        self.selection_model = self.selectionModel()
        #self.selection_model.currentRowChanged.connect(self._current_row_changed)
        self.setSelectionMode(QAbstractItemView.ContiguousSelection)
        self.setSelectionBehavior(QAbstractItemView.SelectItems)

    def _setup_scrolling(self):
        self.setVerticalScrollMode(QAbstractItemView.ScrollPerItem)

    def _setup_headers(self):
        self.vheader = QHeaderView(Qt.Vertical)
        self.setVerticalHeader(self.vheader)
        font = self.vheader.font()
        font.setBold(True)
        fmetrics = QFontMetrics(font)
        max_width = fmetrics.width(u" {0} ".format(
            unicode(self.df_model.rowCount())))
        self.vheader.setMinimumWidth(max_width)

        # self.vheader.setClickable(True) # ADI - this comment is relevant for Qt 4
        self.vheader.setSectionsClickable(True) # ADI - this is relevant for Qt > 4

        self.vheader.setStretchLastSection(False)

        # self.vheader.setResizeMode(QHeaderView.Fixed) # ADI - this comment is relevant for Qt 4
        self.vheader.setSectionResizeMode(QHeaderView.Fixed) # ADI - this is relevant for Qt > 4

        self.hheader = self.horizontalHeader()
        self.hheader.setStretchLastSection(False)

        # self.vheader.setClickable(True) # ADI - this comment is relevant for Qt 4
        self.vheader.setSectionsClickable(True) # ADI - this is relevant for Qt > 4

        # self.hheader.setMovable(True) # ADI - this comment is relevant for Qt 4
        self.hheader.setSectionsMovable(True) # ADI - this is relevant for Qt > 4

    def _setup_style(self):
        self.setWordWrap(False)

    def _current_row_changed(self, model_index):
        print model_index.row()


class DataFrameTable(RawWidget):
    """ A widget that displays a table view tied to a pandas DataFrame."""

    #
    # The data frame to display
    #
    data_frame = d_(Typed(DataFrame))
    selected_row = d_(Int())
    selected_index = d_(Typed(object))

    #
    # Expand the table by default
    #
    hug_width = set_default('weak')
    hug_height = set_default('weak')

    def create_widget(self, parent):
        """Create the DataFrameTable Qt widget."""

        widget = QDataFrameTableView.from_data_frame(
            self.data_frame,
            parent=parent
        )
        widget.currentChanged = self.current_changed

        return widget

    @observe('data_frame')
    def _data_frame_changed(self, change):
        """ Proxy changes in `data_frame` down to the Qt widget."""

        table = self.get_widget()
        if table is not None:
            df_model = QDataFrameModel(change['value'])
            table = self.get_widget()
            table.df_model = df_model
            table.setModel(df_model)

    def current_changed(self, current_item, previous_item):

        self.selected_row = current_item.row()
        self.selected_index = self.data_frame.index[current_item.row()]
