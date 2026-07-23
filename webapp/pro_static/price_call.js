/* Binance-aligned price-call and paper-performance presentation layer. */

let _pcClockOffsetMs = 0;

function pcTimestamp(value) {
    const numeric = Number(value);
    if (!Number.isFinite(numeric)) return null;
    return new Date(numeric < 1e12 ? numeric * 1000 : numeric);
}

function pcTime(value) {
    const date = pcTimestamp(value);
    if (!date || Number.isNaN(date.getTime())) return '—';
    return date.toLocaleTimeString([], {
        hour: '2-digit', minute: '2-digit', second: '2-digit',
        fractionalSecondDigits: 3,
    });
}

function pcNumber(value, digits = 2, suffix = '') {
    if (value === null || value === undefined) return '—';
    const number = Number(value);
    return Number.isFinite(number) ? `${number.toFixed(digits)}${suffix}` : '—';
}

function pcEscape(value) {
    return String(value ?? '').replace(/[&<>'"]/g, character => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;',
    }[character]));
}

function pcNowMs() {
    return Date.now() - _pcClockOffsetMs;
}

function onCallTrade(trade) {
    if (!trade || trade.symbol !== binancePair(currentSymbol)) return;
    if (Number.isFinite(Number(trade.tradeMs))) {
        _pcClockOffsetMs = Date.now() - Number(trade.tradeMs);
    }
    if (Number.isFinite(Number(trade.price))) {
        document.querySelectorAll('.call-live-price').forEach(el => {
            el.textContent = `$${formatPrice(Number(trade.price))}`;
        });
    }
    document.querySelectorAll('.call-live-time').forEach(el => {
        el.textContent = pcTime(trade.tradeMs);
    });
}

function pcRunMarkup(call, running) {
    const up = call.direction === 'UP';
    const status = running ? 'RUNNING' : (call.correct ? 'HIT' : 'MISS');
    const endLabel = running ? 'Expiry' : 'Settled';
    const endValue = running ? call.due_at : call.settled_at;
    const actual = call.actual_price == null
        ? '—' : `$${formatPrice(Number(call.actual_price))}`;
    const evidence = running
        ? '<span><small>Live</small><b class="call-live-price">—</b> <em class="call-live-time"></em></span>'
        : `<span><small>Official close</small>${actual}</span>`;
    const method = running ? '' : (call.settlement_method || 'STREAM_KLINE');
    const economicVerdict = call.economic_action
        ? `<span><small>Economic action</small>${pcEscape(call.economic_action.replaceAll('_', ' '))}</span>`
        : '';
    const timingClass = call.timing_class
        ? `<span><small>Evidence timing</small>${pcEscape(call.timing_class.replaceAll('_', ' '))}</span>`
        : '';

    return `<div class="call-run ${running ? 'running' : (call.correct ? 'hit' : 'miss')}">
        <div class="call-run-top">
            <span class="call-horizon">${call.horizon_label} BINANCE CYCLE</span>
            <span class="call-direction ${up ? 'up' : 'down'}">${up ? '▲' : '▼'} ${call.direction}</span>
            <span class="call-status">${status}</span>
            ${running ? `<span class="call-countdown" data-due="${Number(call.due_at)}"></span>` : ''}
        </div>
        <div class="call-run-details">
            <span><small>Prediction issued</small>${pcTime(call.created_at)}</span>
            <span><small>Entry price</small>$${formatPrice(Number(call.price_to_beat))}</span>
            <span><small>Paper stake</small>$1.00 fixed</span>
            <span><small>${endLabel}</small>${pcTime(endValue)}</span>
            ${evidence}
            ${economicVerdict}
            ${timingClass}
            <span class="call-cycle"><small>Cycle</small>${pcTime(call.cycle_start_ms)} → ${pcTime(call.due_at)}</span>
            ${running ? '' : `<span><small>Evidence</small>${method} · ${Number(call.settlement_latency_ms || 0)} ms</span>`}
        </div>
    </div>`;
}

