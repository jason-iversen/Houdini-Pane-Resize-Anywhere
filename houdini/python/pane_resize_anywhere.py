"""pane_resize_anywhere.py -- KDE-style "resize anywhere" for Houdini panes.

Hold MODIFIERS and press BUTTON anywhere inside a pane within EDGE_MARGIN
pixels of one of its borders, then drag.  The split that owns that border
follows the mouse.  Pressing near a corner drags both splits at once.
Borders that are window edges (nothing to resize) are ignored.

Moving a split relayouts the panes and makes every visible 3D viewport
redraw, so DRAG_MODE decides how often that happens: "live" moves the splits
at most once every DRAG_INTERVAL_MS, "preview" draws a rubber band during the
drag and moves them once, on release.

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
EDGE_MARGIN = 0.4

# Keep splits from collapsing completely.
MIN_FRACTION = 0.02

# Change the mouse cursor while MODIFIERS are held to show what a press would
# grab.  Costs one hou.ui.paneUnderCursor() call per mouse move while held.
SHOW_HOVER_CURSOR = True

# Also draw the rubber bands over the borders a press would grab while
# MODIFIERS are held, before any drag starts.
SHOW_HOVER_BANDS = True

# Opacity of those candidate bands.  The bands of a drag are always opaque.
HOVER_BAND_OPACITY = 0.45

# How the drag updates the layout.  Both modes draw the rubber bands, which
# follow the mouse exactly; the difference is how often the panes themselves
# are relayouted.  That is the expensive part, because it makes every visible
# 3D viewport redraw, so neither mode does it once per mouse move:
#   "live"    -- move the splits during the drag, but at most once every
#                DRAG_INTERVAL_MS.
#   "preview" -- move the splits once, on release.  Nothing but the bands
#                redraws during the drag.
DRAG_MODE = "live"

# Shortest interval in milliseconds between split updates in "live" mode.
DRAG_INTERVAL_MS = 30

# Thickness in pixels of a rubber band.
BAND_THICKNESS = 4

# How far a divider may sit from the pane border it was found for, and how far
# a split's rect may fall short of containing the pane, in pixels.  Walking up
# the pane tree reports a split but cannot prove it is the pane's neighbour,
# and Houdini sometimes hands back a rect that no longer matches the screen, so
# both are checked before a split is drawn or dragged; what fails either one
# puts a band across the middle of the window instead of on a border.  Raise it
# if your theme has unusually wide divider gaps.
BORDER_SLOP = 12

# --------------------------------------------------------------- internals --

_LEFT, _RIGHT, _TOP, _BOTTOM = "left", "right", "top", "bottom"

_MODIFIER_NAMES = (
    (Qt.ControlModifier, "Ctrl"),
    (Qt.AltModifier, "Alt"),
    (Qt.ShiftModifier, "Shift"),
    (Qt.MetaModifier, "Meta"),
)

_BUTTON_NAMES = (
    (Qt.LeftButton, "left"),
    (Qt.MiddleButton, "middle"),
    (Qt.RightButton, "right"),
)

_ZERO = QtCore.QPoint(0, 0)  # "wherever the divider is now", for hover bands


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


def _border_pos(rect, edge):
    """Screen x or y of one border of a pane."""
    if edge == _LEFT:
        return rect.left()
    if edge == _RIGHT:
        return rect.right()
    if edge == _TOP:
        return rect.top()
    return rect.bottom()


def _owns_border(drag, rect, edge):
    """True if this drag's split really is that border of a pane with this rect.

    A split that owns a pane's border contains that pane and puts its divider
    on that border.  Either test failing means the geometry Houdini reported
    does not hang together -- a stale rect, a child order read backwards -- and
    the split is not the neighbour the walk up the tree took it for.
    """
    room = drag.rect.adjusted(-BORDER_SLOP, -BORDER_SLOP, BORDER_SLOP, BORDER_SLOP)
    return (
        room.contains(rect)
        and abs(drag.divider_pos(_ZERO) - _border_pos(rect, edge)) <= BORDER_SLOP
    )


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

    __slots__ = ("child", "horizontal", "start_fraction", "length", "rect", "edge")

    def __init__(self, child, horizontal, start_fraction, length, rect, edge):
        self.child = child  # a child of the split; set/getSplitFraction act on its parent
        self.horizontal = horizontal
        self.start_fraction = start_fraction
        self.length = max(float(length), 1.0)
        self.rect = rect  # the split's screen rect, for the rubber band
        self.edge = edge  # which border of the pressed pane this divider is

    def fraction_for(self, delta):
        # The fraction is child 0's share, so the divider always moves with
        # the mouse no matter which side of it the drag started on.  Houdini's
        # UI is y-up, so a top/bottom split's fraction grows as the divider
        # moves up the screen, i.e. against Qt's y-down mouse delta.
        d = delta.x() if self.horizontal else -delta.y()
        f = self.start_fraction + d / self.length
        return max(MIN_FRACTION, min(1.0 - MIN_FRACTION, f))

    def apply(self, delta):
        try:
            self.child.setSplitFraction(self.fraction_for(delta))
        except hou.OperationFailed:
            pass

    def divider_pos(self, delta):
        """Screen x of a vertical divider, or y of a horizontal one."""
        f = self.fraction_for(delta)
        if self.horizontal:
            return int(round(self.rect.left() + f * self.length))
        # y-up: child 0's share is measured from the bottom of the split.
        return int(round(self.rect.bottom() - f * self.length))

    def band_rect(self, delta):
        """Screen rect of the divider as this delta would leave it."""
        r, t = self.rect, max(int(BAND_THICKNESS), 1)
        pos = self.divider_pos(delta)
        if self.horizontal:
            return QtCore.QRect(pos - t // 2, r.top(), t, r.height())
        return QtCore.QRect(r.left(), pos - t // 2, r.width(), t)


def _join_band(rect, outer, outer_edge):
    """Stretch an inner band to meet the outer band of a corner drag.

    A corner drag moves two dividers, one of them nested inside the other's
    split.  The nested split is bounded by the outer divider, so its divider
    ends wherever that one currently is: the inner band's length is not fixed,
    it follows the outer band instead of sliding out of contact with it.
    """
    joined = QtCore.QRect(rect)
    if outer_edge == _LEFT:
        joined.setLeft(outer.left())
    elif outer_edge == _RIGHT:
        joined.setRight(outer.right())
    elif outer_edge == _TOP:
        joined.setTop(outer.top())
    else:
        joined.setBottom(outer.bottom())
    return joined if joined.isValid() else rect


def _band_rects(drags, delta):
    """Where the bands go, joined at the corner when two are dragged.

    One of a corner drag's two splits is nested inside the other, which is the
    one whose rect contains the other's.  The outer divider spans its whole
    split whatever the inner one does, so only the inner band has to follow,
    and it does so on the side the outer divider is on.
    """
    rects = [drag.band_rect(delta) for drag in drags]
    if len(rects) == 2:
        a, b = drags
        if a.rect.contains(b.rect):
            rects[1] = _join_band(rects[1], rects[0], a.edge)
        elif b.rect.contains(a.rect):
            rects[0] = _join_band(rects[0], rects[1], b.edge)
    return rects


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
            return _AxisDrag(child, want_horizontal, start, length, rect, edge)
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
        if drag is not None and _owns_border(drag, rect, edge):
            drags.append(drag)
    return drags


def _cursor_for(drags):
    edges = [drag.edge for drag in drags]
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


class _RubberBand(QtWidgets.QWidget):
    """A thin frameless window showing where a divider would land.

    It is a top level window rather than a child widget so that it also draws
    over the 3D viewport, which is a native OpenGL window and would otherwise
    stack above any sibling widget.
    """

    def __init__(self):
        super(_RubberBand, self).__init__(
            None,
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool
            | Qt.WindowDoesNotAcceptFocus
            | Qt.WindowTransparentForInput,
        )
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.opacity = 1.0  # Qt rounds windowOpacity(), so keep what was asked

    def show_at(self, rect, opacity):
        if self.opacity != opacity:
            self.opacity = opacity
            self.setWindowOpacity(opacity)
        self.setGeometry(rect)
        if not self.isVisible():
            self.show()
            self.raise_()

    def paintEvent(self, event):
        color = QtWidgets.QApplication.palette().color(QtGui.QPalette.Highlight)
        painter = QtGui.QPainter(self)
        painter.fillRect(self.rect(), color)
        painter.end()


class _PaneResizeFilter(QtCore.QObject):
    def __init__(self):
        super(_PaneResizeFilter, self).__init__()
        self._drags = []            # [_AxisDrag] while a drag is in progress
        self._start = None          # QPoint where the drag began
        self._delta = None          # newest mouse delta, not applied yet
        self._cursor_shape = None   # override cursor currently shown, if any
        self._swallow_context_menu = False
        self._bands = []            # rubber bands, kept and reused between drags
        self._timer = QtCore.QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._flush)
        self._since_apply = QtCore.QElapsedTimer()

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

    # -- rubber band -----------------------------------------------------

    def _show_bands(self, drags, delta, opacity=1.0):
        rects = _band_rects(drags, delta)
        for i, rect in enumerate(rects):
            while len(self._bands) <= i:
                self._bands.append(_RubberBand())
            self._bands[i].show_at(rect, opacity)
        for band in self._bands[len(rects):]:  # a corner grab left for an edge
            band.hide()

    def _hide_bands(self):
        for band in self._bands:
            if band.isVisible():
                band.hide()

    def _clear_hover(self):
        """Drop the candidate bands and cursor shown while MODIFIERS are held."""
        self._set_cursor(None)
        self._hide_bands()

    def _hover_shown(self):
        return self._cursor_shape is not None or any(
            band.isVisible() for band in self._bands
        )

    def _destroy_bands(self):
        for band in self._bands:
            band.hide()
            band.deleteLater()
        self._bands = []

    # -- applying the drag -----------------------------------------------

    def _queue(self, delta):
        """Take a new mouse position: the bands always follow it, the splits
        only in "live" mode, and then no more often than DRAG_INTERVAL_MS."""
        self._delta = delta
        self._show_bands(self._drags, delta)
        if DRAG_MODE == "preview":
            return
        left = DRAG_INTERVAL_MS - self._since_apply.elapsed()
        if left <= 0:
            self._flush()
        elif not self._timer.isActive():
            self._timer.start(int(left))

    def _flush(self):
        """Move the splits to the newest mouse position."""
        self._timer.stop()
        if not self._drags or self._delta is None:
            return
        for drag in self._drags:
            drag.apply(self._delta)
        self._since_apply.restart()

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
            if et == QEvent.Type.KeyRelease and not self._drags and self._hover_shown():
                if not _modifiers_match(QtWidgets.QApplication.queryKeyboardModifiers()):
                    self._clear_hover()
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
            self._clear_hover()  # a click we do not take ends the hover preview
            return False
        pos = _global_pos(event)
        found = _drags_at(pos)
        if not found:
            self._clear_hover()
            return False
        self._drags = found
        self._start = pos
        self._delta = _ZERO
        self._since_apply.start()
        self._set_cursor(_cursor_for(found))
        self._show_bands(found, self._delta)
        return True

    def _on_move(self, event):
        if self._drags:
            self._queue(_global_pos(event) - self._start)
            return True

        if not (SHOW_HOVER_CURSOR or SHOW_HOVER_BANDS):
            return False
        if _flag_int(event.buttons()) == 0 and _modifiers_match(event.modifiers()):
            # What a press here would grab, drawn where it would grab it.
            found = _drags_at(_global_pos(event))
            if SHOW_HOVER_CURSOR:
                self._set_cursor(_cursor_for(found) if found else None)
            if SHOW_HOVER_BANDS:
                if found:
                    self._show_bands(found, _ZERO, HOVER_BAND_OPACITY)
                else:
                    self._hide_bands()
        elif self._hover_shown():
            self._clear_hover()
        return False

    def _on_release(self, event):
        if not self._drags or event.button() != BUTTON:
            return False
        self._timer.stop()
        self._hide_bands()
        # The exact final position, whatever the bands and the timer did.
        for drag in self._drags:
            drag.apply(_global_pos(event) - self._start)
        self._end_drag()
        self._swallow_context_menu = BUTTON == Qt.RightButton
        return True

    def _end_drag(self):
        self._timer.stop()
        self._drags = []
        self._start = None
        self._delta = None
        self._hide_bands()
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
    _filter._destroy_bands()
    _filter = None


def is_installed():
    return _filter is not None


def shortcut():
    """MODIFIERS and BUTTON as text, e.g. "Ctrl+Alt+right-drag"."""
    mods = [name for flag, name in _MODIFIER_NAMES
            if _flag_int(MODIFIERS) & _flag_int(flag)]
    button = "mouse"
    for flag, name in _BUTTON_NAMES:
        if _flag_int(BUTTON) == _flag_int(flag):
            button = name
    return "+".join(mods + [button + "-drag"])


def toggle():
    """Shelf-tool helper: switch the handler on or off and report the state."""
    if is_installed():
        uninstall()
        hou.ui.setStatusMessage("Pane resize-anywhere off")
    else:
        install()
        hou.ui.setStatusMessage(
            "Pane resize-anywhere on: %s near a pane border resizes it"
            % shortcut()
        )
