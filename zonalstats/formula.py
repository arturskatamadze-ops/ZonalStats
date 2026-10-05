"""
Safe formula evaluation for vegetation indices.

Index formulas are written in terms of *band role* tokens (Blue, Green, Red,
RedEdge, NIR, LWIR) plus numeric constants, the arithmetic operators
+ - * / **, unary +/-, parentheses, and a small whitelist of functions
(sqrt, abs, exp, log, log10, min, max, clip).

We never use Python's built-in eval(). Instead the expression is parsed to an
AST and walked with an explicit allow-list of node types, so a user typing a
new index formula in the GUI cannot execute arbitrary code.
"""

from __future__ import annotations

import ast
import numpy as np

# Functions a formula is allowed to call. Names are case-sensitive.
_ALLOWED_FUNCS = {
    "sqrt": np.sqrt,
    "abs": np.abs,
    "exp": np.exp,
    "log": np.log,
    "log10": np.log10,
    "min": np.minimum,
    "max": np.maximum,
    "minimum": np.minimum,
    "maximum": np.maximum,
    "clip": np.clip,
}

# Canonical band-role tokens understood by formulas.
KNOWN_ROLES = ("Blue", "Green", "Red", "RedEdge", "NIR", "LWIR")

_ALLOWED_BINOPS = (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow, ast.Mod)
_ALLOWED_UNARYOPS = (ast.UAdd, ast.USub)


class FormulaError(ValueError):
    """Raised when a formula is malformed or references something not allowed."""


def _check_node(node: ast.AST) -> None:
    """Recursively verify that *node* only uses allow-listed constructs."""
    if isinstance(node, ast.Expression):
        _check_node(node.body)
    elif isinstance(node, ast.BinOp):
        if not isinstance(node.op, _ALLOWED_BINOPS):
            raise FormulaError(f"Operator {type(node.op).__name__} is not allowed.")
        _check_node(node.left)
        _check_node(node.right)
    elif isinstance(node, ast.UnaryOp):
        if not isinstance(node.op, _ALLOWED_UNARYOPS):
            raise FormulaError(f"Unary operator {type(node.op).__name__} is not allowed.")
        _check_node(node.operand)
    elif isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name) or node.func.id not in _ALLOWED_FUNCS:
            name = getattr(node.func, "id", "?")
            raise FormulaError(f"Function '{name}' is not allowed.")
        if node.keywords:
            raise FormulaError("Keyword arguments are not allowed in formulas.")
        for arg in node.args:
            _check_node(arg)
    elif isinstance(node, ast.Name):
        # Names are band roles (resolved at evaluation time) or function names.
        return
    elif isinstance(node, ast.Constant):
        if not isinstance(node.value, (int, float)) or isinstance(node.value, bool):
            raise FormulaError("Only numeric constants are allowed.")
    else:
        raise FormulaError(f"Syntax element {type(node).__name__} is not allowed.")


def parse_formula(expr: str) -> ast.Expression:
    """Parse and validate *expr*. Returns the AST or raises FormulaError."""
    if not expr or not expr.strip():
        raise FormulaError("Formula is empty.")
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as exc:  # pragma: no cover - message passthrough
        raise FormulaError(f"Syntax error: {exc.msg}") from exc
    _check_node(tree)
    return tree


def formula_dependencies(expr: str) -> set[str]:
    """Return the set of band-role tokens referenced by *expr*.

    Function names (sqrt, abs, ...) are excluded. Used to decide whether an
    index is computable for a given sensor.
    """
    tree = parse_formula(expr)
    deps: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id not in _ALLOWED_FUNCS:
            deps.add(node.id)
    return deps


def validate_formula(expr: str, allowed_roles: set[str] | None = None) -> set[str]:
    """Validate syntax and (optionally) that every referenced role is allowed.

    Returns the set of dependencies. Raises FormulaError on problems.
    """
    deps = formula_dependencies(expr)
    roles = allowed_roles if allowed_roles is not None else set(KNOWN_ROLES)
    unknown = deps - roles
    if unknown:
        raise FormulaError(
            "Unknown band(s): " + ", ".join(sorted(unknown)) +
            ". Allowed: " + ", ".join(sorted(roles))
        )
    return deps


def _eval_node(node: ast.AST, env: dict[str, np.ndarray]):
    if isinstance(node, ast.Expression):
        return _eval_node(node.body, env)
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        if node.id in env:
            return env[node.id]
        raise FormulaError(f"Band '{node.id}' is not available for this sensor.")
    if isinstance(node, ast.UnaryOp):
        val = _eval_node(node.operand, env)
        return +val if isinstance(node.op, ast.UAdd) else -val
    if isinstance(node, ast.BinOp):
        left = _eval_node(node.left, env)
        right = _eval_node(node.right, env)
        if isinstance(node.op, ast.Add):
            return left + right
        if isinstance(node.op, ast.Sub):
            return left - right
        if isinstance(node.op, ast.Mult):
            return left * right
        if isinstance(node.op, ast.Div):
            return left / right
        if isinstance(node.op, ast.Pow):
            return left ** right
        if isinstance(node.op, ast.Mod):
            return left % right
    if isinstance(node, ast.Call):
        func = _ALLOWED_FUNCS[node.func.id]
        args = [_eval_node(a, env) for a in node.args]
        return func(*args)
    raise FormulaError(f"Cannot evaluate {type(node).__name__}.")


def evaluate_formula(expr: str, band_arrays: dict[str, np.ndarray]) -> np.ndarray:
    """Evaluate *expr* against *band_arrays* (role -> ndarray of equal shape).

    Division by zero and invalid operations yield nan/inf rather than raising;
    callers should drop non-finite values before computing statistics.
    """
    tree = parse_formula(expr)
    with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
        result = _eval_node(tree, band_arrays)
    return np.asarray(result, dtype=np.float64)
