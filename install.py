"""Install this handler into one Houdini's preferences.

    import sys
    sys.path.insert(0, "C:/path/to/Houdini Pane Resize Anywhere")
    import install
    install.install()

Run that from the Python Shell of the Houdini you want it in, or from a
shell as `hython install.py`, or from any Python at all by naming the
preferences folder yourself:

    python install.py "C:/Users/you/OneDrive/Documents/houdini22.0"

It copies pane_resize_anywhere.py into that folder's scripts/python, which
every Houdini Python version reads, and has pythonrc.py there switch the
handler on when the UI comes up.  Nothing in it depends on which Python the
Houdini was built with, so the same install keeps working across versions.

install_package() is the other way round: it leaves this repository where it
is and registers it as a Houdini package, which needs a startup hook for that
Houdini's exact Python version and so has to be redone for a build with a new
one.  uninstall() undoes either.
"""

import json
import os
import shutil
import sys

MODULE = "pane_resize_anywhere.py"
PACKAGE_FILE = "pane_resize_anywhere.json"

_MARK = "# Houdini Pane Resize Anywhere"

_PYTHONRC = """\
%s -- added by its install.py, remove this block to stop it loading.
try:
    import pane_resize_anywhere
    pane_resize_anywhere.install_when_ready()
except Exception:
    import traceback
    traceback.print_exc()
# end Houdini Pane Resize Anywhere
""" % _MARK

_UIREADY = """\
# Run by Houdini once the UI is ready (see $HOUDINI_PATH/pythonX.Ylibs/uiready.py).
# The module itself lives in ../python, which the package file puts on PYTHONPATH.
import pane_resize_anywhere

pane_resize_anywhere.install()
"""


# ------------------------------------------------------------- where things are --


def repository(path=None):
    """This repository's root: the folder holding the package file."""
    if path is None:
        path = globals().get("__file__")
    if path is None:
        raise RuntimeError(
            "say which repository: install(root=r'C:/path/to/Houdini Pane "
            "Resize Anywhere') -- this was exec()d, so it cannot tell where "
            "it is on disk"
        )
    root = os.path.dirname(os.path.abspath(path)) if os.path.isfile(path) else path
    if not os.path.isfile(os.path.join(root, PACKAGE_FILE)):
        raise RuntimeError("%s is not the repository: no %s in it"
                           % (root, PACKAGE_FILE))
    return root


def preferences(prefs=None):
    """A Houdini preferences folder: the one given, or the running Houdini's."""
    if prefs:
        prefs = os.path.abspath(os.path.expanduser(prefs))
        # Naming the scripts/python folder itself is the obvious thing to do.
        tail = os.path.join("scripts", "python")
        if prefs.endswith(tail):
            prefs = prefs[:-len(tail)].rstrip("\\/")
        return prefs
    try:
        import hou
        return hou.homeHoudiniDirectory()
    except ImportError:
        pass
    prefs = os.environ.get("HOUDINI_USER_PREF_DIR")
    if not prefs:
        raise RuntimeError(
            "no Houdini here: name the preferences folder, e.g. "
            "install(prefs=r'C:/Users/you/Documents/houdini22.0')"
        )
    return prefs


def scripts_python(prefs=None):
    """<preferences>/scripts/python, every Houdini Python's own module folder."""
    return os.path.join(preferences(prefs), "scripts", "python")


# -------------------------------------------------------------------- installing --


def install(prefs=None, root=None):
    """Copy the handler into a Houdini's scripts/python and start it there."""
    source = os.path.join(repository(root), "houdini", "python", MODULE)
    folder = scripts_python(prefs)
    if not os.path.isdir(folder):
        os.makedirs(folder)

    shutil.copyfile(source, os.path.join(folder, MODULE))
    print("wrote  %s" % os.path.join(folder, MODULE))

    pythonrc = os.path.join(folder, "pythonrc.py")
    existing = ""
    if os.path.isfile(pythonrc):
        with open(pythonrc) as current:
            existing = current.read()
    if _MARK in existing:
        print("have   %s, already loads it" % pythonrc)
    else:
        with open(pythonrc, "a") as out:
            if existing and not existing.endswith("\n"):
                out.write("\n")
            if existing:
                out.write("\n")
            out.write(_PYTHONRC)
        print("%s %s" % ("added to" if existing else "wrote   ", pythonrc))

    _switch_on(folder)
    return folder


