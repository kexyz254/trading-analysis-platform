from pathlib import Path


def orchestrator_source():
    return Path(
        "trading_assistant/agents/orchestrator.py"
    ).read_text()


def test_manual_assist_has_deferred_heavy_warm_start():
    source = orchestrator_source()

    assert '== "manual_assist"' in source
    assert "deferred_heavy_warm_start" in source
    assert "heavy_warm_start_task" in source


def test_position_monitor_remains_independent_background_task():
    source = orchestrator_source()

    assert (
        "asyncio.create_task("
        "self._position_monitor_loop()"
        in source.replace("\n", "").replace(" ", "")
        or "_position_monitor_loop()" in source
    )


def test_main_cycle_still_uses_configured_poll_interval():
    source = orchestrator_source()

    assert "timeout=self.poll_interval_seconds" in source


def test_cycle_latency_is_measured():
    source = orchestrator_source()

    assert '"cycle performance"' in source
    assert "cycle_seconds" in source