function pcPerformanceMarkup(stats) {
    return ['5m', '15m'].map(horizon => {
        const s = stats?.by_horizon?.[horizon];
        if (!s) return '';
        const interval = s.wilson_95_low == null
            ? '—'
            : `${pcNumber(s.wilson_95_low * 100, 1, '%')}–${pcNumber(s.wilson_95_high * 100, 1, '%')}`;
        const coverage = s.cycle_coverage == null
            ? null : s.cycle_coverage * 100;
        return `<div class="call-performance">
            <div><strong>${horizon} all-history tracking</strong><span class="sample-state">${s.sample_status.replaceAll('_', ' ')}</span></div>
            <div>Hit rate ${pcNumber(s.hit_rate * 100, 1, '%')} · n=${s.directional_sample}</div>
            <div>95% interval ${interval}</div>
            <div>Brier skill ${pcNumber(s.brier_skill, 3)} · calibration ${pcNumber(s.calibration_error, 3)}</div>
            <div>Coverage ${pcNumber(coverage, 1, '%')} · missed ${s.missed_cycles} · void ${s.voided_cycles}</div>
            <div>Latency p50 ${pcNumber(s.median_settlement_latency_ms, 0, ' ms')} · p95 ${pcNumber(s.p95_settlement_latency_ms, 0, ' ms')} · REST recovery ${s.rest_recoveries}</div>
        </div>`;
    }).join('');
}

function pcMoney(value) {
    const number = Number(value);
    if (!Number.isFinite(number)) return '—';
    return `${number >= 0 ? '+' : '-'}$${Math.abs(number).toFixed(6)}`;
}

function pcCost(value) {
    const number = Number(value);
    return Number.isFinite(number) ? `$${Math.abs(number).toFixed(6)}` : '—';
}

function pcProvenanceMarkup(provenance) {
    const labels = {
        STREAM_KLINE: 'Stream closes',
        REST_RECOVERY: 'REST-recovered closes',
        STATE_RECONCILIATION: 'Restart-reconciled closes',
    };
    return Object.entries(provenance || {}).map(([method, metrics]) =>
        `<div>${pcEscape(labels[method] || method.replaceAll('_', ' '))}: ` +
        `${Number(metrics.sample || 0)} · hit ${metrics.hit_rate == null ? '—' : pcNumber(Number(metrics.hit_rate) * 100, 1, '%')}</div>`
    ).join('') || '<div>Settlement provenance begins with the first primary cycle.</div>';
}

function pcEconomicEvidenceMarkup(report) {
    const horizons = report?.horizons || {};
    const sections = ['5m', '15m'].filter(horizon => horizons[horizon]).map(horizon => {
        const item = horizons[horizon];
        const signal = item.primary_signal || {};
        const benchmark = item.probability_benchmarks || {};
        const actions = item.economic_actions || {};
        const lead = item.lead_time_ms || {};
        const exclusions = Object.entries(item.exclusion_categories || {})
            .map(([code, count]) => `${pcEscape(code.replaceAll('_', ' '))} ${Number(count)}`)
            .join(' · ') || 'none';
        return `<section class="economic-evidence-horizon">
            <div class="economic-evidence-head">
                <strong>${pcEscape(horizon)} PRIMARY EVIDENCE</strong>
                <span class="sample-state">${pcEscape(String(item.sample_status || 'INSUFFICIENT_SAMPLE').replaceAll('_', ' '))}</span>
            </div>
            <div class="economic-evidence-grid">
                <span><small>Classified / primary</small>${Number(item.classified_cycles || 0)} / ${Number(item.primary_eligible_cycles || 0)}</span>
                <span><small>Excluded / pre-policy</small>${Number(item.primary_excluded_cycles || 0)} / ${Number(item.pre_policy_preserved_cycles || 0)}</span>
                <span><small>Forecast lead p50</small>${lead.median == null ? '—' : pcNumber(Number(lead.median) / 1000, 1, ' s')}</span>
                <span><small>Eligible / abstained</small>${Number(actions.eligible || 0)} / ${Number(actions.abstained || 0)}</span>
                <span><small>Participation</small>${actions.participation_rate == null ? '—' : pcNumber(Number(actions.participation_rate) * 100, 1, '%')}</span>
                <span><small>Primary sample</small>${Number(signal.sample || 0)}</span>
            </div>
            <div class="economic-evidence-line">Signal economics: gross ${pcMoney(signal.gross_pnl_usd)} · costs ${pcCost(signal.modeled_cost_usd)} · net ${pcMoney(signal.net_pnl_usd)}</div>
            <div class="economic-evidence-line">Brier: model ${pcNumber(benchmark.model_brier, 3)} · 50/50 ${pcNumber(benchmark.coin_flip_brier, 3)} · prior drift ${pcNumber(benchmark.prior_drift_brier, 3)}</div>
            <div class="economic-evidence-line">Skill: vs 50/50 ${pcNumber(benchmark.skill_vs_coin_flip, 3)} · vs prior drift ${pcNumber(benchmark.skill_vs_prior_drift, 3)}</div>
            <div class="economic-evidence-line">Excluded categories: ${exclusions}</div>
            <div class="economic-evidence-line">${pcProvenanceMarkup(item.settlement_provenance)}</div>
        </section>`;
    }).join('');
    return `<div class="call-model-note">PRIMARY BENCHMARK · BOUNDARY-ISSUED PROSPECTIVE CYCLES ONLY</div>
        <div class="call-sub">Late and pre-policy observations remain preserved but cannot influence headline economics or tournament promotion. Official stream and recovery closes are compared separately.</div>
        ${sections || '<div class="call-sub">The primary v1.3 evidence sample begins with the next classified cycle.</div>'}`;
}

