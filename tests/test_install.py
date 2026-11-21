"""Tests for install.py, against a fake Houdini preferences folder.

    python tests/test_install.py

Needs nothing but the standard library: hou is stubbed, and everything is
written into a temporary copy of the repository.
"""

import json
import os
import shutil
import sys
import tempfile
import types

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, _ROOT)

import install


def fake_houdini():
    """A copy of the repository and an empty preferences folder beside it."""
    temp = tempfile.mkdtemp(prefix="pane_resize_anywhere_")
    repo = os.path.join(temp, "repo")
    shutil.copytree(_ROOT, repo, ignore=shutil.ignore_patterns(
        ".git", "__pycache__", "*.pyc"))
    prefs = os.path.join(temp, "houdini22.0")

    hou = types.ModuleType("hou")
    hou.homeHoudiniDirectory = lambda: prefs
    hou.isUIAvailable = lambda: False      # no UI: nothing is switched on
    sys.modules["hou"] = hou
    return temp, repo, prefs


def test_the_target_folder_can_be_named_either_way():
    temp, repo, prefs = fake_houdini()
    try:
        wanted = os.path.join(prefs, "scripts", "python")
        assert install.scripts_python(prefs) == wanted
        assert install.scripts_python(wanted) == wanted            # as written
        assert install.scripts_python(wanted.replace(os.sep, "/")) == wanted
        assert install.scripts_python() == wanted                  # from Houdini
    finally:
        shutil.rmtree(temp)


def test_install_copies_the_module_and_hooks_pythonrc():
    temp, repo, prefs = fake_houdini()
    try:
        folder = install.install(prefs=prefs, root=repo)
        module = os.path.join(folder, "pane_resize_anywhere.py")
        pythonrc = os.path.join(folder, "pythonrc.py")

        source = os.path.join(repo, "houdini", "python", "pane_resize_anywhere.py")
        assert open(module).read() == open(source).read()

        body = open(pythonrc).read()
        assert "pane_resize_anywhere.install_when_ready()" in body
        assert body.count(install._MARK) == 1
        compile(body, pythonrc, "exec")

        # Running it again refreshes the copy without hooking it twice.
        with open(module, "w") as out:
            out.write("# an older copy\n")
        install.install(prefs=folder, root=repo)
        assert open(module).read() == open(source).read()
        assert open(pythonrc).read().count(install._MARK) == 1
    finally:
        shutil.rmtree(temp)


def test_an_existing_pythonrc_is_appended_to_and_left_behind():
    temp, repo, prefs = fake_houdini()
    try:
        folder = os.path.join(prefs, "scripts", "python")
        os.makedirs(folder)
        pythonrc = os.path.join(folder, "pythonrc.py")
        theirs = "import os\nprint('my own startup')"     # no trailing newline
        with open(pythonrc, "w") as out:
            out.write(theirs)

        install.install(prefs=prefs, root=repo)
        body = open(pythonrc).read()
        assert body.startswith(theirs + "\n")
        assert "install_when_ready()" in body
        compile(body, pythonrc, "exec")

        install.uninstall(prefs=prefs)
        assert open(pythonrc).read() == theirs + "\n"     # only our block went
        assert not os.path.exists(os.path.join(folder, "pane_resize_anywhere.py"))
    finally:
        shutil.rmtree(temp)


def test_package_install_writes_the_hook_for_this_python():
    temp, repo, prefs = fake_houdini()
    try:
        package = install.install_package(root=repo)
        hook = os.path.join(repo, "houdini",
                            "python%d.%dlibs" % sys.version_info[:2], "uiready.py")
        assert os.path.isfile(hook)
        assert "pane_resize_anywhere.install()" in open(hook).read()
        assert json.load(open(package)) == {"package_path": repo}

        # A package file naming another copy is kept unless forced.
        with open(package, "w") as out:
            json.dump({"package_path": "/somewhere/else"}, out)
        install.install_package(root=repo)
        assert json.load(open(package))["package_path"] == "/somewhere/else"
        install.install_package(root=repo, force=True)
        assert json.load(open(package))["package_path"] == repo

        install.uninstall(prefs=prefs)
        assert not os.path.exists(package)
    finally:
        shutil.rmtree(temp)


def test_a_folder_that_is_not_the_repository_is_refused():
    temp, repo, prefs = fake_houdini()
    try:
        for bad in (temp, prefs):
            try:
                install.repository(bad)
            except RuntimeError as error:
                assert "not the repository" in str(error)
            else:
                raise AssertionError("accepted %s" % bad)
    finally:
        shutil.rmtree(temp)


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    quiet = open(os.devnull, "w")
    for test in tests:
        stdout, sys.stdout = sys.stdout, quiet      # the installer narrates
        try:
            test()
        finally:
            sys.stdout = stdout
        print("ok   %s" % test.__name__)
    print("%d passed" % len(tests))


if __name__ == "__main__":
    main()
