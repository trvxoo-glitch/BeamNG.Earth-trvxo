"""The player app's data files (tools/build_app.py server_data): every file a server module reads beside itself goes into
the .exe / AppImage. A hand-kept --add-data list left server/data/billboards.npz out of 1.8.0's exe (no billboards for
any player of it, "billboards.npz is missing" once per worker in their logs); the list is found now, and this test
fails when a module names a file next to it that the build would not bring.
"""
import ast
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER = os.path.normpath(os.path.join(HERE, ".."))
ROOT = os.path.dirname(SERVER)
sys.path.insert(0, os.path.join(ROOT, "tools"))
import build_app  # noqa: E402


def _is_file_dir(node):
    """os.path.dirname(os.path.abspath(__file__)) / os.path.dirname(__file__)."""
    if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "dirname"
            and node.args):
        return False
    a = node.args[0]
    if isinstance(a, ast.Name) and a.id == "__file__":
        return True
    return (isinstance(a, ast.Call) and isinstance(a.func, ast.Attribute) and a.func.attr in ("abspath", "realpath")
            and a.args and isinstance(a.args[0], ast.Name) and a.args[0].id == "__file__")


def _literal(node, release):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value.replace("{release}", release)
    return None


def module_data_paths(release):
    """{(module, path relative to server/)}: the files (or folders) the server's modules name beside themselves:
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "a", "b") and Path(__file__).with_name("a")."""
    out = set()
    for name in sorted(os.listdir(SERVER)):
        if not name.endswith(".py"):
            continue
        with open(os.path.join(SERVER, name), encoding="utf-8") as f:
            tree = ast.parse(f.read(), name)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr == "join" and node.args and _is_file_dir(node.args[0]):
                parts = [_literal(a, release) for a in node.args[1:]]
                if parts and all(p is not None for p in parts):
                    out.add((name, "/".join(parts)))
            elif node.func.attr == "with_name" and len(node.args) == 1:
                v = node.func.value
                if isinstance(v, ast.Call) and isinstance(v.func, ast.Name) and v.func.id == "Path" and v.args \
                        and isinstance(v.args[0], ast.Name) and v.args[0].id == "__file__":
                    p = _literal(node.args[0], release)
                    if p is not None:
                        out.add((name, p))
    return out


class BundleTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.release = build_app._overture_release()
        cls.data = build_app.server_data()
        cls.bundled = {os.path.relpath(src, SERVER).replace(os.sep, "/") for src, _ in cls.data}

    def test_every_file_a_module_names_is_bundled(self):
        missing = []
        for module, rel in sorted(module_data_paths(self.release)):
            if rel.startswith("../"):       # (the mod's tree: its own --add-data)
                continue
            p = os.path.join(SERVER, *rel.split("/"))
            if os.path.isdir(p):
                inside = any(b.startswith(rel + "/") for b in self.bundled)
                files = [f for f in os.listdir(p) if os.path.isfile(os.path.join(p, f))]
                if files and not inside:
                    missing.append(f"{module}: {rel}/")
            elif os.path.isfile(p) and rel not in self.bundled:
                missing.append(f"{module}: {rel}")
        self.assertEqual(missing, [], "tools/build_app.py server_data() leaves these out of the player app")

    def test_known_data_is_found(self):
        for rel in ("data/billboards.npz", "bridges_us.npz", "koppen_1991_2020_0p1.png", "sign_states.geojson",
                    f"overture_stac_{self.release}.json.gz"):
            self.assertIn(rel, self.bundled)

    def test_folders_inside_the_app_follow_the_repo(self):
        dest = {os.path.relpath(src, SERVER).replace(os.sep, "/"): d for src, d in self.data}
        self.assertEqual(dest["bridges_us.npz"], ".")
        self.assertEqual(dest["data/billboards.npz"], "data")
        fonts = [k for k in dest if k.startswith("data/fonts/")]
        self.assertTrue(fonts)
        self.assertTrue(all(dest[k] == "data/fonts" for k in fonts))

    def test_no_code_tests_notes_or_other_releases(self):
        files = ["server/earthd.py", "server/earthfast.pyd", "server/earthfast.abi3.so", "server/README.md",
                 "server/requirements.txt", "server/tests/fixtures/x.npz", "server/__pycache__/a.pyc",
                 "server/overture_stac_2000-01-01.0.json.gz", f"server/overture_stac_{self.release}.json.gz",
                 "server/data/billboards.npz", "server/.hidden", "mod/x.npz"]
        got = [os.path.relpath(s, ROOT).replace(os.sep, "/")
               for s, _ in build_app.server_data(files=files, release=self.release)]
        self.assertEqual(got, sorted(f for f in (f"server/overture_stac_{self.release}.json.gz",
                                                 "server/data/billboards.npz")
                                     if os.path.isfile(os.path.join(ROOT, f))))

    def test_a_source_copy_without_git_walks_the_folder(self):
        walked = {os.path.relpath(os.path.join(ROOT, r), SERVER).replace(os.sep, "/")
                  for r in build_app._walked(ROOT, "server")}
        self.assertTrue(self.bundled <= walked)
        self.assertFalse(any(w.startswith("tests/") for w in walked))


if __name__ == "__main__":
    unittest.main()