loadPriceCall = async function loadValidatedPriceCall() {
    try {
        const data = await apiScoped(`/api/pro/price-calls/${currentSymbol}?limit=20`);
        if (data === null) return;
        const content = document.getElementById('call-content');
        const economicContent = document.getElementById('economic-evidence-content');
        const openCalls = Array.isArray(data.open_call)
            ? data.open_call : (data.open_call ? [data.open_call] : []);
        const recent = Array.isArray(data.recent) ? data.recent : [];
        const market = data.market || {};
        updateExchangeEvidence(market);

        if (_callPriceLine) {
            candleSeries.removePriceLine(_callPriceLine);
            _callPriceLine = null;
        }
        if (openCalls.length > 0) {
            const active = openCalls[0];
            _callPriceLine = candleSeries.createPriceLine({
                price: Number(active.price_to_beat),
                color: active.direction === 'UP' ? '#00FFC2' : '#FF3366',
                lineWidth: 1,
                lineStyle: LightweightCharts.LineStyle.Dashed,
                axisLabelVisible: true,
                title: `${active.horizon_label} Binance entry`,
            });
        }

        const parts = [
            `<div class="call-model-note">${data.evidence_label}</div>`,
            '<div class="call-sub">UP/DOWN measures direction only; DOWN is simulated directional exposure, not a Binance Spot short order.</div>',
        ];
        if (openCalls.length === 0 && recent.length === 0) {
            parts.push('<div class="call-sub">Waiting for the first Binance aggregate-trade cycle…</div>');
        } else {
            openCalls.sort((a, b) => Number(a.due_at) - Number(b.due_at))
                .forEach(call => parts.push(pcRunMarkup(call, true)));
            recent.forEach(call => parts.push(pcRunMarkup(call, false)));
        }

        const resolved = Number(data.stats?.total_resolved || 0);
        if (resolved > 0) {
            parts.push(`<div class="call-score">All preserved validated calls: ${resolved} resolved ·
                <strong>${pcNumber(data.stats.win_rate * 100, 1, '%')} hit rate</strong> ·
                ${pcNumber(data.stats.avg_move_pct, 3, '%')} average absolute move</div>`);
            parts.push('<div class="call-sub">Operational history includes preserved pre-policy and late calls. Use Economic Evidence Eligibility for the primary benchmark.</div>');
        } else {
            parts.push('<div class="call-sub">Track record begins after the first validated cycle settles.</div>');
        }
        parts.push(pcPerformanceMarkup(data.stats));
        if (Number(data.stats?.legacy_excluded || 0) > 0) {
            parts.push(`<div class="call-sub">Excluded legacy timer calls: ${Number(data.stats.legacy_excluded)}</div>`);
        }
        content.innerHTML = parts.join('');
        if (economicContent) {
            economicContent.innerHTML = pcEconomicEvidenceMarkup(data.economic_evidence);
        }
        onCallTrade({
            symbol: binancePair(currentSymbol),
            price: market.price_text,
            tradeMs: market.trade_ms,
        });
        updateCallCountdown();
    } catch (error) {
        console.warn('Price-call refresh failed', error);
        const economicContent = document.getElementById('economic-evidence-content');
        if (economicContent) economicContent.textContent = 'Economic evidence unavailable.';
    }
};

