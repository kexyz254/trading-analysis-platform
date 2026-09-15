import ast
from pathlib import Path


def method_source(path, class_name, method_name):
    source = Path(path).read_text()
    tree = ast.parse(source)

    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            for item in node.body:
                if (
                    isinstance(
                        item,
                        (ast.FunctionDef, ast.AsyncFunctionDef),
                    )
                    and item.name == method_name
                ):
                    return ast.get_source_segment(source, item)

    raise AssertionError(
        f"{class_name}.{method_name} not found"
    )


def test_buy_path_is_canary_gated_before_execution():
    source = method_source(
        "trading_assistant/agents/orchestrator.py",
        "Orchestrator",
        "_process_symbol",
    )

    authorize = source.index(
        "self.canary_guard.authorize_entry"
    )
    execute = source.index(
        "self.execution.execute",
        authorize,
    )

    assert authorize < execute


def test_successful_entry_is_counted_and_owned():
    source = method_source(
        "trading_assistant/agents/orchestrator.py",
        "Orchestrator",
        "_process_symbol",
    )

    assert "self.canary_guard.record_entry" in source
    assert "TradeProvenance.ATS_AUTONOMOUS" in source
    assert "self.ownership_service.record_fill" in source


def test_execution_error_halts_new_canary_entries():
    source = method_source(
        "trading_assistant/agents/orchestrator.py",
        "Orchestrator",
        "_process_symbol",
    )

    assert (
        "self.canary_guard.record_uncertain_execution"
        in source
    )


def test_protective_stop_failure_halts_new_entries():
    source = method_source(
        "trading_assistant/agents/orchestrator.py",
        "Orchestrator",
        "_process_symbol",
    )

    assert "result.stop_order_placed is False" in source
    assert "self.canary_guard.halt" in source


def test_sell_path_is_not_blocked_by_authorize_entry():
    source = method_source(
        "trading_assistant/agents/orchestrator.py",
        "Orchestrator",
        "_process_symbol",
    )

    sell = source.index(
        "elif prediction.signal == Signal.SELL"
    )

    sell_source = source[sell:]

    assert "authorize_entry" not in sell_source


def test_run_system_injects_guard():
    source = Path("run_system.py").read_text()

    assert '"/app/data/autonomous_canary.db"' in source
    assert "canary_guard=canary_guard" in source
    assert "max_order_notional=10.0" in source
    assert "max_gross_exposure=20.0" in source
    assert "max_open_positions=2" in source
    assert "max_entries=3" in source
    assert "max_cumulative_loss=5.0" in source
