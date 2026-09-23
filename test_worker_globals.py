"""
Every function that ASSIGNS a module-level setting must declare it global.

WHY THIS EXISTS
---------------
hf_build_worker.py keeps its per-build state in UPPER_CASE module globals --
FILE_NAME, SOURCE_TYPE, SOURCE_URL and so on -- and several download routes
update FILE_NAME when a source reports the real filename.

In Python, one assignment anywhere in a function makes that name LOCAL for the
whole function. So a function that reads FILE_NAME near its top and assigns it
further down raises UnboundLocalError at the read, before doing any work.

That is exactly what happened. Commit 6402327 added

    FILE_NAME = data["fileName"]

to gas_try_folder_download() without a `global` line. Every build from then on
logged

    GAS folder error: cannot access local variable 'FILE_NAME' where it is
    not associated with a value

and fell through to the next route. It was found in a production log, not by
a test -- test_gas_folder.py lifts pieces of the function and never executes
the line that reads the name first.

The other failure mode is quieter: a function that assigns without reading gets
no error at all, and the assignment silently vanishes when it returns. Both are
caught here, because both come from the same missing line.

This reads the source with `ast`, so it needs no network, no credentials and
no environment -- it cannot be skipped on a runner.
"""

import ast
import io
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TARGETS = ["hf_build_worker.py"]

PASS = 0
FAIL = 0


def ok(cond, label, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  ok   " + label)
    else:
        FAIL += 1
        print("  FAIL " + label + ("\n       " + str(detail) if detail else ""))


def module_settings(tree):
    """UPPER_CASE names assigned at module level: the per-build state."""
    names = set()
    for node in tree.body:
        targets = []
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
            targets = [node.target]
        for t in targets:
            for n in ast.walk(t):
                if isinstance(n, ast.Name) and n.id.isupper():
                    names.add(n.id)
    return names


def assigned_in(fn):
    """Names this function binds, excluding nested functions' own scopes."""
    out = set()
    stack = list(fn.body)
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.Lambda, ast.ClassDef)):
            continue
        if isinstance(node, ast.Assign):
            tgts = node.targets
        elif isinstance(node, (ast.AugAssign, ast.AnnAssign)):
            tgts = [node.target]
        elif isinstance(node, (ast.For, ast.AsyncFor)):
            tgts = [node.target]
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            tgts = [i.optional_vars for i in node.items if i.optional_vars]
        else:
            tgts = []
        for t in tgts:
            for n in ast.walk(t):
                if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store):
                    out.add(n.id)
        stack.extend(ast.iter_child_nodes(node))
    return out


def declared_global(fn):
    out = set()
    stack = list(fn.body)
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.Lambda, ast.ClassDef)):
            continue
        if isinstance(node, ast.Global):
            out.update(node.names)
        stack.extend(ast.iter_child_nodes(node))
    return out


for target in TARGETS:
    path = os.path.join(HERE, target)
    tree = ast.parse(io.open(path, encoding="utf-8").read(), filename=target)
    settings = module_settings(tree)

    print("\n== %s ==" % target)
    ok("FILE_NAME" in settings,
       "FILE_NAME is recognised as module state (the check is looking at the "
       "right thing)", sorted(settings)[:12])

    checked = 0
    for fn in [n for n in ast.walk(tree)
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
        shadowed = (assigned_in(fn) & settings) - declared_global(fn)
        touched = assigned_in(fn) & settings
        if not touched:
            continue
        checked += 1
        ok(not shadowed,
           "%s() declares global for %s" % (fn.name, ", ".join(sorted(touched))),
           "assigns %s without `global` -- that name is LOCAL for the whole "
           "function (line %d)" % (", ".join(sorted(shadowed)), fn.lineno))

    ok(checked >= 4,
       "found the functions that update module state (%d)" % checked,
       "expected at least download_file, mediafire_download, "
       "signed_url_download, extract_from_google_zip")

    # The specific regression, named so its failure message says what broke.
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef)
              and n.name == "gas_try_folder_download")
    ok("FILE_NAME" in declared_global(fn),
       "gas_try_folder_download() declares global FILE_NAME "
       "(regression from 6402327)")

print("\n%d passed, %d failed\n" % (PASS, FAIL))
sys.exit(0 if FAIL == 0 else 1)