updateCallCountdown = function updateBinanceCountdown() {
    document.querySelectorAll('.call-countdown[data-due]').forEach(el => {
        const due = pcTimestamp(el.dataset.due);
        if (!due) return;
        const remaining = Math.max(0, due.getTime() - pcNowMs());
        if (remaining === 0) {
            el.textContent = 'settling…';
            return;
        }
        const minutes = Math.floor(remaining / 60000);
        const seconds = Math.floor((remaining % 60000) / 1000);
        const millis = Math.floor(remaining % 1000);
        el.textContent = `${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}.${String(millis).padStart(3, '0')}`;
    });
};

function qtMetric(value, digits = 3, suffix = '') {
    const number = Number(value);
    return Number.isFinite(number) ? `${number.toFixed(digits)}${suffix}` : '—';
}

function qtCandidateName(value) {
    return String(value || 'unknown').replaceAll('_', ' ').replace(/\b\w/g, letter => letter.toUpperCase());
}

function qtProfitFactor(candidate) {
    return candidate.profit_factor_infinite ? '∞' : qtMetric(candidate.profit_factor, 2);
}

function qtHealthMarkup(horizon, data) {
    const health = data.health || {};
    const state = String(health.state || 'INITIALIZING');
    const stateClass = state.toLowerCase();
    return `<section class="qt-health-horizon">
        <div class="qt-health-head">
            <strong>${pcEscape(horizon)} INTEGRITY</strong>
            <span class="qt-health-state ${pcEscape(stateClass)}">${pcEscape(state)}</span>
        </div>
        <div class="qt-health-grid">
            <span><small>Recorded cycles</small>${Number(health.recorded_cycles ?? data.cycles_recorded ?? 0)}</span>
            <span><small>Settled cycles</small>${Number(health.settled_cycles ?? data.cycles_settled ?? 0)}</span>
            <span><small>Active cycles</small>${Number(health.active_cycles || 0)}</span>
            <span><small>Decision records (incl. active)</small>${Number(health.actual_decision_records || 0)} / ${Number(health.expected_decision_records || 0)}</span>
            <span><small>Missing / duplicates</small>${Number(health.missing_decision_records || 0)} / ${Number(health.duplicate_decision_groups || 0)}</span>
            <span><small>Pending decision recovery</small>${Number(health.pending_decision_outbox || 0)}</span>
            <span><small>Settled decisions</small>${Number(health.settled_candidate_decisions || 0)}</span>
            <span><small>Missing outcomes</small>${Number(health.missing_outcome_records || 0)}</span>
            <span><small>State-managed cycles</small>${Number(health.market_state_managed_cycles || 0)}</span>
            <span><small>State links</small>${Number(health.market_state_linked_decisions || 0)} / ${Number(health.expected_market_state_linked_decisions || 0)}</span>
            <span><small>Missing / broken state links</small>${Number(health.missing_market_state_links || 0)} / ${Number(health.broken_market_state_links || 0)}</span>
            <span><small>Pre-state preserved cycles</small>${Number(health.legacy_pre_market_state_cycles || 0)}</span>
            <span><small>Economic-policy cycles</small>${Number(health.economic_policy_managed_cycles || 0)}</span>
            <span><small>Primary economic cycles</small>${Number(health.primary_economic_cycles || 0)}</span>
            <span><small>Broken economic links</small>${Number(health.broken_economic_policy_links || 0)}</span>
            <span><small>Pre-policy preserved cycles</small>${Number(health.legacy_pre_economic_policy_cycles || 0)}</span>
            <span><small>Active candidates</small>${Number(health.active_candidates || 0)}</span>
            <span><small>Sufficient sample</small>${Number(health.candidates_with_sufficient_sample || 0)}</span>
            <span><small>Qualified</small>${Number(health.qualified_candidates || 0)}</span>
            <span><small>Next evaluation</small>${Number(data.next_evaluation_in_cycles || 0)} cycles</span>
            <span><small>Last cycle</small>${health.last_successful_cycle_ms ? pcTime(health.last_successful_cycle_ms) : '—'}</span>
            <span><small>Last evaluation</small>${health.last_evaluation_ms ? pcTime(health.last_evaluation_ms) : 'not run'}</span>
        </div>
    </section>`;
}

