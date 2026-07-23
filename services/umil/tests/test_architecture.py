from __future__ import annotations

import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = ROOT / "app"
FORBIDDEN_IMPORT_ROOTS = {
    "trading_assistant", "webapp", "ccxt",
}
FORBIDDEN_CALLS = {
    "create_order", "create_market_buy_order", "create_market_sell_order",
    "cancel_order", "cancel_all_orders", "set_leverage",
}


class ArchitectureBoundaryTest(unittest.TestCase):
    def test_umil_has_no_ats_or_exchange_execution_imports(self):
        violations = []
        for path in APP_ROOT.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                names = []
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    names = [node.module]
                for name in names:
                    if name.split(".")[0] in FORBIDDEN_IMPORT_ROOTS:
                        violations.append(f"{path.name}: import {name}")
        self.assertEqual(violations, [])

    def test_umil_contains_no_order_placement_calls(self):
        violations = []
        for path in APP_ROOT.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                if isinstance(node.func, ast.Attribute):
                    name = node.func.attr
                elif isinstance(node.func, ast.Name):
                    name = node.func.id
                else:
                    continue
                if name in FORBIDDEN_CALLS:
                    violations.append(f"{path.name}: {name}")
        self.assertEqual(violations, [])

    def test_no_runtime_file_references_ats_database_names(self):
        permitted_guards = {"config.py", "evidence.py"}
        violations = []
        for path in APP_ROOT.rglob("*.py"):
            text = path.read_text(encoding="utf-8").lower()
            if path.name not in permitted_guards and (
                "trading_system.db" in text or "analytics.db" in text
            ):
                violations.append(path.name)
        self.assertEqual(violations, [])


if __name__ == "__main__":
    unittest.main()
