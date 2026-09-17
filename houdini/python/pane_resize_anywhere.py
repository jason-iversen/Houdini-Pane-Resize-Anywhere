"""pane_resize_anywhere.py -- KDE-style "resize anywhere" for Houdini panes.

Hold MODIFIERS and press BUTTON anywhere inside a pane within EDGE_MARGIN
pixels of one of its borders, then drag.  The split that owns that border
follows the mouse.  Pressing near a corner drags both splits at once.
Borders that are window edges (nothing to resize) are ignored.

It works in every pane type (viewport, network editor, parameters, Python
panels, ...) because it filters Qt's application-level mouse events instead
of using a per-pane-type hook such as nodegraphhooks.

Install
-------
This repository is a Houdini package.  Either point Houdini at it with a
one-line package file in $HOUDINI_USER_PREF_DIR/packages/:

    { "package_path": "C:/path/to/Houdini Pane Resize Anywhere" }

or copy pane_resize_anywhere.json there and set PANE_RESIZE_ANYWHERE to the
absolute path of this repository's houdini/ folder.  Restart Houdini.

houdini/pythonX.Ylibs/uiready.py calls install() once the UI is ready.
install() is a no-op outside the UI (hython, hbatch) and defers itself until
the Qt application exists, so it is also safe to call from pythonrc.py.
A shelf tool can call pane_resize_anywhere.toggle() to switch it on and off.
"""

import hou

try:
    from PySide6 import QtCore, QtGui, QtWidgets
except ImportError:  # Houdini < 20.5
    from PySide2 import QtCore, QtGui, QtWidgets

Qt = QtCore.Qt
QEvent = QtCore.QEvent

# ---------------------------------------------------------------- settings --

# Modifier keys that must be held (and no others).  Ctrl+Alt avoids Houdini's
# own Alt/Space+mouse viewport navigation.  KDE's default is Meta, but the
# Windows key is awkward on Windows.
MODIFIERS = Qt.ControlModifier | Qt.AltModifier

# Mouse button that starts the drag.
BUTTON = Qt.RightButton

# How close to a pane border the press must be.  < 1 is a fraction of the
# pane's width/height (0.5 == anywhere in the pane); >= 1 is fixed pixels.
EDGE_MARGIN = 0.35

# Keep splits from collapsing completely.
MIN_FRACTION = 0.02

# Change the mouse cursor while MODIFIERS are held to show what a press would
# grab.  Costs one hou.ui.paneUnderCursor() call per mouse move while held.
SHOW_HOVER_CURSOR = True

# --------------------------------------------------------------- internals --

_LEFT, _RIGHT, _TOP, _BOTTOM = "left", "right", "top", "bottom"


def _flag_int(flags):
    """int value of a Qt flag in both PySide2 (QFlags) and PySide6 (enum.Flag)."""
    try:
        return int(flags)
    except TypeError:
        return int(flags.value)


_MODIFIER_MASK = _flag_int(
    Qt.ShiftModifier | Qt.ControlModifier | Qt.AltModifier | Qt.MetaModifier
)


def _modifiers_match(mods):
    return (_flag_int(mods) & _MODIFIER_MASK) == _flag_int(MODIFIERS)


def _global_pos(event):
    if hasattr(event, "globalPosition"):  # Qt6
        return event.globalPosition().toPoint()
    return event.globalPos()


def _geometry(pane):
    try:
        return pane.qtScreenGeometry()
    except Exception:
        return None


def _leaf_pane_under(pane, pos):
    """paneUnderCursor() normally returns a leaf; descend just in case."""
    while pane is not None and pane.isSplit():
        c0 = pane.getSplitChild(0)
        r0 = _geometry(c0) if c0 is not None else None
        pane = c0 if (r0 is not None and r0.contains(pos)) else pane.getSplitChild(1)
    return pane


def _is_side_by_side(split):
    """True if the split's children sit left/right (vertical divider)."""
    r0 = _geometry(split.getSplitChild(0))
    r1 = _geometry(split.getSplitChild(1))
    if r0 is None or r1 is None:
        return None
    # Child 1 is offset from child 0 along the split axis only.  Comparing the
    # offsets (rather than testing one for zero) stays correct when a child is
    # stowed to a few pixels wide.
    return (r1.left() - r0.left()) > (r1.top() - r0.top())


