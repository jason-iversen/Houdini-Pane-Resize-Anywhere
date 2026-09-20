# Houdini Pane Resize Anywhere

KDE-style "resize anywhere" for SideFX Houdini panes. Hold a modifier, press
the mouse anywhere near a pane's border, and drag — the split that owns that
border follows the mouse. No more hunting for the thin divider between panes.

**Demo:** https://youtu.be/-a2CH3XuB10

## How it works

- **Ctrl + Alt + right-drag** near any pane border resizes the split that
  owns that border. Press near a corner to drag both splits at once.
- The grab zone is the outer 40% of the pane on each side (configurable).
- Borders that are window edges (nothing to resize) are ignored and the click
  passes through to Houdini as normal. So is any split whose divider does not
  actually land on the border it was found for (see
  [Troubleshooting](#troubleshooting)).
- While the modifiers are held, the cursor changes to a resize arrow and a
  dimmed band is drawn over each border a press would grab, so you can see
  what you are about to resize before pressing anything.
- A band follows the mouse exactly during the drag, while moving the splits
  themselves is throttled, so a 3D viewport does not redraw once per mouse
  move (see [Drag performance](#drag-performance)).

It works in every pane type — viewport, network editor, parameters, Python
panels, and so on — because it filters Qt's application-level mouse events
rather than using a per-pane-type hook such as `nodegraphhooks`.

## Install

This repository is a [Houdini package](https://www.sidefx.com/docs/houdini/ref/plugins.html),
so nothing needs to be copied into your Houdini preferences.

1. Clone or download this repository somewhere permanent.
2. Create `$HOUDINI_USER_PREF_DIR/packages/pane_resize_anywhere.json`
   (on Windows `$HOUDINI_USER_PREF_DIR` is usually `Documents\houdiniXX.X`)
   containing the path to the repository:

   ```json
   { "package_path": "C:/path/to/Houdini Pane Resize Anywhere" }
   ```

   This makes Houdini load the package file shipped in the repository, which
   adds `houdini/` to `HOUDINI_PATH` and `houdini/python/` to `PYTHONPATH`.

3. Restart Houdini. `houdini/pythonX.Ylibs/uiready.py` installs the handler
   as soon as the UI is ready.

Alternatively, copy `pane_resize_anywhere.json` into your `packages` folder
and change `PANE_RESIZE_ANYWHERE` to the absolute path of the repository's
`houdini/` folder.

### Layout

```
Houdini Pane Resize Anywhere/
├── pane_resize_anywhere.json        # package file (HOUDINI_PATH + PYTHONPATH)
└── houdini/                         # added to HOUDINI_PATH
    ├── python/
    │   └── pane_resize_anywhere.py  # the event handler
    ├── python3.11libs/uiready.py    # startup hook, Houdini 20.5 / 21 (Python 3.11)
    └── python3.13libs/uiready.py    # startup hook, Houdini 22 (Python 3.13)
```

Houdini only runs the `uiready.py` that matches its own Python version. For a
Houdini build with a different Python, add a `pythonX.Ylibs/uiready.py` with
the same two lines.

### Optional shelf tool

To toggle the handler on and off, create a shelf tool with this script:

```python
import pane_resize_anywhere
pane_resize_anywhere.toggle()
```

It reports the new state in the status bar, naming the current shortcut when
it switches on, so a rebound `MODIFIERS` or `BUTTON` is never a guess.

### Reloading during development

```python
import importlib, pane_resize_anywhere as m
m.uninstall(); importlib.reload(m); m.install()
```

## Configuration

Settings are at the top of `pane_resize_anywhere.py`:

| Setting | Default | Description |
| --- | --- | --- |
| `MODIFIERS` | `Ctrl + Alt` | Modifier keys that must be held (and no others). Ctrl+Alt avoids Houdini's own Alt/Space viewport navigation. |
| `BUTTON` | Right mouse | Mouse button that starts the drag. |
| `EDGE_MARGIN` | `0.4` | How close to a border the press must be. Below 1 it is a fraction of the pane's width/height (`0.5` = anywhere in the pane); 1 or above is a fixed pixel distance. |
| `MIN_FRACTION` | `0.02` | Stops a split from being collapsed completely. |
| `SHOW_HOVER_CURSOR` | `True` | Show a resize cursor while the modifiers are held. |
| `SHOW_HOVER_BANDS` | `True` | Draw a band over each border a press would grab while the modifiers are held. |
| `HOVER_BAND_OPACITY` | `0.45` | Opacity of those candidate bands. The bands of a drag are always opaque. |
| `DRAG_MODE` | `"live"` | `"live"` moves the splits during the drag, throttled to `DRAG_INTERVAL_MS`. `"preview"` draws a rubber band during the drag and moves the splits once, on release. |
| `DRAG_INTERVAL_MS` | `30` | Shortest interval between split updates in `"live"` mode (30 ms ≈ 33 updates/second). |
| `BAND_THICKNESS` | `4` | Thickness in pixels of a rubber band. |
| `BORDER_SLOP` | `12` | How far a divider may sit from the border it was found for, and how far a split's rect may fall short of containing the pane, before that split is dropped. |

### Drag performance

Moving a split relayouts the panes, which makes every visible 3D viewport
redraw. That is the one expensive thing a resize does, and a mouse can easily
send several hundred move events per second, so the handler never moves a
split once per move event. Both modes draw the rubber bands, which follow the
mouse exactly and cost nothing but a 4 pixel window move; they differ in how
often the panes themselves are relayouted:

- **`DRAG_MODE = "live"`** (default) applies the newest mouse position at most
  once every `DRAG_INTERVAL_MS`, and applies the exact final position on
  release. Intermediate moves are coalesced rather than queued, so the layout
  never lags behind the mouse by more than one interval. Raise
  `DRAG_INTERVAL_MS` if a heavy scene still stutters.
- **`DRAG_MODE = "preview"`** does not touch the layout during the drag at all.
  The bands show where the dividers would land and the splits move once, when
  the mouse comes up, so nothing else redraws until then.

A band is a frameless top level window, so it also draws over the viewport,
which is a native OpenGL window that would otherwise cover a plain child
widget.

A corner drag moves two dividers, one of them nested inside the other's split.
The nested split is bounded by the outer divider, so the inner band does not
keep a fixed length: its end follows the outer band as that one moves, and the
two stay joined at the corner. The outer band spans its own split whatever the
inner divider does, so it keeps its full length.

## Troubleshooting

**A border will not grab.** Walking up the pane tree reports a split but
cannot prove it is the pane's neighbour, and the geometry Houdini hands back
does not always match what is on screen. A split that owns a pane's border
contains that pane and puts its divider on that border, so both are checked
and whatever fails either one is dropped rather than drawn — better a border
that does not respond than a band across the middle of the window. If a border
that should work is being dropped, raise `BORDER_SLOP`.

## Requirements

- Houdini 20.0 or newer. 
- Uses only public HOM APIs: `hou.ui.paneUnderCursor()`,
  `hou.Pane.qtScreenGeometry()`, `getSplitParent()` / `getSplitChild()` and
  `getSplitFraction()` / `setSplitFraction()`.

## License

MIT
