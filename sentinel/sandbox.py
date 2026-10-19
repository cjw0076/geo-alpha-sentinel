"""Deterministic restricted Python execution.

All arithmetic shown to users runs here, never inside the language model.
The sandbox accepts a small Python subset (no imports, no attribute access to
private names, no I/O); `np` (NumPy) and `math` are pre-injected, inputs are
passed as plain data, and the program must assign its output to `result`.

This is defence in depth for model-written snippets, not an OS-level jail; the
Docker deployment additionally runs the API as an unprivileged user.
"""

from __future__ import annotations

import ast
import math
import threading
import time
from typing import Any, Dict

import numpy as np

ALLOWED_NODES = (
    ast.Module, ast.Expr, ast.Assign, ast.AugAssign, ast.AnnAssign, ast.Name, ast.Load, ast.Store, ast.Del,
    ast.Constant, ast.BinOp, ast.UnaryOp, ast.BoolOp, ast.Compare, ast.IfExp, ast.If, ast.For, ast.While,
    ast.Break, ast.Continue, ast.Pass, ast.Call, ast.keyword, ast.Attribute, ast.Subscript, ast.Slice,
    ast.Tuple, ast.List, ast.Dict, ast.Set, ast.ListComp, ast.DictComp, ast.SetComp, ast.GeneratorExp,
    ast.comprehension, ast.FunctionDef, ast.arguments, ast.arg, ast.Return, ast.Lambda,
    ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow, ast.USub, ast.UAdd, ast.Not,
    ast.And, ast.Or, ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.In, ast.NotIn, ast.Is, ast.IsNot,
    ast.MatMult, ast.BitAnd, ast.BitOr, ast.Invert, ast.Starred,
)

def _internal_import(name, globals=None, locals=None, fromlist=(), level=0):
    """NumPy's C layer lazily imports its own submodules through the caller's
    builtins. User code can never reach this (Import nodes and dunder names are
    rejected by the AST validator), so only numpy/math internals are allowed."""
    if name.split(".")[0] in ("numpy", "math", "warnings", "functools", "operator"):
        import builtins

        return builtins.__import__(name, globals, locals, fromlist, level)
    raise ImportError(f"import of {name!r} is not allowed in the sandbox")


SAFE_BUILTINS = {
    "__import__": _internal_import,
    "abs": abs, "min": min, "max": max, "sum": sum, "len": len, "range": range, "round": round,
    "sorted": sorted, "enumerate": enumerate, "zip": zip, "float": float, "int": int, "bool": bool,
    "list": list, "dict": dict, "tuple": tuple, "set": set, "str": str, "any": any, "all": all,
    "map": map, "filter": filter, "reversed": reversed, "isinstance": isinstance,
}
BANNED_NAMES = {"eval", "exec", "compile", "open", "__import__", "globals", "locals", "vars", "getattr", "setattr", "delattr", "input", "help", "breakpoint", "type", "object", "memoryview"}
BANNED_NP_ATTRS = {"load", "save", "savez", "savetxt", "loadtxt", "fromfile", "tofile", "memmap", "genfromtxt", "ctypeslib", "lib", "distutils", "f2py", "testing"}


class SandboxError(Exception):
    pass


def validate(code: str) -> ast.Module:
    if len(code) > 20_000:
        raise SandboxError("program too long")
    tree = ast.parse(code, mode="exec")
    for node in ast.walk(tree):
        if not isinstance(node, ALLOWED_NODES):
            raise SandboxError(f"disallowed syntax: {type(node).__name__}")
        if isinstance(node, ast.Name) and (node.id in BANNED_NAMES or node.id.startswith("__")):
            raise SandboxError(f"disallowed name: {node.id}")
        if isinstance(node, ast.Attribute):
            if node.attr.startswith("_") or node.attr in BANNED_NP_ATTRS:
                raise SandboxError(f"disallowed attribute: {node.attr}")
    return tree


def run(code: str, inputs: Dict[str, Any], timeout_s: float = 5.0) -> Dict[str, Any]:
    """Execute `code` with `inputs` bound as variables; return {'result', 'ms'}."""
    tree = validate(code)
    env: Dict[str, Any] = {"__builtins__": SAFE_BUILTINS, "np": np, "math": math}
    env.update(inputs)
    out: Dict[str, Any] = {}
    err: Dict[str, BaseException] = {}

    def target():
        try:
            exec(compile(tree, "<sentinel-sandbox>", "exec"), env, env)  # noqa: S102 - validated AST
            out["result"] = env.get("result")
        except BaseException as exc:  # surfaced to caller
            err["e"] = exc

    t0 = time.perf_counter()
    th = threading.Thread(target=target, daemon=True)
    th.start()
    th.join(timeout_s)
    ms = (time.perf_counter() - t0) * 1000
    if th.is_alive():
        raise SandboxError(f"timeout after {timeout_s}s")
    if "e" in err:
        raise SandboxError(f"{type(err['e']).__name__}: {err['e']}")
    if "result" not in out or out["result"] is None:
        raise SandboxError("program did not assign `result`")
    return {"result": _plain(out["result"]), "ms": round(ms, 1)}


def _plain(x: Any) -> Any:
    if isinstance(x, dict):
        return {str(k): _plain(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_plain(v) for v in x]
    if isinstance(x, np.ndarray):
        return [_plain(v) for v in x.tolist()]
    if isinstance(x, (np.floating,)):
        return float(x)
    if isinstance(x, (np.integer,)):
        return int(x)
    if isinstance(x, float) and (math.isnan(x) or math.isinf(x)):
        return None
    return x