def _edges_near(rect, pos):
    """Pane borders within EDGE_MARGIN of pos: up to one per axis."""
    w, h = rect.width(), rect.height()
    mx = EDGE_MARGIN * w if EDGE_MARGIN < 1 else EDGE_MARGIN
    my = EDGE_MARGIN * h if EDGE_MARGIN < 1 else EDGE_MARGIN
    mx, my = min(mx, w / 2.0), min(my, h / 2.0)

    dl, dr = pos.x() - rect.left(), rect.right() - pos.x()
    dt, db = pos.y() - rect.top(), rect.bottom() - pos.y()

    edges = []
    if min(dl, dr) <= mx:
        edges.append(_LEFT if dl <= dr else _RIGHT)
    if min(dt, db) <= my:
        edges.append(_TOP if dt <= db else _BOTTOM)
    return edges


class _AxisDrag(object):
    """One split being dragged along one axis."""

    __slots__ = ("child", "horizontal", "start_fraction", "length")

    def __init__(self, child, horizontal, start_fraction, length):
        self.child = child  # a child of the split; set/getSplitFraction act on its parent
        self.horizontal = horizontal
        self.start_fraction = start_fraction
        self.length = max(float(length), 1.0)

    def apply(self, delta):
        # The fraction is child 0's share, so the divider always moves with
        # the mouse no matter which side of it the drag started on.  Houdini's
        # UI is y-up, so a top/bottom split's fraction grows as the divider
        # moves up the screen, i.e. against Qt's y-down mouse delta.
        d = delta.x() if self.horizontal else -delta.y()
        f = self.start_fraction + d / self.length
        f = max(MIN_FRACTION, min(1.0 - MIN_FRACTION, f))
        try:
            self.child.setSplitFraction(f)
        except hou.OperationFailed:
            pass


def _find_split_for_edge(pane, edge):
    """Walk up from pane to the split whose divider is this border of pane."""
    want_horizontal = edge in (_LEFT, _RIGHT)
    # The divider is child 0's right/bottom border or child 1's left/top border.
    want_index = 0 if edge in (_RIGHT, _BOTTOM) else 1

    child = pane
    parent = child.getSplitParent()
    while parent is not None:
        side_by_side = _is_side_by_side(parent)
        if side_by_side is None:
            return None
        c0 = parent.getSplitChild(0)
        index = 0 if (c0 is not None and c0.id() == child.id()) else 1
        if side_by_side == want_horizontal and index == want_index:
            rect = _geometry(parent)
            if rect is None:
                return None
            try:
                start = float(child.getSplitFraction())
            except hou.OperationFailed:
                return None
            length = rect.width() if want_horizontal else rect.height()
            return _AxisDrag(child, want_horizontal, start, length)
        child, parent = parent, parent.getSplitParent()
    return None  # reached the window edge: nothing to resize


def _drags_at(pos):
    """The splits a press at global pos would grab, or an empty list."""
    try:
        pane = hou.ui.paneUnderCursor()
    except hou.Error:
        return []
    pane = _leaf_pane_under(pane, pos)
    if pane is None or pane.isMaximized():
        return []
    rect = _geometry(pane)
    if rect is None or not rect.contains(pos):
        return []

    drags = []
    for edge in _edges_near(rect, pos):
        drag = _find_split_for_edge(pane, edge)
        if drag is not None:
            drags.append((edge, drag))
    return drags


def _cursor_for(edges):
    horizontal = [e for e in edges if e in (_LEFT, _RIGHT)]
    vertical = [e for e in edges if e in (_TOP, _BOTTOM)]
    if horizontal and vertical:
        h, v = horizontal[0], vertical[0]
        if (h, v) in ((_LEFT, _TOP), (_RIGHT, _BOTTOM)):
            return Qt.SizeFDiagCursor
        return Qt.SizeBDiagCursor
    if horizontal:
        return Qt.SizeHorCursor
    return Qt.SizeVerCursor


