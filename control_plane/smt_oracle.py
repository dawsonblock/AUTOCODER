from __future__ import annotations

import ast

import z3


class Z3EquivalenceChecker:
    def __init__(self) -> None:
        self.symbols: dict[str, z3.ArithRef] = {}

    def _get_symbol(self, name: str) -> z3.ArithRef:
        if name not in self.symbols:
            self.symbols[name] = z3.Int(name)
        return self.symbols[name]

    def _ast_to_z3(self, node: ast.AST):
        if isinstance(node, ast.Name):
            return self._get_symbol(node.id)
        if isinstance(node, ast.Constant):
            if isinstance(node.value, bool):
                return z3.BoolVal(node.value)
            if isinstance(node.value, int):
                return z3.IntVal(node.value)
            raise ValueError("Unsupported constant")
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
            return -self._ast_to_z3(node.operand)
        if isinstance(node, ast.BinOp):
            left = self._ast_to_z3(node.left)
            right = self._ast_to_z3(node.right)
            if isinstance(node.op, ast.Add):
                return left + right
            if isinstance(node.op, ast.Sub):
                return left - right
            raise ValueError("Unsupported binary operator")
        if isinstance(node, ast.Compare) and len(node.ops) == 1 and len(node.comparators) == 1:
            left = self._ast_to_z3(node.left)
            right = self._ast_to_z3(node.comparators[0])
            op = node.ops[0]
            if isinstance(op, ast.Lt):
                return left < right
            if isinstance(op, ast.LtE):
                return left <= right
            if isinstance(op, ast.Gt):
                return left > right
            if isinstance(op, ast.GtE):
                return left >= right
            if isinstance(op, ast.Eq):
                return left == right
            if isinstance(op, ast.NotEq):
                return left != right
        raise ValueError("Unsupported AST node")

    def _comparison_nodes(self, tree: ast.AST) -> dict[tuple[int, int, int, int], ast.Compare]:
        items: dict[tuple[int, int, int, int], ast.Compare] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Compare):
                key = (
                    getattr(node, "lineno", -1),
                    getattr(node, "col_offset", -1),
                    getattr(node, "end_lineno", -1),
                    getattr(node, "end_col_offset", -1),
                )
                items[key] = node
        return items

    def _parse(self, code: str) -> ast.AST:
        try:
            return ast.parse(code)
        except SyntaxError:
            expr = ast.parse(code, mode="eval")
            return expr

    def _changed_comparison(self, orig_code: str, mut_code: str) -> tuple[ast.Compare, ast.Compare] | None:
        orig_nodes = self._comparison_nodes(self._parse(orig_code))
        mut_nodes = self._comparison_nodes(self._parse(mut_code))
        for location in sorted(set(orig_nodes) & set(mut_nodes)):
            if ast.dump(orig_nodes[location]) != ast.dump(mut_nodes[location]):
                return orig_nodes[location], mut_nodes[location]
        return None

    def is_semantically_equivalent(self, orig_code: str, mut_code: str) -> bool:
        if orig_code.strip() == mut_code.strip():
            return True
        changed = self._changed_comparison(orig_code, mut_code)
        if not changed:
            return False

        self.symbols = {}
        try:
            original, mutated = changed
            z3_original = self._ast_to_z3(original)
            z3_mutated = self._ast_to_z3(mutated)
        except Exception:
            return False

        solver = z3.Solver()
        solver.add(z3_original != z3_mutated)
        return solver.check() == z3.unsat
