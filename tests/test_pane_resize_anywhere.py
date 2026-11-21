"""Tests for pane_resize_anywhere, against a stub hou and a real Qt.

    python tests/test_pane_resize_anywhere.py

Needs PySide6 or PySide2; no Houdini.  hou is stubbed and the pane tree is
faked, so what is covered is this module's own arithmetic and event handling:
where a band goes, which splits it refuses, and what a pan does to a pane.
"""

import os
import sys
import types

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "houdini", "python"))

hou = types.ModuleType("hou")


class _Error(Exception):
    pass


class _OperationFailed(_Error):
    pass


hou.Error, hou.OperationFailed = _Error, _OperationFailed
hou.isUIAvailable = lambda: True
hou.ui = types.SimpleNamespace()
sys.modules["hou"] = hou

try:
    from PySide6 import QtCore, QtWidgets
except ImportError:
    from PySide2 import QtCore, QtWidgets

app = QtWidgets.QApplication(sys.argv)

import pane_resize_anywhere as m

P, R = QtCore.QPoint, QtCore.QRect
_real_side_by_side = m._is_side_by_side
_ids = [0]


# ------------------------------------------------------------- a pane tree --


class Tab(object):
    def __init__(self, name):
        self._name = name

    def type(self):
        return types.SimpleNamespace(name=lambda: self._name)


class Pane(object):
    """A pane whose split fraction follows the module's own convention."""

    def __init__(self, rect, children=(), fraction=None, tab=None):
        _ids[0] += 1
        self._id = _ids[0]
        self._rect = rect
        self._children = list(children)
        self._parent = None
        self._fraction = fraction  # override, for the rigged cases
        self._tab = tab
        for child in self._children:
            child._parent = self

    def id(self):
        return self._id

    def isSplit(self):
        return bool(self._children)

    def isMaximized(self):
        return False

    def getSplitChild(self, index):
        return self._children[index]

    def getSplitParent(self):
        return self._parent

    def qtScreenGeometry(self):
        return self._rect

    def currentTab(self):
        return self._tab

    def getSplitFraction(self):
        """Child 0's share of the parent, as the module reads it."""
        parent = self._parent
        if parent is None:
            raise _OperationFailed("not in a split")
        if parent._fraction is not None:
            return parent._fraction
        a, b = (c._rect for c in parent._children)
        rect = parent._rect
        if _real_side_by_side(parent):  # divider x, measured from the left
            return ((a.right() + b.left()) / 2.0 - rect.left()) / rect.width()
        return (rect.bottom() - (a.bottom() + b.top()) / 2.0) / rect.height()


def desktop(root_fraction=None, tab=None):
    """Scene view over python shell on the left, one pane on the right."""
    scene = Pane(R(0, 71, 646, 577))                      # 71..647
    shell = Pane(R(0, 652, 646, 375))                     # 652..1026
    left = Pane(R(0, 71, 646, 956), [scene, shell])
    right = Pane(R(650, 71, 1145, 956), tab=tab)          # 650..1794
    root = Pane(R(0, 71, 1795, 956), [left, right], fraction=root_fraction)
    hou.ui.paneUnderCursor = lambda: right
    return root, left, right


CURSOR = P(980, 977)      # bottom left area of the right hand pane


# ------------------------------------------------------------------- tests --


def test_band_geometry():
    rect = R(100, 200, 400, 300)
    horizontal = m._AxisDrag(None, True, 0.5, rect.width(), rect, m._LEFT)
    assert abs(horizontal.fraction_for(P(40, 0)) - 0.6) < 1e-9
    assert horizontal.fraction_for(P(-10000, 0)) == m.MIN_FRACTION
    assert horizontal.fraction_for(P(10000, 0)) == 1.0 - m.MIN_FRACTION
    assert horizontal.band_rect(P(0, 0)) == R(298, 200, 4, 300)
    assert horizontal.band_rect(P(40, 0)).left() == 338

    vertical = m._AxisDrag(None, False, 0.5, rect.height(), rect, m._TOP)
    assert abs(vertical.fraction_for(P(0, -30)) - 0.6) < 1e-9   # up is bigger
    assert vertical.band_rect(P(0, 0)).width() == rect.width()
    for delta in (P(0, 0), P(0, -57), P(0, 91)):
        divider = int(round(rect.bottom() - vertical.fraction_for(delta) * 300))
        assert vertical.band_rect(delta).top() + 2 == divider


