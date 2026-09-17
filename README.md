# Houdini Pane Resize Anywhere

KDE-style "resize anywhere" for SideFX Houdini panes. Hold a modifier, press
the mouse anywhere near a pane's border, and drag — the split that owns that
border follows the mouse. No more hunting for the thin divider between panes.

**Demo:** https://youtu.be/bUvCURDZHEA

## How it works

- **Ctrl + Alt + right-drag** near any pane border resizes the split that
  owns that border. Press near a corner to drag both splits at once.
- The grab zone is the outer quarter of the pane on each side (configurable).
- Borders that are window edges (nothing to resize) are ignored and the click
  passes through to Houdini as normal.
- The cursor changes to a resize arrow while the modifiers are held, showing
  what a press would grab.

It works in every pane type — viewport, network editor, parameters, Python
panels, and so on — because it filters Qt's application-level mouse events
rather than using a per-pane-type hook such as `nodegraphhooks`.

## Install

1. Copy `pane_resize_anywhere.py` to `$HOUDINI_USER_PREF_DIR/scripts/python/`
   (on Windows this is usually `Documents\houdiniXX.X\scripts\python\`).
2. Add the two lines from `pythonrc.py` to
   `$HOUDINI_USER_PREF_DIR/scripts/pythonrc.py` (create the file if it does
   not exist):

   ```python
   import pane_resize_anywhere
   pane_resize_anywhere.install()
   ```

3. Restart Houdini.

`install()` is a no-op outside the UI (hython, hbatch) and defers itself until
the Qt application exists, so it is safe to call from `pythonrc.py`.

### Optional shelf tool

To toggle the handler on and off, create a shelf tool with this script:

```python
import pane_resize_anywhere
pane_resize_anywhere.toggle()
```

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
| `EDGE_MARGIN` | `0.25` | How close to a border the press must be. Below 1 it is a fraction of the pane's width/height (`0.5` = anywhere in the pane); 1 or above is a fixed pixel distance. |
| `MIN_FRACTION` | `0.02` | Stops a split from being collapsed completely. |
| `SHOW_HOVER_CURSOR` | `True` | Show a resize cursor while the modifiers are held. |

## Requirements

- Houdini 20.5 or newer (PySide6). Older PySide2 builds are supported by the
  import fallback but have not been tested.
- Uses only public HOM APIs: `hou.ui.paneUnderCursor()`,
  `hou.Pane.qtScreenGeometry()`, `getSplitParent()` / `getSplitChild()` and
  `getSplitFraction()` / `setSplitFraction()`.

## License

MIT