function qtStandingsMarkup(horizon, data) {
    const candidates = Array.isArray(data.candidates) ? [...data.candidates] : [];
    const lifecycleOrder = { CHAMPION: 0, QUALIFIED: 1, ELIGIBLE: 2, COLLECTING: 3, RETIRED: 4 };
    candidates.sort((left, right) => {
        const state = (lifecycleOrder[left.lifecycle] ?? 9) - (lifecycleOrder[right.lifecycle] ?? 9);
        if (state !== 0) return state;
        const trades = Number(right.eligible_trades || 0) - Number(left.eligible_trades || 0);
        return trades || String(left.candidate).localeCompare(String(right.candidate));
    });
    const rows = candidates.map(candidate => {
        const lifecycle = String(candidate.lifecycle || 'COLLECTING');
        return `<tr>
            <td><strong>${pcEscape(qtCandidateName(candidate.candidate))}</strong></td>
            <td><span class="qt-lifecycle ${pcEscape(lifecycle.toLowerCase())}">${pcEscape(lifecycle)}</span></td>
            <td>${Number(candidate.cycles || 0)}</td>
            <td>${Number(candidate.eligible_trades || 0)}</td>
            <td>${Number(candidate.no_trade_cycles || 0)}</td>
            <td>${qtMetric(Number(candidate.participation_rate || 0) * 100, 1, '%')}</td>
            <td>${qtMetric(candidate.net_expectancy_usd, 6)}</td>
            <td>${qtProfitFactor(candidate)}</td>
            <td>${qtMetric(candidate.max_drawdown_pct, 2, '%')}</td>
        </tr>`;
    }).join('');
    return `<section class="qt-standings-horizon">
        <div class="qt-horizon-head">
            <strong>${pcEscape(horizon)} · ${pcEscape(String(data.current_regime || 'unclassified').replaceAll('_', ' '))}</strong>
            <span>${candidates.length} active</span>
        </div>
        <div class="qt-table-wrap">
            <table class="qt-standings-table">
                <thead><tr><th>Candidate</th><th>Lifecycle</th><th>Cycles</th><th>Trades</th><th>No trade</th><th>Particip.</th><th>Net exp. USD</th><th>PF</th><th>Max DD</th></tr></thead>
                <tbody>${rows || '<tr><td colspan="9">Collecting the first prospective decision set.</td></tr>'}</tbody>
            </table>
        </div>
        <div class="qt-table-note">Lifecycle is evidence-derived. Collecting candidates are visible but are not officially ranked or promotion-eligible.</div>
    </section>`;
}

function qtHorizonMarkup(horizon, data, policy) {
    const champion = data.champion;
    const sampleMinimum = Number(policy.minimum_eligible_trades || 0);
    const evidence = Number(data.primary_cycles_settled || 0);
    const preserved = Number(data.pre_policy_preserved_cycles || 0);
    const championBlock = champion
        ? `<div class="qt-champion">
            <div><small>Champion</small><strong>${pcEscape(champion.name)}</strong></div>
            <div><small>Promotion confidence</small><strong>${qtMetric(champion.confidence, 1, '%')}</strong></div>
            <div><small>Challenger evaluations survived</small><strong>${Number(champion.challenger_evaluations_survived || 0)}</strong></div>
            <p>${pcEscape(champion.reason)}</p>
        </div>`
        : `<div class="qt-no-champion"><strong>NONE PROMOTED</strong><span>No candidate has cleared every mandatory gate.</span></div>`;
    return `<section class="qt-horizon">
        <div class="qt-horizon-head">
            <strong>${pcEscape(horizon)} TOURNAMENT</strong>
            <span>${pcEscape(String(data.current_regime || 'unclassified').replaceAll('_', ' '))}</span>
        </div>
        <div class="qt-evidence">${evidence} primary settled cycles · ${preserved} pre-policy cycles preserved · ${Number(data.cycles_recorded || 0)} total recorded · ${Number(data.next_evaluation_in_cycles || 0)} to next evaluation</div>
        ${championBlock}
        <div class="qt-waiting">Promotion requires at least ${sampleMinimum} eligible trades per candidate and every mandatory economic, risk, reliability, stability, and statistical gate.</div>
    </section>`;
}