def test_corner_bands_stay_joined():
    outer = R(0, 0, 1000, 800)        # split A: its divider is the left border
    inner = R(400, 0, 600, 800)       # split B, nested in A's right hand child
    thickness = m.BAND_THICKNESS

    def corner(delta):
        drags = [m._AxisDrag(None, True, 0.4, outer.width(), outer, m._LEFT),
                 m._AxisDrag(None, False, 0.5, inner.height(), inner, m._TOP)]
        return drags, m._band_rects(drags, delta)

    drags, (vertical, horizontal) = corner(P(0, 0))
    assert horizontal.left() == vertical.left()     # inner starts at the outer
    assert vertical.top() == outer.top()            # outer keeps its full span
    assert horizontal.right() == inner.right()

    for delta in (P(-120, -90), P(60, 30), P(250, -200), P(-350, 0)):
        drags, (vertical, horizontal) = corner(delta)
        assert vertical == drags[0].band_rect(delta)    # the outer is untouched
        edge = drags[0].divider_pos(delta) - thickness // 2
        assert horizontal.left() == vertical.left() == edge
        assert horizontal.right() == inner.right() and horizontal.isValid()


def test_only_real_neighbours_are_grabbed():
    root, left, right = desktop()
    assert m._is_side_by_side(root) is True
    assert m._is_side_by_side(left) is False
    assert m._edges_near(right.qtScreenGeometry(), CURSOR) == [m._LEFT, m._BOTTOM]

    drags = m._drags_at(CURSOR)
    assert [d.edge for d in drags] == [m._LEFT]
    assert abs(drags[0].divider_pos(m._ZERO) - 647) <= 1
    assert drags[0].rect == root.qtScreenGeometry()
    assert m._find_split_for_edge(right, m._BOTTOM) is None   # window edge

    # Passing the pane in is the same as looking it up.
    assert [d.edge for d in m._drags_at(CURSOR, m._pane_at(CURSOR))] == [m._LEFT]


def test_a_split_that_is_not_the_border_is_dropped():
    # Children ordered right to left: the offset test reads the split as
    # stacked, so a vertical border matches it and the divider lands mid window.
    scene = Pane(R(0, 71, 646, 577))
    shell = Pane(R(0, 652, 646, 375))
    left = Pane(R(0, 71, 646, 956), [scene, shell])
    right = Pane(R(650, 71, 1145, 956))
    Pane(R(0, 71, 1795, 956), [right, left], fraction=(1026 - 651) / 956.0)
    hou.ui.paneUnderCursor = lambda: right

    assert m._is_side_by_side(right.getSplitParent()) is False
    bad = m._find_split_for_edge(right, m._BOTTOM)
    assert bad is not None and abs(bad.divider_pos(m._ZERO) - 651) <= 1
    assert not m._owns_border(bad, right.qtScreenGeometry(), m._BOTTOM)
    assert m._drags_at(CURSOR) == []


def test_a_split_rect_that_misses_the_pane_is_dropped():
    root, left, right = desktop()
    rect = right.qtScreenGeometry()
    drag = m._find_split_for_edge(right, m._LEFT)
    drag.rect = R(0, 71, 1483, 956)                  # stale: 313 px too narrow
    assert not m._owns_border(drag, rect, m._LEFT)
    drag.rect = root.qtScreenGeometry()
    assert m._owns_border(drag, rect, m._LEFT)

    for off, ok in ((0, True), (3, True), (m.BORDER_SLOP, True),
                    (m.BORDER_SLOP + 8, False)):
        drag = m._find_split_for_edge(right, m._LEFT)
        drag.start_fraction = (rect.left() + off) / float(drag.length)
        assert m._owns_border(drag, rect, m._LEFT) is ok


