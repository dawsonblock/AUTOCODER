from __future__ import annotations

import ast
from typing import List, Set

try:
    import tree_sitter  # noqa: F401
    import tree_sitter_python as tspython  # noqa: F401

    TREE_SITTER_AVAILABLE = True
except ModuleNotFoundError:  # pragma: no cover
    TREE_SITTER_AVAILABLE = False


OPERATOR_SWAPS = {
    ast.Lt: "<=",
    ast.LtE: "<",
    ast.Gt: ">=",
    ast.GtE: ">",
    ast.Eq: "!=",
    ast.NotEq: "==",
}


class TargetedTreeSitterPatcher:
    def __init__(self) -> None:
        self.backend = "tree-sitter" if TREE_SITTER_AVAILABLE else "python-ast"

    def comparison_lines(self, code: str) -> list[int]:
        tree = ast.parse(code)
        lines = {
            int(getattr(node, "lineno", -1))
            for node in ast.walk(tree)
            if isinstance(node, ast.Compare) and getattr(node, "lineno", None) is not None
        }
        return sorted(line for line in lines if line > 0)

    def _replace_operator(self, code: str, node: ast.Compare, new_op_text: str) -> str | None:
        if node.end_lineno != node.lineno:
            return None
        segment = ast.get_source_segment(code, node)
        if not segment:
            return None

        original_op = next((text for kind, text in _operator_tokens().items() if isinstance(node.ops[0], kind)), None)
        if original_op is None:
            return None
        relative_idx = segment.find(original_op)
        if relative_idx == -1:
            return None

        lines = code.splitlines(keepends=True)
        line_idx = node.lineno - 1
        start_col = node.col_offset + relative_idx
        end_col = start_col + len(original_op)
        line = lines[line_idx]
        lines[line_idx] = f"{line[:start_col]}{new_op_text}{line[end_col:]}"
        return "".join(lines)

    def generate_targeted_patches(
        self,
        code: str,
        suspicious_lines: List[int],
        max_candidates: int = 4,
    ) -> List[str]:
        tree = ast.parse(code)
        candidates: Set[str] = set()
        line_filter = set(suspicious_lines)

        for node in ast.walk(tree):
            if not isinstance(node, ast.Compare) or len(node.ops) != 1:
                continue
            if getattr(node, "lineno", None) not in line_filter:
                continue
            replacement = OPERATOR_SWAPS.get(type(node.ops[0]))
            if not replacement:
                continue
            candidate = self._replace_operator(code, node, replacement)
            if candidate and candidate != code:
                candidates.add(candidate)
            if len(candidates) >= max_candidates:
                break

        return list(candidates)


def _operator_tokens() -> dict[type[ast.AST], str]:
    return {
        ast.Lt: "<",
        ast.LtE: "<=",
        ast.Gt: ">",
        ast.GtE: ">=",
        ast.Eq: "==",
        ast.NotEq: "!=",
    }
