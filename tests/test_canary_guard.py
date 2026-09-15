from trading_assistant.execution.canary_guard import (
    CanaryGuard,
    CanaryLimits,
)


def guard(tmp_path):
    return CanaryGuard(
        str(tmp_path / "canary.db"),
        CanaryLimits(
            max_order_notional=10,
            max_gross_exposure=20,
            max_open_positions=2,
            max_entries=3,
            max_cumulative_loss=5,
        ),
    )


def test_not_armed_by_default(tmp_path):
    g = guard(tmp_path)

    decision = g.authorize_entry(
        order_notional=5,
        current_gross_exposure=0,
        open_positions=0,
    )

    assert not decision.approved


def test_valid_small_entry_is_approved(tmp_path):
    g = guard(tmp_path)
    g.arm(duration_seconds=3600)

    decision = g.authorize_entry(
        order_notional=10,
        current_gross_exposure=0,
        open_positions=0,
    )

    assert decision.approved


def test_per_order_cap_is_hard(tmp_path):
    g = guard(tmp_path)
    g.arm(duration_seconds=3600)

    decision = g.authorize_entry(
        order_notional=10.01,
        current_gross_exposure=0,
        open_positions=0,
    )

    assert not decision.approved


def test_total_exposure_cap_is_hard(tmp_path):
    g = guard(tmp_path)
    g.arm(duration_seconds=3600)

    decision = g.authorize_entry(
        order_notional=6,
        current_gross_exposure=15,
        open_positions=1,
    )

    assert not decision.approved


def test_open_position_cap_is_hard(tmp_path):
    g = guard(tmp_path)
    g.arm(duration_seconds=3600)

    decision = g.authorize_entry(
        order_notional=5,
        current_gross_exposure=10,
        open_positions=2,
    )

    assert not decision.approved


def test_entry_count_cap_is_hard(tmp_path):
    g = guard(tmp_path)
    g.arm(duration_seconds=3600)

    for n in range(3):
        assert g.authorize_entry(
            order_notional=5,
            current_gross_exposure=0,
            open_positions=0,
        ).approved

        g.record_entry(
            symbol=f"TEST{n}/USDT",
            notional=5,
        )

    assert not g.authorize_entry(
        order_notional=5,
        current_gross_exposure=0,
        open_positions=0,
    ).approved


def test_five_dollar_loss_disables_new_entries(tmp_path):
    g = guard(tmp_path)
    g.arm(duration_seconds=3600)

    g.record_realized_pnl(-2)
    state = g.record_realized_pnl(-3)

    assert state.cumulative_realized_pnl == -5
    assert not state.entry_enabled


def test_uncertain_execution_immediately_halts_entries(tmp_path):
    g = guard(tmp_path)
    g.arm(duration_seconds=3600)

    state = g.record_uncertain_execution(
        request_id="r1",
        symbol="BTC/USDT",
    )

    assert not state.entry_enabled
    assert "uncertain" in state.halt_reason


def test_expiration_disables_new_entries(tmp_path, monkeypatch):
    g = guard(tmp_path)

    monkeypatch.setattr(
        "trading_assistant.execution.canary_guard.time.time",
        lambda: 1000,
    )

    g.arm(duration_seconds=60)

    decision = g.authorize_entry(
        order_notional=5,
        current_gross_exposure=0,
        open_positions=0,
        now=1061,
    )

    assert not decision.approved


def test_halt_does_not_delete_state(tmp_path):
    g = guard(tmp_path)
    g.arm(duration_seconds=3600)

    g.record_entry(
        symbol="BTC/USDT",
        notional=5,
    )

    state = g.halt("operator halt")

    assert not state.entry_enabled
    assert state.entries == 1
    assert state.halt_reason == "operator halt"
