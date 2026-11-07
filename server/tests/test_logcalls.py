"""Every log call in the server's code: its %-placeholders match its arguments (a mismatch is not an error where it
is written: the logging module prints '--- Logging error ---' and a traceback to stderr when the line is emitted,
which is server.log, and the line itself is lost), and no module prints to stdout outside the CLI tools (server.log
is the server's stdout: a print has no time, level or area, and the digest cannot read it)."""
import ast
import glob
import os
import re
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER = os.path.dirname(HERE)
LEVELS = {"debug", "info", "warning", "error", "critical", "exception", "log"}
SPEC = re.compile(r"%(?:\([^)]*\))?[#0\- +]*(?:\*|\d+)?(?:\.(?:\*|\d+))?[hlL]?([diouxXeEfFgGcrsa%])")


def placeholders(fmt):
    n = 0
    for m in SPEC.finditer(fmt):
        if m.group(1) == "%":
            continue
        n += 1 + m.group(0).count("*")
    return n


def parse(path):
    with open(path, encoding="utf-8") as f:
        return ast.parse(f.read(), path)


def calls(path):
    tree = parse(path)
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in LEVELS):
            continue
        base = node.func.value
        name = base.id if isinstance(base, ast.Name) else (base.attr if isinstance(base, ast.Attribute) else "")
        is_logger = name in ("log", "logger", "lg", "glog", "hlog", "_log") or (
            isinstance(base, ast.Call) and getattr(base.func, "attr", "") == "getLogger")
        if not is_logger:
            continue
        args = node.args[1:] if node.func.attr == "log" else node.args
        if not args or not isinstance(args[0], ast.Constant) or not isinstance(args[0].value, str):
            continue
        if any(isinstance(a, ast.Starred) for a in args[1:]):
            continue
        yield node.lineno, args[0].value, len(args) - 1


class LogCallsTest(unittest.TestCase):
    def test_placeholders_match_arguments(self):
        bad = []
        for p in sorted(glob.glob(os.path.join(SERVER, "*.py"))):
            for line, fmt, n in calls(p):
                if n == 0 and "%" in fmt and placeholders(fmt) > 0:
                    bad.append(f"{os.path.basename(p)}:{line}: {placeholders(fmt)} placeholders, no arguments: {fmt[:60]!r}")
                elif n and placeholders(fmt) != n:
                    bad.append(f"{os.path.basename(p)}:{line}: {placeholders(fmt)} placeholders, {n} arguments: {fmt[:60]!r}")
        self.assertEqual(bad, [], "\n".join(bad))

    def test_placeholder_count(self):
        self.assertEqual(placeholders("a %s b %d %%"), 2)
        self.assertEqual(placeholders("%.1f s %(x)s %*d"), 4)

    def test_no_prints_in_server_modules(self):
        # (the CLI entry points and tools print by design: their __main__ blocks, and the modules named here)
        allowed = {"earthd.py", "logtools.py", "sampler.py", "stackdump.py"}
        bad = []
        for p in sorted(glob.glob(os.path.join(SERVER, "*.py"))):
            if os.path.basename(p) in allowed:
                continue
            tree = parse(p)
            mains = [n for n in tree.body if isinstance(n, ast.If) and "__main__" in ast.dump(n.test)]
            funcs = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
            skip = set()
            for m in mains + [funcs[k] for k in ("main", "cli", "_main") if k in funcs]:
                for n in ast.walk(m):
                    skip.add(id(n))
            for n in ast.walk(tree):
                if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "print" and id(n) not in skip:
                    kw = {k.arg: k.value for k in n.keywords}
                    if "file" in kw:        # (stderr on purpose, or a file)
                        continue
                    bad.append(f"{os.path.basename(p)}:{n.lineno}")
        self.assertLessEqual(len(bad), BASELINE_PRINTS, "new print() in server modules (log instead):\n" + "\n".join(bad))


BASELINE_PRINTS = 0


if __name__ == "__main__":
    unittest.main()