def test_which_panes_pan():
    assert m._is_pannable(Pane(R(0, 0, 10, 10), tab=Tab("Parm"))) is True
    assert m._is_pannable(Pane(R(0, 0, 10, 10), tab=Tab("NetworkEditor"))) is False
    assert m._is_pannable(Pane(R(0, 0, 10, 10))) is False
    assert m._is_pannable(None) is False

    class Angry(object):
        def currentTab(self):
            raise hou.Error("no tab")

    assert m._is_pannable(Angry()) is False

    m.PAN_PANE_TYPES = ("Parm", "PythonPanel")
    try:
        assert m._is_pannable(Pane(R(0, 0, 10, 10), tab=Tab("PythonPanel"))) is True
    finally:
        m.PAN_PANE_TYPES = ("Parm",)

    # _pan_at bites only in a pannable pane.
    root, left, right = desktop(tab=Tab("NetworkEditor"))
    assert m._pan_at(CURSOR) is None
    assert m._pan_at(CURSOR, (None, None)) is None


def test_pan_sets_scroll_bars():
    area = QtWidgets.QScrollArea()
    inner = QtWidgets.QWidget()
    inner.setFixedSize(2000, 4000)
    area.setWidget(inner)
    area.resize(400, 300)
    area.show()
    app.processEvents()

    horizontal, vertical = area.horizontalScrollBar(), area.verticalScrollBar()
    assert vertical.maximum() > 0 and horizontal.maximum() > 0
    horizontal.setValue(500)
    vertical.setValue(1000)

    pan = m._PanDrag(area, None)
    assert (pan.start_h, pan.start_v) == (500, 1000)
    pan.apply(P(0, 120), P(0, 0))        # drag down: the contents come down
    assert vertical.value() == 880
    pan.apply(P(-60, 120), P(0, 0))      # and left
    assert horizontal.value() == 560
    pan.apply(P(0, 0), P(0, 0))          # back to where the drag began, exactly
    assert (horizontal.value(), vertical.value()) == (500, 1000)
    pan.apply(P(0, 100000), P(0, 0))     # past the end: clamped, not wrapped
    assert vertical.value() == vertical.minimum()
    area.hide()


def test_pan_sends_wheel_notches():
    class Catcher(QtWidgets.QWidget):
        def __init__(self):
            super(Catcher, self).__init__()
            self.wheels = []

        def wheelEvent(self, event):
            delta = event.angleDelta()
            self.wheels.append((delta.x(), delta.y(), m._flag_int(event.modifiers())))
            event.accept()

    catcher = Catcher()
    catcher.resize(400, 300)
    catcher.show()
    app.processEvents()

    step = m.PAN_PIXELS_PER_NOTCH
    pan = m._PanDrag(None, catcher)
    pan.apply(P(0, step - 1), P(5, 5))       # less than a notch: nothing yet
    assert catcher.wheels == []
    pan.apply(P(0, step), P(5, 5))           # one notch, contents downwards
    assert catcher.wheels == [(0, 120, 0)], catcher.wheels
    pan.apply(P(0, 3 * step), P(5, 5))       # two more, not four
    assert catcher.wheels[-1] == (0, 240, 0), catcher.wheels
    pan.apply(P(0, 0), P(5, 5))              # back to the start: three back
    assert catcher.wheels[-1] == (0, -360, 0), catcher.wheels
    pan.apply(P(2 * step, 0), P(5, 5))       # horizontal too
    assert catcher.wheels[-1][:2] == (240, 0), catcher.wheels

    assert sum(w[1] for w in catcher.wheels) == 0    # no drift over the drag
    assert all(w[2] == 0 for w in catcher.wheels)    # never Ctrl+Alt+wheel
    catcher.hide()


def test_bands_are_never_what_gets_panned():
    band = m._RubberBand()
    band.setGeometry(R(0, 0, 40, 400))
    band.show()
    app.processEvents()
    assert m._scroll_area_at(P(10, 10)) is None
    band.hide()
    band.deleteLater()


def test_shortcut_text_follows_the_settings():
    assert m.shortcut() == "Ctrl+Alt+right-drag"
    assert m.shortcut(m.PAN_BUTTON) == "Ctrl+Alt+middle-drag"
    m.MODIFIERS = m.Qt.ShiftModifier | m.Qt.MetaModifier
    m.BUTTON = m.Qt.LeftButton
    try:
        assert m.shortcut() == "Shift+Meta+left-drag"
    finally:
        m.MODIFIERS = m.Qt.ControlModifier | m.Qt.AltModifier
        m.BUTTON = m.Qt.RightButton


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
        print("ok   %s" % test.__name__)
    print("%d passed" % len(tests))


if __name__ == "__main__":
    main()
