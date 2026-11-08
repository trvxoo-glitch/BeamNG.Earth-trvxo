"""Every file rename in the server tolerates Windows' "Access is denied" (staging.replace_retry / replace_cached).

On Windows os.replace fails with PermissionError (WinError 5 / 32) whenever another thread or process has the target
open for a moment: the game reading it, a second server, an antivirus or search indexer scanning the new file. Writers
were fixed one at a time as players hit them (GitHub #67 the DEM cache, #240 / #241 the map data, #663 the OSM tags,
a player's netstats file 2026-11-06). This test makes it a rule: a bare os.replace / os.rename in server/*.py fails the
build unless it renames a folder (no retry there: a folder's rename fails for other reasons) and is listed below.
"""
import ast
import os
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER = os.path.join(HERE, "..")
sys.path.insert(0, SERVER)
import staging  # noqa: E402

# (module, function): folder renames, kept bare
FOLDER_RENAMES = {
    ("airports.py", "refresh"),             # a new generation's folder into place
    ("earthd.py", "_rmtree_outside"),       # a session folder moved into the cache's trash
    ("gravity.py", "_save_derived"),        # the derived model's folder
    ("mapdata.py", "_repack"),              # a cell's packed folder
    ("mapdata.py", "_mirrored"),            # a mirrored cell's folder
    ("worldbundle.py", "build_bundle"),     # a bundle's staging folder
}
ALLOWED_MODULES = {"staging.py"}            # the helpers themselves


def bare_renames(path):
    """[(function name, line)] of os.replace / os.rename calls in a module."""
    with open(path, encoding="utf-8") as f:
        tree = ast.parse(f.read(), path)
    out = []

    def visit(node, fn):
        for child in ast.iter_child_nodes(node):
            name = child.name if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) else fn
            if isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute) \
                    and child.func.attr in ("replace", "rename") and isinstance(child.func.value, ast.Name) \
                    and child.func.value.id == "os":
                out.append((fn, child.lineno))
            visit(child, name)
    visit(tree, "<module>")
    return out


class RuleTest(unittest.TestCase):
    def test_no_bare_file_renames(self):
        found, folder = [], set()
        for name in sorted(os.listdir(SERVER)):
            if not name.endswith(".py") or name in ALLOWED_MODULES:
                continue
            for fn, line in bare_renames(os.path.join(SERVER, name)):
                if (name, fn) in FOLDER_RENAMES:
                    folder.add((name, fn))
                else:
                    found.append(f"{name}:{line} in {fn}()")
        self.assertEqual(found, [], "use staging.replace_retry (the new content must go in) or staging.replace_cached "
                                    "(a cache file anyone could have written): a bare os.replace fails on Windows "
                                    "while another process has the file open")
        self.assertEqual(folder, FOLDER_RENAMES, "a listed folder rename is gone: take it off FOLDER_RENAMES")


class ReplaceRetryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.src = os.path.join(self.tmp.name, "a.tmp")
        self.dst = os.path.join(self.tmp.name, "a.json")
        with open(self.src, "w") as f:
            f.write("new")
        with open(self.dst, "w") as f:
            f.write("old")

    def read(self):
        with open(self.dst) as f:
            return f.read()

    def test_held_for_a_moment_then_replaced(self):
        real, calls = os.replace, []

        def flaky(a, b):
            calls.append(a)
            if len(calls) < 3:
                raise PermissionError(13, "Access is denied")
            real(a, b)
        with mock.patch.object(staging.os, "replace", flaky), mock.patch.object(staging.time, "sleep") as sleep:
            staging.replace_retry(self.src, self.dst)
        self.assertEqual(self.read(), "new")
        self.assertEqual(len(calls), 3)
        self.assertEqual([c.args[0] for c in sleep.call_args_list], list(staging.REPLACE_WAITS[:2]))
        self.assertFalse(os.path.exists(self.src))

    def test_held_for_good_raises_and_keeps_both(self):
        def held(a, b):
            raise PermissionError(13, "Access is denied")
        with mock.patch.object(staging.os, "replace", held), mock.patch.object(staging.time, "sleep") as sleep:
            with self.assertRaises(PermissionError):
                staging.replace_retry(self.src, self.dst)
        self.assertEqual(sleep.call_count, len(staging.REPLACE_WAITS))
        self.assertEqual(self.read(), "old")
        self.assertTrue(os.path.exists(self.src))       # (the caller's own handling removes it, as before)

    def test_other_errors_are_not_retried(self):
        def broken(a, b):
            raise IsADirectoryError(21, "Is a directory")
        with mock.patch.object(staging.os, "replace", broken), mock.patch.object(staging.time, "sleep") as sleep:
            with self.assertRaises(IsADirectoryError):
                staging.replace_retry(self.src, self.dst)
        sleep.assert_not_called()

    def test_a_vanished_folder_is_made_again(self):
        dst = os.path.join(self.tmp.name, "gone", "b.json")
        staging.replace_retry(self.src, dst)
        with open(dst) as f:
            self.assertEqual(f.read(), "new")


if __name__ == "__main__":
    unittest.main()