function qtMarketStateMarkup(horizon, state) {
    const dataState = String(state?.data_state || 'WARMING_UP');
    const regime = String(state?.regime || 'unclassified').replaceAll('_', ' ');
    const ready = dataState === 'READY';
    return `<section class="qt-state-horizon">
        <div class="qt-health-head">
            <strong>${pcEscape(horizon)} MARKET STATE</strong>
            <span class="qt-health-state ${ready ? 'healthy' : 'initializing'}">${pcEscape(dataState)}</span>
        </div>
        <div class="qt-health-grid">
            <span><small>Regime</small>${pcEscape(regime.toUpperCase())}</span>
            <span><small>Regime confidence</small>${qtMetric(Number(state?.regime_confidence || 0) * 100, 1, '%')}</span>
            <span><small>Closed candles ready</small>${Number(state?.candle_count || 0)} / ${Number(state?.required_candles || 0)}</span>
            <span><small>Missing intervals</small>${Number(state?.missing_intervals || 0)}</span>
            <span><small>Latest closed candle</small>${state?.latest_close_time_ms ? pcTime(state.latest_close_time_ms) : '—'}</span>
            <span><small>State version</small>${pcEscape(state?.market_state_version || 'market-state-v1.2.0')}</span>
        </div>
    </section>`;
}

function qtReadinessMarkup(horizon, data) {
    const candidates = Array.isArray(data?.candidates) ? [...data.candidates] : [];
    const rows = candidates.map(candidate => `<tr>
        <td><strong>${pcEscape(qtCandidateName(candidate.candidate))}</strong></td>
        <td><span class="qt-readiness ${candidate.data_ready ? 'ready' : 'warming'}">${candidate.data_ready ? 'READY' : 'WARMING'}</span></td>
        <td>${pcEscape(candidate.lifecycle || 'COLLECTING')}</td>
        <td>${qtMetric(Number(candidate.participation_rate || 0) * 100, 1, '%')}</td>
        <td>${pcEscape(String(candidate.main_no_trade_category || 'NONE').replaceAll('_', ' '))}</td>
    </tr>`).join('');
    return `<section class="qt-readiness-horizon">
        <div class="qt-horizon-head"><strong>${pcEscape(horizon)} READINESS</strong><span>${candidates.length} candidates</span></div>
        <div class="qt-table-wrap">
            <table class="qt-standings-table qt-readiness-table">
                <thead><tr><th>Candidate</th><th>Market data</th><th>Lifecycle</th><th>Particip.</th><th>Main no-trade category</th></tr></thead>
                <tbody>${rows || '<tr><td colspan="5">Waiting for the first point-in-time decision set.</td></tr>'}</tbody>
            </table>
        </div>
        <div class="qt-table-note">Only coarse operational categories are exposed. Thresholds, feature vectors, weights, and decision reasons remain server-side.</div>
    </section>`;
}

