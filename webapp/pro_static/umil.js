/** Complete read-only UMIL intelligence workspace. Polls only while visible. */
(() => {
    'use strict';

    const REFRESH_MS = 5000;
    const ANOMALY_REFRESH_MS = 15000;
    const MARKET_REFRESH_MS = 60000;
    const SLOW_REFRESH_MS = 300000;
    const REQUEST_TIMEOUT_MS = 12000;
    const DNA_ORDER = ['TRN', 'MOM', 'PRS', 'STR', 'VOL', 'LIQ', 'ACT', 'RSK'];
    const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
    let active = false;
    let timer = null;
    let markets = [];
    let universe = [];
    let scaleRows = [];
    let selectedSymbol = 'BTCUSDT';
    let requestGeneration = 0;
    let lastAnomalyRefresh = 0;
    let lastMarketRefresh = 0;
    let lastAuthorityRefresh = 0;
    let lastUniverseRefresh = 0;
    let scaleLoaded = false;
    const inFlight = new Set();
    const payloads = new Map();

    const byId = id => document.getElementById(id);
    const escapeHtml = value => String(value ?? '')
        .replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;').replaceAll("'", '&#39;');
    const finite = value => Number.isFinite(Number(value));
    const score = value => finite(value) ? Math.round(Number(value)) : '—';
    const pct = value => finite(value) ? `${Number(value) >= 0 ? '+' : ''}${Number(value).toFixed(2)}%` : '—';
    const ratioPct = value => finite(value) ? `${(Number(value) * 100).toFixed(1)}%` : '—';
    const compact = value => finite(value)
        ? Intl.NumberFormat(undefined, { notation: 'compact', maximumFractionDigits: 2 }).format(Number(value))
        : '—';
    const fixed = (value, digits = 2) => finite(value) ? Number(value).toFixed(digits) : '—';
    const label = value => String(value ?? '—').replaceAll('_', ' ');
    const displayDate = value => {
        const date = new Date(value);
        return Number.isNaN(date.getTime()) ? '—' : date.toLocaleString();
    };
    const age = seconds => {
        if (!finite(seconds)) return '—';
        const value = Math.max(0, Number(seconds));
        if (value < 60) return `${value.toFixed(1)}s`;
        if (value < 3600) return `${Math.round(value / 60)}m`;
        return `${(value / 3600).toFixed(1)}h`;
    };
    const jsonText = value => JSON.stringify(value ?? null, null, 2);
    const jsonBlock = value => `<pre class="umil-json">${escapeHtml(jsonText(value))}</pre>`;

    async function get(path) {
        const controller = new AbortController();
        const timeout = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
        try {
            const response = await fetch(path, {
                cache: 'no-store',
                headers: { Accept: 'application/json' },
                signal: controller.signal,
            });
            const body = await response.json().catch(() => null);
            if (!response.ok) {
                const detail = body?.detail ? (typeof body.detail === 'string' ? body.detail : jsonText(body.detail)) : `HTTP ${response.status}`;
                throw new Error(detail);
            }
            if (!body || body.source !== 'UMIL' || body.data === undefined || body.data === null) {
                throw new Error('Invalid UMIL gateway envelope');
            }
            return body;
        } finally {
            clearTimeout(timeout);
        }
    }

    function remember(name, envelope) {
        payloads.set(name, {
            gateway_status: envelope.gateway_status,
            served_at: envelope.served_at,
            fallback_age_seconds: envelope.fallback_age_seconds,
            data: envelope.data,
        });
        const select = byId('umil-raw-select');
        if (!select) return;
        const current = select.value;
        select.innerHTML = [...payloads.keys()].sort().map(key =>
            `<option value="${escapeHtml(key)}">${escapeHtml(key)}</option>`
        ).join('');
        select.value = payloads.has(current) ? current : name;
        if (isViewActive('raw')) renderRaw();
    }

    function isViewActive(name) {
        const panel = document.querySelector(`[data-umil-panel="${name}"]`);
        return Boolean(panel && !panel.hidden);
    }

    function renderRaw() {
        const key = byId('umil-raw-select')?.value;
        const output = byId('umil-raw');
        if (!output) return;
        output.textContent = key && payloads.has(key) ? jsonText(payloads.get(key)) : 'No validated UMIL payload captured.';
    }

    function setState(state, gatewayStatus = 'LIVE') {
        const normalized = String(gatewayStatus === 'STALE' ? 'STALE' : state || gatewayStatus || 'UNAVAILABLE').toUpperCase();
        const badge = byId('umil-state');
        if (badge) {
            badge.textContent = normalized;
            badge.className = `umil-state ${normalized.toLowerCase()}`;
        }
        const footer = byId('umil-footer-status');
        if (footer) {
            footer.textContent = `UMIL: ● ${normalized}`;
            footer.className = normalized === 'READY' ? 'status-green' :
                (normalized === 'UNAVAILABLE' || normalized === 'INVALID' ? 'status-red' : '');
        }
    }

    function simpleValue(value) {
        if (value === null || value === undefined) return '—';
        if (typeof value === 'boolean') return value ? 'YES' : 'NO';
        if (typeof value === 'number') return Number.isInteger(value) ? String(value) : String(Number(value.toFixed(6)));
        if (Array.isArray(value)) return value.length ? value.map(label).join(', ') : 'None';
        if (typeof value === 'object') return jsonText(value);
        return label(value);
    }

    function kvHtml(value, prefix = '') {
        if (!value || typeof value !== 'object') return `<div class="umil-empty">${escapeHtml(simpleValue(value))}</div>`;
        const rows = [];
        Object.entries(value).forEach(([key, item]) => {
            const name = prefix ? `${prefix}.${key}` : key;
            if (item && typeof item === 'object' && !Array.isArray(item)) {
                Object.entries(item).forEach(([childKey, childValue]) => {
                    rows.push(`<div><span>${escapeHtml(`${name}.${childKey}`)}</span><b>${escapeHtml(simpleValue(childValue))}</b></div>`);
                });
            } else {
                rows.push(`<div><span>${escapeHtml(name)}</span><b>${escapeHtml(simpleValue(item))}</b></div>`);
            }
        });
        return rows.join('') || '<div class="umil-empty">No values.</div>';
    }

    function renderHealth(envelope) {
        const data = envelope.data;
        remember('health', envelope);
        setState(data.state, envelope.gateway_status);
        byId('umil-version').textContent = data.service_version || '—';
        byId('umil-provider').textContent = `${label(data.provider_state)}${data.provider_reason ? ` · ${label(data.provider_reason)}` : ''}`;
        byId('umil-observed').textContent = `${data.observed_instruments ?? '—'} / ${data.eligible_instruments ?? '—'}`;
        byId('umil-batch-age').textContent = age(data.last_batch_age_seconds);
        const evidenceState = data.evidence_chain_state || (data.evidence_chain_valid === true ? 'VALID' : data.evidence_chain_valid === false ? 'INVALID' : 'VERIFYING');
        byId('umil-evidence-chain').textContent = `${label(evidenceState)}${data.evidence_chain_error ? ` · ${data.evidence_chain_error}` : ''}`;
        byId('umil-scale-state').textContent = `${label(data.scale_state)}${data.scale_reason ? ` · ${label(data.scale_reason)}` : ''}`;
        byId('umil-recoveries').textContent = `${data.sequence_gap_recoveries_last_hour ?? 0} / hour · ${data.sequence_gap_recoveries_since_start ?? 0} total`;
        byId('umil-failures').textContent = `${data.intelligence_failures ?? 0} intelligence · ${data.out_of_order_events ?? 0} out-of-order`;
        byId('umil-command-asof').textContent = `Snapshot ${displayDate(data.last_snapshot_at)}`;
    }

    function renderBreadth(envelope) {
        const data = envelope.data;
        remember('breadth', envelope);
        setState(data.state, envelope.gateway_status);
        byId('umil-posture').textContent = label(data.market_posture);
        byId('umil-advancers').textContent = data.advancers ?? '—';
        byId('umil-decliners').textContent = data.decliners ?? '—';
        byId('umil-unchanged').textContent = data.unchanged ?? '—';
        const advanceRatio = finite(data.advance_ratio) ? Math.max(0, Math.min(1, Number(data.advance_ratio))) : 0;
        byId('umil-advance-bar').style.width = `${advanceRatio * 100}%`;
        byId('umil-coverage').textContent = ratioPct(data.quality?.completeness);
        byId('umil-freshness').textContent = finite(data.quality?.freshness_percentile) ? `${fixed(data.quality.freshness_percentile, 1)}th pct` : '—';
        byId('umil-median').textContent = pct(data.median_return_24h_pct);
        byId('umil-dispersion').textContent = pct(data.return_dispersion_pct);
        byId('umil-concentration').textContent = fixed(data.volume_concentration_hhi, 4);
        byId('umil-volume-advance').textContent = ratioPct(data.volume_weighted_advance_ratio);
        byId('umil-sync').textContent = ratioPct(data.quality?.synchronization_quality);
        byId('umil-quality-state').textContent = label(data.quality?.state);
        byId('umil-breadth-asof').textContent = displayDate(data.as_of);
        byId('umil-quality-detail').innerHTML = kvHtml({
            quality_flags: data.quality?.quality_flags,
            stale_dimensions: data.quality?.stale_dimensions,
            missing_providers: data.quality?.missing_providers,
            sequence_gap_recoveries: data.quality?.sequence_gap_recoveries,
            provider_latency_ms: data.quality?.provider_latency_ms,
            dimensions: data.quality?.dimensions,
            evidence_ids: data.evidence_ids,
            calculation_version: data.calculation_version,
            snapshot_schema_version: data.snapshot_schema_version,
            feature_schema_version: data.feature_schema_version,
            quality_schema_version: data.quality_schema_version,
        });
        const note = byId('umil-note');
        if (note) {
            const stale = envelope.gateway_status === 'STALE' ? ` Last valid gateway snapshot: ${age(envelope.fallback_age_seconds)} old.` : '';
            note.textContent = `UMIL is isolated and read-only. It cannot trade, access credentials, train, influence ATS, modify risk, or alter tournament state.${stale}`;
        }
    }

    function renderAuthority(envelope) {
        remember('authority', envelope);
        const data = envelope.data;
        byId('umil-authority').innerHTML = kvHtml({ authority: data.authority, versions: data.versions });
    }

    function mergedUniverse() {
        const metadata = new Map(universe.map(item => [item.venue_symbol, item]));
        return markets.map(item => ({ ...metadata.get(item.venue_symbol), ...item }));
    }

    function renderMarkets() {
        const query = (byId('umil-search')?.value || '').trim().toUpperCase();
        const all = mergedUniverse();
        const visible = all.filter(item => [item.venue_symbol, item.base_asset, item.quote_asset, item.venue, item.instrument_id]
            .some(value => String(value || '').toUpperCase().includes(query)));
        byId('umil-universe-count').textContent = `${visible.length} shown · ${all.length} intelligence rows · ${universe.length} registry rows`;
        const body = byId('umil-markets');
        if (!body) return;
        body.innerHTML = visible.map(item => {
            const direction = Number(item.return_24h_pct) >= 0 ? 'positive' : 'negative';
            const selected = item.venue_symbol === selectedSymbol ? ' selected' : '';
            const state = item.universe_state?.state || item.intelligence_state || '—';
            return `<tr class="${selected.trim()}" data-umil-symbol="${escapeHtml(item.venue_symbol)}" tabindex="0">
                <td><b>${escapeHtml(item.venue_symbol)}</b><br><span class="umil-dna-mini">${escapeHtml(item.base_asset || '—')}/${escapeHtml(item.quote_asset || '—')} · ${escapeHtml(item.venue || '—')}</span></td>
                <td>${escapeHtml(compact(item.last_price))}</td>
                <td class="${direction}">${escapeHtml(pct(item.return_24h_pct))}</td>
                <td>${escapeHtml(compact(item.quote_volume_24h))}</td>
                <td>${escapeHtml(item.coverage_tier || '—')}</td>
                <td>${escapeHtml(label(state))}</td>
                <td>${escapeHtml(label(item.universe_state?.trend || '—'))}</td>
                <td>${escapeHtml(score(item.universe_state?.anomaly_score))}</td>
            </tr>`;
        }).join('') || '<tr><td colspan="8">No matching UMIL instruments.</td></tr>';
        body.querySelectorAll('[data-umil-symbol]').forEach(row => {
            const select = () => selectInstrument(row.dataset.umilSymbol);
            row.addEventListener('click', select);
            row.addEventListener('keydown', event => {
                if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); select(); }
            });
        });
    }

    function renderLeaders() {
        const rich = markets.filter(item => item.dna).sort((a, b) =>
            ((b.dna?.TRN || 0) + (b.dna?.PRS || 0) + (b.dna?.ACT || 0)) -
            ((a.dna?.TRN || 0) + (a.dna?.PRS || 0) + (a.dna?.ACT || 0))
        ).slice(0, 8);
        const fallback = [...markets].sort((a, b) => Math.abs(b.return_24h_pct) - Math.abs(a.return_24h_pct)).slice(0, 8);
        const chosen = rich.length ? rich : fallback;
        byId('umil-leader-list').innerHTML = chosen.map(item =>
            `<button type="button" class="umil-chip" data-umil-leader="${escapeHtml(item.venue_symbol)}">${escapeHtml(item.venue_symbol)} <b>${escapeHtml(item.dna ? `PRS ${score(item.dna.PRS)}` : pct(item.return_24h_pct))}</b></button>`
        ).join('') || 'No eligible markets yet.';
        byId('umil-leader-list').querySelectorAll('[data-umil-leader]').forEach(button =>
            button.addEventListener('click', () => selectInstrument(button.dataset.umilLeader))
        );
    }

    function renderAnomalies(envelope) {
        remember('anomalies', envelope);
        const items = envelope.data.items || [];
        const rejected = envelope.data.gateway_rejected_rows || 0;
        byId('umil-anomaly-count').textContent = `${items.length} calibrated research alerts${rejected ? ` · ${rejected} invalid rows rejected` : ''}`;
        const panel = byId('umil-anomalies');
        panel.innerHTML = items.map((item, index) => {
            const state = item.universe_state || {};
            const flags = state.quality_flags || [];
            const evidence = item.evidence_record_id;
            return `<article class="umil-anomaly" style="animation-delay:${Math.min(index * 22, 180)}ms">
                <div class="umil-anomaly-head"><button type="button" data-anomaly-symbol="${escapeHtml(item.symbol)}"><strong>${escapeHtml(item.symbol)}</strong></button><b>${escapeHtml(fixed(item.anomaly_score, 2))} calibrated</b></div>
                <div class="umil-anomaly-metrics">
                    <span>Raw signal <b>${escapeHtml(fixed(item.raw_anomaly_score, 2))}</b></span><span>Confidence <b>${escapeHtml(fixed(item.confidence_score, 1))} · ${escapeHtml(label(item.confidence_tier))}</b></span>
                    <span>Evidence <b>${escapeHtml(label(item.evidence_tier))}</b></span><span>Usage <b>${escapeHtml(label(item.usage))}</b></span>
                    <span>State <b>${escapeHtml(label(state.state))}</b></span><span>Trend <b>${escapeHtml(label(state.trend))}</b></span>
                    <span>24h <b>${escapeHtml(pct(state.return_24h_pct))}</b></span><span>Return pctile <b>${escapeHtml(fixed(state.return_percentile, 2))}</b></span>
                    <span>Range position <b>${escapeHtml(ratioPct(state.range_position))}</b></span><span>Intraday range <b>${escapeHtml(pct(state.intraday_range_pct))}</b></span>
                    <span>Volume pctile <b>${escapeHtml(fixed(state.volume_percentile, 2))}</b></span><span>Freshness <b>${escapeHtml(item.display_freshness)} · ${escapeHtml(age(item.age_seconds))}</b></span>
                </div>
                <small>${escapeHtml(item.coverage)} · ${escapeHtml(flags.length ? flags.join(', ') : 'NO QUALITY FLAGS')}</small>
                <small>Available: ${escapeHtml((state.available_dimensions || []).join(', ') || '—')} · Missing: ${escapeHtml((state.missing_dimensions || []).join(', ') || '—')}</small>
                ${item.dna ? `<div class="umil-dna-grid">${dnaRows(item.dna)}</div>` : '<div class="umil-empty">Full DNA is not available for this universe-tier anomaly.</div>'}
                ${evidence ? `<button type="button" class="umil-link-action" data-evidence-id="${escapeHtml(evidence)}">Trace evidence ${escapeHtml(evidence)}</button>` : '<span class="umil-muted">No evidence record ID at this coverage tier.</span>'}
                <details class="umil-payload"><summary>Complete anomaly payload</summary>${jsonBlock(item)}</details>
            </article>`;
        }).join('') || 'No anomalies currently meet the evidence threshold.';
        panel.querySelectorAll('[data-anomaly-symbol]').forEach(button =>
            button.addEventListener('click', () => selectInstrument(button.dataset.anomalySymbol))
        );
        panel.querySelectorAll('[data-evidence-id]').forEach(button =>
            button.addEventListener('click', () => openEvidence(button.dataset.evidenceId))
        );
    }

    function dnaRows(dna) {
        return DNA_ORDER.map(key => `<div class="umil-dna-row ${key === 'RSK' ? 'risk' : ''}"><b>${key}</b><span class="umil-dna-track"><i class="umil-dna-fill" style="width:${Math.max(0, Math.min(100, Number(dna?.[key]) || 0))}%"></i></span><em>${escapeHtml(score(dna?.[key]))}</em></div>`).join('');
    }

    function renderDimensions(data) {
        const dimensions = data?.dimensions || {};
        byId('umil-dimensions').innerHTML = DNA_ORDER.map(key => {
            const item = dimensions[key];
            if (!item) return `<article class="umil-mini-card"><strong>${key}</strong><span>Unavailable</span></article>`;
            return `<article class="umil-mini-card">
                <div><strong>${key}</strong><b>${escapeHtml(score(item.score))}</b></div>
                <span>${escapeHtml(label(item.state))} · ${escapeHtml(label(item.normalization_state))}</span>
                <small>Confidence ${escapeHtml(fixed(item.confidence, 1))} · cross-section ${escapeHtml(fixed(item.cross_sectional_percentile, 1))} · time-series ${escapeHtml(fixed(item.time_series_percentile, 1))}</small>
                <small>Raw ${escapeHtml(fixed(item.raw_score, 2))} · peers ${escapeHtml(item.peer_count ?? '—')} · history ${escapeHtml(item.history_count ?? '—')}</small>
                <small>Inputs: ${escapeHtml((item.inputs || []).join(', ') || '—')}</small>
            </article>`;
        }).join('');
    }

    function renderCommentary(data) {
        const commentary = data?.commentary || {};
        const claims = commentary.claims || [];
        byId('umil-commentary').innerHTML = `<article class="umil-mini-card"><strong>Summary</strong><p>${escapeHtml(commentary.summary || 'No commentary available.')}</p><small>${escapeHtml(commentary.commentary_schema_version || '—')} · market-data-only ${commentary.generated_from_market_data_only === true ? 'YES' : 'NO'}</small></article>` +
            claims.map(claim => `<article class="umil-mini-card"><div><strong>${escapeHtml(label(claim.claim_id))}</strong><span>${escapeHtml((claim.evidence_keys || []).join(', '))}</span></div><p>${escapeHtml(claim.text)}</p><small>Evidence: ${escapeHtml((claim.evidence_ids || []).join(', ') || '—')}</small></article>`).join('');
    }

    function renderTimeframes(data) {
        const rows = Object.values(data?.timeframes || {});
        byId('umil-timeframes').innerHTML = `<table class="umil-table umil-table-dense"><thead><tr><th>TF</th><th>As of</th><th>Structure</th><th>RSI</th><th>1</th><th>6</th><th>24</th><th>ATR</th><th>Realized vol</th><th>Range</th><th>Rel volume Z</th><th>Taker buy</th><th>Samples</th></tr></thead><tbody>${rows.map(item => `<tr>
            <td><b>${escapeHtml(item.interval)}</b></td><td>${escapeHtml(displayDate(item.as_of))}</td><td>${escapeHtml(label(item.structure_state))}</td>
            <td>${escapeHtml(fixed(item.rsi_14, 2))}</td><td>${escapeHtml(pct(item.return_1_pct))}</td><td>${escapeHtml(pct(item.return_6_pct))}</td><td>${escapeHtml(pct(item.return_24_pct))}</td>
            <td>${escapeHtml(pct(item.atr_pct))}</td><td>${escapeHtml(pct(item.realized_volatility_pct))}</td><td>${escapeHtml(ratioPct(item.range_position))}</td>
            <td>${escapeHtml(fixed(item.relative_volume_z, 3))}</td><td>${escapeHtml(ratioPct(item.taker_buy_ratio))}</td><td>${escapeHtml(item.sample_count ?? '—')}</td>
        </tr>`).join('')}</tbody></table>`;
    }

    function renderWatch(data) {
        const rows = data?.watch_conditions || [];
        byId('umil-watch').innerHTML = rows.map(item => `<article class="umil-mini-card">
            <div><strong>${escapeHtml(label(item.state))}</strong><span>${escapeHtml(item.metric)} ${escapeHtml(item.comparison)} ${escapeHtml(simpleValue(item.threshold))}</span></div>
            <p>${escapeHtml(item.interpretation)}</p>
            <small>Created ${escapeHtml(displayDate(item.created_at))} · expires ${escapeHtml(displayDate(item.expires_at))} · resolved ${escapeHtml(displayDate(item.resolved_at))}</small>
            <small>Invalidation: ${escapeHtml(item.invalidation_metric)} ${escapeHtml(item.invalidation_comparison)} ${escapeHtml(simpleValue(item.invalidation_threshold))}</small>
        </article>`).join('') || '<div class="umil-empty">No active or historical watch conditions.</div>';
    }

    function renderInstrument(results, symbol) {
        const intelligence = results.intelligence?.data || {};
        const dna = results.dna?.data || intelligence;
        const insights = results.insights?.data || intelligence;
        const scale = results.scale?.data?.scale || intelligence.market_scale || null;
        const name = String(intelligence.instrument_id || symbol).split(':').pop();
        const state = intelligence.state || 'UNAVAILABLE';
        byId('umil-selected-status').textContent = `${label(state)} · ${displayDate(intelligence.as_of)}`;
        byId('umil-instrument-summary').hidden = false;
        byId('umil-instrument-summary').innerHTML = `<div class="umil-detail-head"><strong>${escapeHtml(name)}</strong><span>Clarity ${escapeHtml(score(intelligence.clarity))} · confidence ${escapeHtml(score(intelligence.confidence))}</span></div>
            <div class="umil-dna-grid">${dnaRows(dna.dna || intelligence.dna)}</div>
            <p class="umil-summary">${escapeHtml(insights.commentary?.summary || intelligence.commentary?.summary || 'Evidence-linked commentary is warming.')}</p>
            <div class="umil-quality-grid"><span>State <b>${escapeHtml(label(state))}</b></span><span>Quality flags <b>${escapeHtml((intelligence.quality_flags || []).join(', ') || 'None')}</b></span><span>Snapshot <b>${escapeHtml(intelligence.snapshot_id || '—')}</b></span><span>Evidence record <b>${escapeHtml(intelligence.evidence_record_id || insights.evidence_record_id || '—')}</b></span><span>Market rank <b>${escapeHtml(scale?.rank ?? '—')}</b></span><span>Identity <b>${escapeHtml(label(scale?.identity_quality))}</b></span></div>`;
        renderDimensions(dna);
        renderCommentary(insights);
        renderTimeframes(intelligence);
        byId('umil-microstructure').innerHTML = kvHtml(intelligence.microstructure);
        byId('umil-derivatives').innerHTML = kvHtml(intelligence.derivatives);
        renderWatch(intelligence);
        const combined = { intelligence, dna, insights, scale: results.scale?.data || scale };
        byId('umil-instrument-payload').textContent = jsonText(combined);
        const evidenceId = intelligence.evidence_record_id || insights.evidence_record_id || intelligence.evidence_ids?.[0];
        if (evidenceId) byId('umil-evidence-input').value = evidenceId;
    }

    async function loadInstrument(symbol) {
        const normalized = String(symbol || '').trim().toUpperCase();
        if (!/^[A-Z0-9_-]{2,40}$/.test(normalized)) {
            byId('umil-selected-status').textContent = 'Invalid symbol';
            return;
        }
        selectedSymbol = normalized;
        byId('umil-symbol-input').value = normalized;
        byId('umil-selected-status').textContent = 'Loading complete UMIL contract…';
        renderMarkets();
        const generation = requestGeneration;
        const settled = await Promise.allSettled([
            get(`/api/pro/umil/intelligence/${encodeURIComponent(normalized)}`),
            get(`/api/pro/umil/dna/${encodeURIComponent(normalized)}`),
            get(`/api/pro/umil/insights/${encodeURIComponent(normalized)}`),
            get(`/api/pro/umil/scale/${encodeURIComponent(normalized)}`),
        ]);
        if (!active || generation !== requestGeneration || selectedSymbol !== normalized) return;
        const keys = ['intelligence', 'dna', 'insights', 'scale'];
        const results = {};
        settled.forEach((result, index) => {
            if (result.status === 'fulfilled') {
                results[keys[index]] = result.value;
                remember(`${keys[index]}:${normalized}`, result.value);
            }
        });
        if (!results.intelligence) {
            byId('umil-selected-status').textContent = settled[0].reason?.message || `${normalized} intelligence unavailable`;
            return;
        }
        renderInstrument(results, normalized);
    }

    function selectInstrument(symbol) {
        selectedSymbol = symbol;
        setView('instrument');
        loadInstrument(symbol);
    }

    async function loadHistory() {
        const input = byId('umil-history-asof');
        const panel = byId('umil-history');
        panel.hidden = false;
        if (!input?.value) {
            panel.textContent = 'Choose a historical knowledge cutoff.';
            return;
        }
        let asOf;
        try { asOf = new Date(input.value).toISOString(); }
        catch (_) { panel.textContent = 'Invalid historical cutoff.'; return; }
        panel.textContent = 'Loading point-in-time evidence…';
        try {
            const envelope = await get(`/api/pro/umil/history/${encodeURIComponent(selectedSymbol)}?as_of=${encodeURIComponent(asOf)}`);
            remember(`history:${selectedSymbol}:${asOf}`, envelope);
            panel.innerHTML = `<div class="umil-detail-head"><strong>${escapeHtml(selectedSymbol)} history</strong><span>Knowledge cutoff: ${escapeHtml(displayDate(envelope.data.requested_as_of))}</span></div>${jsonBlock(envelope.data)}`;
            const evidenceId = envelope.data.evidence?.evidence_id;
            if (evidenceId) byId('umil-evidence-input').value = evidenceId;
        } catch (error) {
            panel.textContent = `Historical intelligence unavailable: ${error.message}`;
        }
    }

    async function loadScale() {
        if (scaleLoaded) return;
        byId('umil-scale-count').textContent = 'Loading market scale…';
        try {
            const envelope = await get('/api/pro/umil/scale?limit=500');
            remember('scale:global', envelope);
            scaleRows = envelope.data.items || [];
            scaleLoaded = true;
            renderScale();
        } catch (error) {
            byId('umil-scale-count').textContent = `Scale unavailable: ${error.message}`;
        }
    }

    function renderScale() {
        const query = (byId('umil-scale-search')?.value || '').trim().toUpperCase();
        const rows = scaleRows.filter(item => [item.symbol, item.asset_id, item.identity_quality, item.rank]
            .some(value => String(value ?? '').toUpperCase().includes(query)));
        byId('umil-scale-count').textContent = `${rows.length} shown · ${scaleRows.length} total`;
        byId('umil-scale').innerHTML = rows.map(item => `<tr>
            <td>${escapeHtml(item.rank ?? '—')}</td><td><b>${escapeHtml(item.symbol)}</b><br><span class="umil-dna-mini">${escapeHtml(item.asset_id || '—')}</span></td>
            <td>${escapeHtml(compact(item.market_cap_usd))}</td><td>${escapeHtml(compact(item.fully_diluted_value_usd))}</td><td>${escapeHtml(compact(item.circulating_supply))}</td>
            <td>${escapeHtml(label(item.identity_quality))}</td><td>${escapeHtml(displayDate(item.as_of))}</td>
        </tr>`).join('') || '<tr><td colspan="7">No matching scale records.</td></tr>';
    }

    async function openEvidence(evidenceId) {
        byId('umil-evidence-input').value = evidenceId || '';
        setView('evidence');
        await loadEvidence();
    }

    async function loadEvidence() {
        const evidenceId = (byId('umil-evidence-input')?.value || '').trim();
        const panel = byId('umil-evidence');
        if (!UUID.test(evidenceId)) {
            panel.textContent = 'Enter a valid evidence UUID.';
            return;
        }
        panel.textContent = 'Tracing immutable evidence lineage…';
        try {
            const envelope = await get(`/api/pro/umil/evidence/${encodeURIComponent(evidenceId)}`);
            remember(`evidence:${evidenceId}`, envelope);
            const data = envelope.data;
            panel.innerHTML = `<div class="umil-detail-head"><strong>${escapeHtml(data.event_type)}</strong><span>Sequence ${escapeHtml(data.sequence)}</span></div>
                <div class="umil-kv-list">${kvHtml(data)}</div>
                <details class="umil-payload" open><summary>Complete evidence lineage</summary>${jsonBlock(data)}</details>`;
        } catch (error) {
            panel.textContent = `Evidence unavailable: ${error.message}`;
        }
    }

    function requestOnce(key, path, onSuccess, onFailure = null) {
        if (!active || document.hidden || inFlight.has(key)) return;
        const generation = requestGeneration;
        inFlight.add(key);
        get(path).then(envelope => {
            if (active && generation === requestGeneration) onSuccess(envelope);
        }).catch(error => {
            if (active && generation === requestGeneration && onFailure) onFailure(error);
        }).finally(() => {
            inFlight.delete(key);
        });
    }

    function refreshCore() {
        requestOnce('health', '/api/pro/umil/health', renderHealth, () => {
            if (!payloads.has('health')) setState('UNAVAILABLE');
        });
        requestOnce('breadth', '/api/pro/umil/breadth', renderBreadth);
    }

    function refreshAnomalies(force = false) {
        const now = Date.now();
        if (!force && now - lastAnomalyRefresh < ANOMALY_REFRESH_MS) return;
        lastAnomalyRefresh = now;
        requestOnce('anomalies', '/api/pro/umil/anomalies?limit=25', renderAnomalies, () => { lastAnomalyRefresh = 0; });
    }

    function refreshMarkets(force = false) {
        const now = Date.now();
        if (!force && now - lastMarketRefresh < MARKET_REFRESH_MS) return;
        lastMarketRefresh = now;
        requestOnce('markets', '/api/pro/umil/markets?offset=0&limit=500', envelope => {
            remember('markets', envelope);
            markets = [...(envelope.data.items || [])].sort((a, b) =>
                Number(b.quote_volume_24h || 0) - Number(a.quote_volume_24h || 0)
            );
            renderLeaders();
            if (isViewActive('universe')) renderMarkets();
        }, () => { lastMarketRefresh = 0; });
    }

    function refreshAuthority(force = false) {
        const now = Date.now();
        if (!force && now - lastAuthorityRefresh < SLOW_REFRESH_MS) return;
        lastAuthorityRefresh = now;
        requestOnce('authority', '/api/pro/umil/authority', renderAuthority, () => {
            lastAuthorityRefresh = 0;
        });
    }

    function refreshUniverse(force = false) {
        const now = Date.now();
        if (!force && now - lastUniverseRefresh < SLOW_REFRESH_MS) return;
        lastUniverseRefresh = now;
        requestOnce('universe', '/api/pro/umil/universe?offset=0&limit=500', envelope => {
            remember('universe', envelope);
            universe = envelope.data.items || [];
            if (isViewActive('universe')) renderMarkets();
        }, () => { lastUniverseRefresh = 0; });
    }

    function refresh() {
        if (!active || document.hidden) return;
        refreshCore();
        refreshAnomalies();
        refreshMarkets();
        refreshAuthority();
        if (isViewActive('universe')) refreshUniverse();
    }

    function setView(name) {
        document.querySelectorAll('[data-umil-view]').forEach(button => {
            const on = button.dataset.umilView === name;
            button.classList.toggle('active', on);
            button.setAttribute('aria-selected', on ? 'true' : 'false');
        });
        document.querySelectorAll('[data-umil-panel]').forEach(panel => {
            const on = panel.dataset.umilPanel === name;
            panel.hidden = !on;
            panel.classList.toggle('active', on);
        });
        if (name === 'command') refreshCore();
        if (name === 'universe') {
            renderMarkets();
            refreshMarkets(true);
            refreshUniverse(true);
        }
        if (name === 'anomalies') refreshAnomalies(true);
        if (name === 'scale') loadScale();
        if (name === 'raw') renderRaw();
    }

    function activate(value) {
        active = value;
        if (timer) { clearInterval(timer); timer = null; }
        requestGeneration++;
        if (active) {
            const generation = requestGeneration;
            refreshCore();
            window.setTimeout(() => {
                if (active && generation === requestGeneration) refreshAnomalies(true);
            }, 150);
            window.setTimeout(() => {
                if (active && generation === requestGeneration) refreshMarkets(true);
            }, 400);
            window.setTimeout(() => {
                if (active && generation === requestGeneration) refreshAuthority(true);
            }, 700);
            if (isViewActive('universe')) refreshUniverse(true);
            timer = setInterval(refresh, REFRESH_MS);
        } else {
            inFlight.clear();
        }
    }

    const historyInput = byId('umil-history-asof');
    if (historyInput && !historyInput.value) {
        const now = new Date(Date.now() - new Date().getTimezoneOffset() * 60000);
        historyInput.value = now.toISOString().slice(0, 19);
    }

    document.querySelectorAll('[data-umil-view]').forEach(button =>
        button.addEventListener('click', () => setView(button.dataset.umilView))
    );
    byId('umil-search')?.addEventListener('input', renderMarkets);
    byId('umil-scale-search')?.addEventListener('input', renderScale);
    byId('umil-raw-select')?.addEventListener('change', renderRaw);
    byId('umil-load-symbol')?.addEventListener('click', () => loadInstrument(byId('umil-symbol-input')?.value));
    byId('umil-symbol-input')?.addEventListener('keydown', event => {
        if (event.key === 'Enter') loadInstrument(event.currentTarget.value);
    });
    byId('umil-load-history')?.addEventListener('click', loadHistory);
    byId('umil-load-evidence')?.addEventListener('click', loadEvidence);
    byId('umil-evidence-input')?.addEventListener('keydown', event => {
        if (event.key === 'Enter') loadEvidence();
    });
    window.addEventListener('ats:workspace', event => activate(event.detail?.name === 'intelligence'));
    document.addEventListener('visibilitychange', () => {
        if (!document.hidden && active) refresh();
    });
    activate(window.location.hash === '#intelligence');
})();
