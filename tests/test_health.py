from trading_assistant.health import compute_health_score


def test_no_heartbeat_returns_unknown_not_a_fabricated_score():
    report = compute_health_score(has_heartbeat=False)

    assert report.score is None
    assert report.label == "unknown"
    assert "no heartbeat" in report.reasons[0]


def test_healthy_when_nothing_is_wrong():
    report = compute_health_score(has_heartbeat=True)

    assert report.score == 100
    assert report.label == "healthy"
    assert report.reasons == []


def test_circuit_breaker_tripped_alone_reaches_critical():
    report = compute_health_score(has_heartbeat=True, circuit_breaker_tripped=True)

    assert report.score == 40
    assert report.label == "critical"
    assert "circuit breaker tripped" in report.reasons


def test_staleness_alone_reaches_degraded_not_critical():
    report = compute_health_score(
        has_heartbeat=True, staleness_seconds=400, poll_interval_seconds=60
    )

    assert report.score == 70
    assert report.label == "degraded"
    assert any("last cycle" in r for r in report.reasons)


def test_staleness_within_tolerance_is_not_penalized():
    report = compute_health_score(
        has_heartbeat=True, staleness_seconds=90, poll_interval_seconds=60
    )

    assert report.score == 100
    assert report.label == "healthy"


def test_recent_error_rate_scales_the_penalty():
    report_low = compute_health_score(has_heartbeat=True, recent_error_rate=0.25)
    report_high = compute_health_score(has_heartbeat=True, recent_error_rate=1.0)

    assert report_low.score == 90
    assert report_high.score == 60
    assert report_low.score > report_high.score


def test_penalties_stack_and_clamp_at_zero():
    report = compute_health_score(
        has_heartbeat=True,
        circuit_breaker_tripped=True,
        staleness_seconds=1000,
        poll_interval_seconds=60,
        recent_error_rate=1.0,
    )

    assert report.score == 0  # 100 - 60 - 30 - 40 clamps at 0, never negative
    assert report.label == "critical"
    assert len(report.reasons) == 3


def test_score_never_exceeds_100():
    report = compute_health_score(has_heartbeat=True, recent_error_rate=0.0)

    assert report.score == 100


def test_label_thresholds():
    # 100 - 30 (staleness only) = 70 -> degraded (>= 50)
    degraded = compute_health_score(has_heartbeat=True, staleness_seconds=1000, poll_interval_seconds=60)
    assert degraded.label == "degraded"

    # 100 - 60 (breaker) = 40 -> critical (< 50)
    critical = compute_health_score(has_heartbeat=True, circuit_breaker_tripped=True)
    assert critical.label == "critical"


def test_to_dict_shape():
    report = compute_health_score(has_heartbeat=True, circuit_breaker_tripped=True)

    d = report.to_dict()
    assert set(d.keys()) == {"score", "label", "reasons"}
    assert d["score"] == 40
    assert d["label"] == "critical"
