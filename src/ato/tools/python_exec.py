"""Conservative validation for Ato's numeric Python execution profile."""

from __future__ import annotations

import ast

from ato.exceptions import ToolError

MAX_PYTHON_CODE_CHARS = 3_000
MAX_PYTHON_AST_NODES = 150
MAX_INTEGER_ABS = 1_000_000_000_000
MAX_POWER_EXPONENT = 100
ALLOWED_CALLS = {"abs", "max", "min", "print", "round"}
ALLOWED_NODES = (
    ast.Module,
    ast.Expr,
    ast.Assign,
    ast.Name,
    ast.Load,
    ast.Store,
    ast.Constant,
    ast.Call,
    ast.keyword,
    ast.BinOp,
    ast.UnaryOp,
    ast.BoolOp,
    ast.Compare,
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.Div,
    ast.FloorDiv,
    ast.Mod,
    ast.Pow,
    ast.USub,
    ast.UAdd,
    ast.Not,
    ast.And,
    ast.Or,
    ast.Eq,
    ast.NotEq,
    ast.Lt,
    ast.LtE,
    ast.Gt,
    ast.GtE,
)


def validate_numeric_python(source: str) -> int:
    """Validate a small numeric-only Python program and return its AST node count."""
    if not source.strip():
        raise ToolError("Python code cannot be empty.")
    if len(source) > MAX_PYTHON_CODE_CHARS:
        raise ToolError(f"Python code exceeds the {MAX_PYTHON_CODE_CHARS}-character limit.")
    try:
        tree = ast.parse(source, mode="exec")
    except SyntaxError as exc:
        raise ToolError(f"Python code has invalid syntax on line {exc.lineno}.") from exc
    nodes = list(ast.walk(tree))
    if len(nodes) > MAX_PYTHON_AST_NODES:
        raise ToolError("Python code exceeds the syntax-complexity limit.")
    for node in nodes:
        if not isinstance(node, ALLOWED_NODES):
            raise ToolError(f"Python syntax {type(node).__name__} is not allowed.")
        if isinstance(node, ast.Assign):
            if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
                raise ToolError("Only single-name assignments are allowed.")
        if isinstance(node, ast.Name) and (
            node.id.startswith("_")
            or (isinstance(node.ctx, ast.Store) and node.id in ALLOWED_CALLS)
        ):
            raise ToolError("Private or reserved names are not allowed.")
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id not in ALLOWED_CALLS:
                raise ToolError("Only approved numeric and print calls are allowed.")
            if any(keyword.arg is None for keyword in node.keywords):
                raise ToolError("Expanded call arguments are not allowed.")
        if isinstance(node, ast.Constant):
            if not isinstance(node.value, (int, float, bool)) or isinstance(node.value, complex):
                raise ToolError("Only real numeric and boolean literals are allowed.")
            if isinstance(node.value, int) and abs(node.value) > MAX_INTEGER_ABS:
                raise ToolError("Integer literal exceeds the numeric limit.")
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Pow):
            if not isinstance(node.right, ast.Constant) or not isinstance(node.right.value, int):
                raise ToolError("Power exponents must be integer literals.")
            if not 0 <= node.right.value <= MAX_POWER_EXPONENT:
                raise ToolError("Power exponent exceeds the numeric limit.")
    return len(nodes)