async function loadQuantTournament() {
    const content = document.getElementById('quant-tournament-content');
    const healthContent = document.getElementById('tournament-health-content');
    const standingsContent = document.getElementById('tournament-standings-content');
    const marketStateContent = document.getElementById('market-state-content');
    const readinessContent = document.getElementById('candidate-readiness-content');
    if (!content) return;
    try {
        const data = await apiScoped(`/api/pro/quant-tournament/${currentSymbol}`);
        if (data === null) return;
        const horizons = data.horizons || {};
        const sections = ['5m', '15m']
            .filter(horizon => horizons[horizon])
            .map(horizon => qtHorizonMarkup(horizon, horizons[horizon], data.policy || {}));
        const activeHorizons = ['5m', '15m'].filter(horizon => horizons[horizon]);
        content.innerHTML = `<div class="qt-disclosure">${pcEscape(data.label || 'SHADOW PAPER TOURNAMENT · NOT LIVE CAPITAL')}</div>
            <div class="qt-policy">Promotion objective: positive post-cost expectancy with a 95% lower confidence bound above zero. Ranking applies only after all hard gates pass.</div>
            ${sections.join('') || '<div class="qt-waiting">Tournament initializes with the next verified Binance cycle.</div>'}`;
        if (healthContent) {
            healthContent.innerHTML = activeHorizons.map(horizon => qtHealthMarkup(horizon, horizons[horizon])).join('')
                || '<div class="qt-waiting">Tournament integrity initializes with the next verified Binance cycle.</div>';
        }
        if (standingsContent) {
            standingsContent.innerHTML = activeHorizons.map(horizon => qtStandingsMarkup(horizon, horizons[horizon])).join('')
                || '<div class="qt-waiting">No prospective candidate decisions recorded yet.</div>';
        }
        if (marketStateContent) {
            const states = data.market_state?.horizons || {};
            marketStateContent.innerHTML = ['5m', '15m']
                .filter(horizon => states[horizon])
                .map(horizon => qtMarketStateMarkup(horizon, states[horizon]))
                .join('') || '<div class="qt-waiting">Market state initializes on the next verified cycle after closed-candle warm-up.</div>';
        }
        if (readinessContent) {
            readinessContent.innerHTML = activeHorizons
                .map(horizon => qtReadinessMarkup(horizon, horizons[horizon]))
                .join('') || '<div class="qt-waiting">Candidate readiness initializes with prospective decisions.</div>';
        }
    } catch (error) {
        content.innerHTML = `<div class="qt-error">Tournament evidence unavailable: ${pcEscape(error.message || 'request failed')}</div>`;
        if (healthContent) healthContent.textContent = 'Tournament integrity unavailable.';
        if (standingsContent) standingsContent.textContent = 'Tournament standings unavailable.';
        if (marketStateContent) marketStateContent.textContent = 'Market-state intelligence unavailable.';
        if (readinessContent) readinessContent.textContent = 'Candidate readiness unavailable.';
    }
}

renderBankrollCard = function renderPaperLedger(data) {
    const el = document.getElementById('bankroll-content');
    if (!el) return;
    let html = `<div class="bankroll-disclosure">${data.label || 'SIMULATED EXECUTION · NOT LIVE CAPITAL'}</div>`;
    if (data.policy_note) {
        html += `<div class="call-sub">${pcEscape(data.policy_note)}</div>`;
    }
    for (const horizon of ['5m', '15m']) {
        const s = data.horizons?.[horizon];
        if (!s) continue;
        const pnlClass = s.net_pnl >= 0 ? 'positive' : 'negative';
        const pf = s.profit_factor == null ? '—' : s.profit_factor.toFixed(2);
        html += `<div class="bankroll-horizon">
            <div class="bankroll-title"><strong>${horizon} PAPER LEDGER</strong><span>${s.settlements} settled</span></div>
            <div>Settled balance: <span class="${pnlClass}">$${s.settled_balance.toFixed(4)}</span></div>
            <div>Net P&amp;L: ${s.net_pnl >= 0 ? '+' : ''}$${s.net_pnl.toFixed(4)} (${s.net_return_pct >= 0 ? '+' : ''}${s.net_return_pct.toFixed(3)}%)</div>
            <div>Open exposure: $${s.open_exposure.toFixed(4)}</div>
            <div>Net win rate: ${(s.net_win_rate * 100).toFixed(1)}% · PF ${pf}</div>
            <div>Expectancy: $${s.net_expectancy.toFixed(6)} / settlement</div>
            <div>Max drawdown: $${s.max_drawdown.toFixed(4)} (${s.max_drawdown_pct.toFixed(3)}%)</div>
            <div>Costs: $${s.total_fees.toFixed(6)} fees · $${s.total_slippage.toFixed(6)} slippage</div>
        </div>`;
    }
    const assumptions = data.assumptions || {};
    html += `<div class="bankroll-assumptions">Assumptions: $${Number(assumptions.fixed_stake_usd || 1).toFixed(2)} fixed stake · one tracked symbol · one position per horizon/cycle · ${Number(assumptions.fee_rate_per_side_pct || 0).toFixed(3)}% fee/side · ${Number(assumptions.slippage_per_side_bps || 0).toFixed(1)} bps slippage/side</div>`;
    el.innerHTML = html;
};