class _PaneResizeFilter(QtCore.QObject):
    def __init__(self):
        super(_PaneResizeFilter, self).__init__()
        self._drags = []            # [_AxisDrag] while a drag is in progress
        self._start = None          # QPoint where the drag began
        self._cursor_shape = None   # override cursor currently shown, if any
        self._swallow_context_menu = False

    # -- cursor ----------------------------------------------------------

    def _set_cursor(self, shape):
        if shape == self._cursor_shape:
            return
        app = QtWidgets.QApplication.instance()
        if shape is None:
            app.restoreOverrideCursor()
        elif self._cursor_shape is None:
            app.setOverrideCursor(QtGui.QCursor(shape))
        else:
            app.changeOverrideCursor(QtGui.QCursor(shape))
        self._cursor_shape = shape

    # -- event filter ----------------------------------------------------

    def eventFilter(self, obj, event):
        et = event.type()
        try:
            if et == QEvent.Type.MouseButtonPress:
                return self._on_press(event)
            if et == QEvent.Type.MouseMove:
                return self._on_move(event)
            if et == QEvent.Type.MouseButtonRelease:
                return self._on_release(event)
            if et == QEvent.Type.MouseButtonDblClick:
                return bool(self._drags)
            if et == QEvent.Type.ContextMenu and self._swallow_context_menu:
                # Windows synthesises a context-menu event on RMB release.
                self._swallow_context_menu = False
                return True
            if et == QEvent.Type.KeyRelease and self._cursor_shape is not None and not self._drags:
                if not _modifiers_match(QtWidgets.QApplication.queryKeyboardModifiers()):
                    self._set_cursor(None)
        except Exception:
            # Never let a bug here break Houdini's event loop; drop any drag.
            self._end_drag()
            import traceback
            traceback.print_exc()
        return False

    def _on_press(self, event):
        if self._drags:
            return True  # another button during a drag: ignore it
        if event.button() != BUTTON or not _modifiers_match(event.modifiers()):
            return False
        pos = _global_pos(event)
        found = _drags_at(pos)
        if not found:
            return False
        self._drags = [drag for _, drag in found]
        self._start = pos
        self._set_cursor(_cursor_for([edge for edge, _ in found]))
        return True

    def _on_move(self, event):
        if self._drags:
            delta = _global_pos(event) - self._start
            for drag in self._drags:
                drag.apply(delta)
            return True

        if not SHOW_HOVER_CURSOR:
            return False
        if _flag_int(event.buttons()) == 0 and _modifiers_match(event.modifiers()):
            found = _drags_at(_global_pos(event))
            self._set_cursor(_cursor_for([e for e, _ in found]) if found else None)
        elif self._cursor_shape is not None:
            self._set_cursor(None)
        return False

    def _on_release(self, event):
        if not self._drags or event.button() != BUTTON:
            return False
        for drag in self._drags:
            drag.apply(_global_pos(event) - self._start)
        self._end_drag()
        self._swallow_context_menu = BUTTON == Qt.RightButton
        return True

    def _end_drag(self):
        self._drags = []
        self._start = None
        self._set_cursor(None)


# ------------------------------------------------------------------ public --

_filter = None  # keep a reference or Qt's filter object is garbage collected


def install():
    """Start handling modifier+drag pane resizing. Safe to call repeatedly."""
    global _filter
    if _filter is not None or not hou.isUIAvailable():
        return
    app = QtWidgets.QApplication.instance()
    if app is None:
        # pythonrc.py can run before Qt is up; try again from the event loop.
        hou.ui.addEventLoopCallback(_deferred_install)
        return
    _filter = _PaneResizeFilter()
    app.installEventFilter(_filter)


def _deferred_install():
    if QtWidgets.QApplication.instance() is None:
        return
    hou.ui.removeEventLoopCallback(_deferred_install)
    install()


def uninstall():
    global _filter
    if _filter is None:
        return
    app = QtWidgets.QApplication.instance()
    if app is not None:
        app.removeEventFilter(_filter)
    _filter._end_drag()
    _filter = None


def is_installed():
    return _filter is not None


def toggle():
    """Shelf-tool helper: switch the handler on or off and report the state."""
    if is_installed():
        uninstall()
        hou.ui.setStatusMessage("Pane resize-anywhere: off")
    else:
        install()
        hou.ui.setStatusMessage("Pane resize-anywhere: on")
