# Run by Houdini once the UI is ready (see $HOUDINI_PATH/pythonX.Ylibs/uiready.py).
# The module itself lives in ../python, which the package file puts on PYTHONPATH.
import pane_resize_anywhere

pane_resize_anywhere.install()