def install_package(root=None, force=False):
    """Register this repository as a Houdini package instead of copying it.

    The repository stays the one copy of the handler, so edits to it are live,
    but Houdini only runs the startup hook matching its own Python version, so
    this writes that too, for the Python running right now.
    """
    root = repository(root)

    libs = os.path.join(root, "houdini",
                        "python%d.%dlibs" % sys.version_info[:2])
    hook = os.path.join(libs, "uiready.py")
    if os.path.isfile(hook):
        print("have   %s" % hook)
    else:
        if not os.path.isdir(libs):
            os.makedirs(libs)
        with open(hook, "w") as out:
            out.write(_UIREADY)
        print("wrote  %s" % hook)

    packages = os.path.join(preferences(), "packages")
    if not os.path.isdir(packages):
        os.makedirs(packages)
    package = os.path.join(packages, PACKAGE_FILE)

    if os.path.isfile(package) and not force:
        try:
            with open(package) as current:
                points_at = json.load(current).get("package_path")
        except ValueError:
            points_at = None
        if points_at and os.path.abspath(points_at) != root:
            print("kept   %s\n       it points at %s, not this repository.\n"
                  "       install_package(force=True) to repoint it."
                  % (package, points_at))
            return package
        print("have   %s" % package)
    else:
        with open(package, "w") as out:
            json.dump({"package_path": root}, out, indent=4)
            out.write("\n")
        print("wrote  %s" % package)

    _switch_on(os.path.join(root, "houdini", "python"))
    return package


def uninstall(prefs=None):
    """Remove whichever of the two installs is there, and stop the handler."""
    folder = scripts_python(prefs)
    module = os.path.join(folder, MODULE)
    if os.path.isfile(module):
        os.remove(module)
        print("removed %s" % module)

    pythonrc = os.path.join(folder, "pythonrc.py")
    if os.path.isfile(pythonrc):
        with open(pythonrc) as current:
            lines = current.readlines()
        kept, dropping = [], False
        for line in lines:
            if line.startswith(_MARK):
                dropping = True
            if not dropping:
                kept.append(line)
            if dropping and line.startswith("# end Houdini Pane Resize"):
                dropping = False
        if len(kept) != len(lines):
            with open(pythonrc, "w") as out:
                out.write("".join(kept).rstrip("\n") + "\n" if kept else "")
            print("edited  %s" % pythonrc)

    package = os.path.join(preferences(prefs), "packages", PACKAGE_FILE)
    if os.path.isfile(package):
        os.remove(package)
        print("removed %s" % package)

    try:
        import pane_resize_anywhere
    except ImportError:
        return
    pane_resize_anywhere.uninstall()
    print("        switched off in this session")


def _switch_on(folder):
    """Start the handler in this session, so there is nothing to restart."""
    try:
        import hou
        if not hou.isUIAvailable():
            print("       start Houdini to pick it up")
            return
    except ImportError:
        print("       start Houdini to pick it up")
        return

    if folder not in sys.path:
        sys.path.insert(0, folder)
    import pane_resize_anywhere
    pane_resize_anywhere.install()
    print("       on now: %s near a pane border resizes it, %s pans a "
          "parameter pane"
          % (pane_resize_anywhere.shortcut(),
             pane_resize_anywhere.shortcut(pane_resize_anywhere.PAN_BUTTON)))


if __name__ == "__main__":
    install(prefs=sys.argv[1] if len(sys.argv) > 1 else None)
