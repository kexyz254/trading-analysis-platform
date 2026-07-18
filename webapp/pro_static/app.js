/**
 * ATS Pro Trading Terminal — vanilla JS
 *
 * Chart history (all timeframes) comes straight from the exchange via
 * /api/ohlcv — the same source the engine trades against — so every
 * timeframe has full history immediately. The in-progress candle streams
 * live over the shared browser->Binance WebSocket feed (/static/ws.js).
 * Engine-computed state (predictions, position, trades, candidates) still
 * polls the journal-backed /api/pro/* endpoints: that data only changes
 * when an engine cycle runs, so polling is the correct transport for it.
 */

let chart, candleSeries, volumeSeries, sma20, sma50, bbUpper, bbLower;
let bars = [];               // current chart bars, unix-second times, oldest first
let currentSymbol = 'BTC/USDT';
let currentTimeframe = '15m';
let pollTimer;
const POLL_INTERVAL = 10000;
const HISTORY_LIMIT = 300;

const TF_SECONDS = {
    '1m': 60, '3m': 180, '5m': 300, '15m': 900, '30m': 1800,
    '1h': 3600, '2h': 7200, '4h': 14400, '6h': 21600, '12h': 43200, '1d': 86400,
};

// --- Initialization ---
document.addEventListener('DOMContentLoaded', initTerminal);

async function initTerminal() {
    if (!chart) setupChart();
    let status;
    try {
        status = await api('/api/status');
    } catch (e) {
        // A transient backend error at page load (e.g. a brief "database is
        // locked" while the engine writes) must degrade to a retry, not
        // permanently brick the whole terminal on a dead init chain.
        document.getElementById('uptime').textContent = 'Backend unreachable — retrying…';
        setTimeout(initTerminal, 5000);
        return;
    }
    renderStatus(status);
    renderSymbolList(status.symbols);
    await renderTimeframeTabs();
    initLiveFeed(status.ws_base_url);
    subscribeTickers(status.symbols, onTicker);
    await refreshAll();
    _lastKlineAt = Date.now(); // start the WS watchdog clock from "just loaded"
    startLiveCandles();
    startPolling();
    // Capital hits the real exchange balance API - poll it on a slower
    // cadence (30s) than the 10s card refresh to respect rate limits.
    loadCapital();
    if (!_capitalTimer) _capitalTimer = setInterval(loadCapital, 30000);

    document.getElementById('symbol-select').addEventListener('change', async (e) => {
        currentSymbol = e.target.value;
        _viewEpoch++;
        bars = []; // stop live klines painting onto the old symbol's series
        await refreshAll();
        startLiveCandles();
    });
}

// --- Chart Setup ---
function setupChart() {
    const container = document.getElementById('chart');
    chart = LightweightCharts.createChart(container, {
        autoSize: true,
        layout: {
            background: { type: 'solid', color: '#0A0E14' },
            textColor: '#EAEAEA',
        },
        grid: {
            vertLines: { color: 'rgba(255,255,255,0.05)' },
            horzLines: { color: 'rgba(255,255,255,0.05)' },
        },
        crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
        rightPriceScale: { borderColor: 'rgba(255,255,255,0.2)' },
        timeScale: { borderColor: 'rgba(255,255,255,0.2)', timeVisible: true, secondsVisible: false },
    });

    candleSeries = chart.addCandlestickSeries({
        upColor: '#00FFC2',
        downColor: '#FF3366',
        borderDownColor: '#FF3366',
        borderUpColor: '#00FFC2',
        wickDownColor: '#FF3366',
        wickUpColor: '#00FFC2',
    });

    // Volume lives in the same chart on its own hidden price scale (bottom
    // 20%) — a second chart panel can never stay perfectly time-synced with
    // the main one during live streaming, an overlay always is.
    volumeSeries = chart.addHistogramSeries({
        priceScaleId: '',
        priceFormat: { type: 'volume' },
        lastValueVisible: false,
        priceLineVisible: false,
    });
    chart.priceScale('').applyOptions({ scaleMargins: { top: 0.8, bottom: 0 } });

    const lineOpts = { lineWidth: 1, priceLineVisible: false, lastValueVisible: false };
    sma20 = chart.addLineSeries({ color: '#FFB800', ...lineOpts });
    sma50 = chart.addLineSeries({ color: '#AA66FF', ...lineOpts });
    bbUpper = chart.addLineSeries({ color: 'rgba(0,255,196,0.3)', lineStyle: LightweightCharts.LineStyle.Dashed, ...lineOpts });
    bbLower = chart.addLineSeries({ color: 'rgba(0,255,196,0.3)', lineStyle: LightweightCharts.LineStyle.Dashed, ...lineOpts });
}

// --- Data Fetching ---
async function api(url) {
    const res = await fetch(url);
    if (!res.ok) throw new Error(res.statusText);
    return res.json();
}

// Stale-response guard: an in-flight request can outlive a symbol/timeframe
// switch, and applying its payload would paint the previous view's data
// onto the new one. Symbol/timeframe-scoped loaders fetch through this and
// treat null as "view changed while waiting - drop it".
let _viewEpoch = 0;
async function apiScoped(url) {
    const epoch = _viewEpoch;
    const data = await api(url);
    return epoch === _viewEpoch ? data : null;
}

let _lastKlineAt = 0;
let _lastTickerAt = 0;

function removeSeriesSafe(series) {
    if (series) { try { chart.removeSeries(series); } catch (e) { /* already removed */ } }
}

async function refreshAll() {
    try {
        await loadChart();
    } catch (e) { console.warn('Chart load error', e); }
    await refreshCards();
}

async function refreshCards() {
    try {
        await Promise.all([
            loadPrediction(),
            loadPosition(),
            loadFeatures(),
            loadTrades(),
            loadCandidates(),
            loadHealth(),
            loadDecisionMarkers(),
            loadPriceCall(),
            loadModelInfo(),
            loadPatterns(),
            loadStatusBadge(),
            loadPipeline(),
            loadInterpreter(),
            loadTournament(),
            loadAiPerformance(),
        ]);
    } catch (e) { console.warn('Refresh error', e); }
    // WS watchdog: if no kline arrived in 45s the browser's Binance socket
    // is dead/blocked - degrade to 10s REST-polled candles instead of a
    // silently frozen "live" chart.
    if (_lastKlineAt && Date.now() - _lastKlineAt > 45000) {
        try { await loadChart(); } catch (e) { /* next poll retries */ }
    }
    // Kept out of the Promise.all: first call per (symbol, timeframe)
    // trains models server-side and can take a few seconds - it must not
    // delay every other card while it does.
    loadForecast();
}

// --- Live candle streaming (ws.js) ---
function binancePair(symbol) {
    return symbol.replace('/', '').toUpperCase(); // "BTC/USDT" -> "BTCUSDT"
}

function startLiveCandles() {
    setKlineSubscription(currentSymbol, currentTimeframe, onKline);
}

function onKline(k) {
    if (k.interval !== currentTimeframe || k.symbol !== binancePair(currentSymbol)) return;
    _lastKlineAt = Date.now();
    if (!bars.length) return; // mid-switch: the REST history hasn't landed yet
    const bar = { time: k.time, open: k.open, high: k.high, low: k.low, close: k.close, volume: k.volume };
    const last = bars[bars.length - 1];
    if (last && bar.time === last.time) {
        bars[bars.length - 1] = bar;
    } else if (!last || bar.time > last.time) {
        bars.push(bar);
        if (bars.length > HISTORY_LIMIT) bars.shift();
    } else {
        return; // stale/out-of-order update
    }
    candleSeries.update(bar);
    volumeSeries.update(volPoint(bar));
    // Recompute on every tick, not just closed candles - 300 bars of SMA/BB
    // math is sub-millisecond, and live-moving overlay lines are the point.
    recomputeIndicators();
    updatePriceHeader();
}

function onTicker(t) {
    if (t.symbol !== binancePair(currentSymbol)) return;
    _lastTickerAt = Date.now();
    const el = document.getElementById('price-change');
    el.textContent = `${t.changePct >= 0 ? '+' : ''}${t.changePct.toFixed(2)}% (24h)`;
    el.style.color = t.changePct >= 0 ? '#00FFC2' : '#FF3366';
}

// --- Chart data ---
async function loadChart() {
    // A symbol/timeframe switch invalidates every overlay drawn in the old
    // time coordinate system - clear them now rather than leaving stale
    // lines up until their own refreshes land.
    removeSeriesSafe(_projectionSeries); _projectionSeries = null;
    _patternSeries.forEach(removeSeriesSafe);
    _patternSeries = [];
    const data = await apiScoped(
        `/api/ohlcv?symbol=${encodeURIComponent(currentSymbol)}&timeframe=${currentTimeframe}&limit=${HISTORY_LIMIT}`
    );
    if (data === null) return;                       // view changed while waiting
    if (!Array.isArray(data) || data.length === 0) return; // keep the last good chart
    bars = data.map(b => ({
        time: Math.floor(b.timestamp / 1000), // exchange sends ms epoch, chart wants seconds
        open: b.open, high: b.high, low: b.low, close: b.close, volume: b.volume,
    }));
    candleSeries.setData(bars);
    volumeSeries.setData(bars.map(volPoint));
    recomputeIndicators();
    updatePriceHeader();
    chart.timeScale().scrollToRealTime();
}

function volPoint(b) {
    return { time: b.time, value: b.volume, color: b.close >= b.open ? 'rgba(0,255,196,0.4)' : 'rgba(255,51,102,0.4)' };
}

function recomputeIndicators() {
    const closes = bars.map(b => b.close);
    sma20.setData(seriesPoints(calcSMA(closes, 20)));
    sma50.setData(seriesPoints(calcSMA(closes, 50)));
    const bb = calcBollinger(closes, 20, 2);
    bbUpper.setData(seriesPoints(bb.upper));
    bbLower.setData(seriesPoints(bb.lower));
}

function seriesPoints(values) {
    const points = [];
    for (let i = 0; i < values.length; i++) {
        if (values[i] !== null) points.push({ time: bars[i].time, value: values[i] });
    }
    return points;
}

function calcSMA(data, period) {
    const result = [];
    let sum = 0;
    for (let i = 0; i < data.length; i++) {
        sum += data[i];
        if (i >= period) sum -= data[i - period];
        result.push(i < period - 1 ? null : sum / period);
    }
    return result;
}

function calcBollinger(data, period, mult) {
    const sma = calcSMA(data, period);
    const upper = [], lower = [];
    for (let i = 0; i < data.length; i++) {
        if (i < period - 1) { upper.push(null); lower.push(null); continue; }
        const slice = data.slice(i - period + 1, i + 1);
        const mean = sma[i];
        const variance = slice.reduce((sum, val) => sum + Math.pow(val - mean, 2), 0) / period;
        const std = Math.sqrt(variance);
        upper.push(mean + mult * std);
        lower.push(mean - mult * std);
    }
    return { upper, lower };
}

function formatPrice(p) {
    if (p >= 100) return p.toFixed(2);
    if (p >= 1) return p.toFixed(4);
    return p.toFixed(6);
}

function updatePriceHeader() {
    if (bars.length === 0) return;
    const last = bars[bars.length - 1];
    document.getElementById('current-price').textContent = `$${formatPrice(last.close)}`;
    // Fallback when the browser->Binance ticker stream is silent (blocked
    // network, geo-block, testnet outage): show candle-over-candle change
    // from REST data instead of leaving the header frozen at '—'.
    if (Date.now() - _lastTickerAt > 90000 && bars.length > 1) {
        const prev = bars[bars.length - 2].close;
        const pct = ((last.close - prev) / prev * 100).toFixed(2);
        const el = document.getElementById('price-change');
        el.textContent = `${last.close >= prev ? '+' : ''}${pct}% (bar)`;
        el.style.color = last.close >= prev ? '#00FFC2' : '#FF3366';
    }
}

// --- Price Call Engine: 5m + 15m, self-scoring, with confidence signals ---
// Chart price lines for the currently-open calls (one per horizon), and the
// due-times keyed by horizon so the countdowns tick independently.
let _callPriceLines = {};   // horizon -> priceLine
let _callDueAt = {};        // horizon -> epoch ms

async function loadPriceCall() {
    try {
        const data = await apiScoped(`/api/pro/price-calls/${currentSymbol}`);
        if (data === null) return;
        const content = document.getElementById('call-content');

        // Clear old chart lines/countdowns; rebuild from the fresh payload.
        Object.values(_callPriceLines).forEach(l => { try { candleSeries.removePriceLine(l); } catch (e) {} });
        _callPriceLines = {};
        _callDueAt = {};

        const blocks = (data.horizons || []).map(h => renderHorizon(h)).join('');
        content.innerHTML = blocks || '<div class="call-sub">No calls yet (runs for configured symbols only)</div>';
        updateCallCountdown();
    } catch (e) { /* ignore */ }
}

function renderHorizon(h) {
    const call = h.open_call;
    const stats = h.stats || {};
    const sig = h.signal;
    // Which engine made this call: a promoted, walk-forward-validated
    // tournament champion, or the unvalidated heuristic forecast fallback.
    const champ = h.champion_backed === true;
    const engineBadge = `<span class="call-engine ${champ ? 'champion' : 'heuristic'}" `
        + `title="${champ ? 'Direction from a promoted, walk-forward-validated tournament champion model'
                          : 'Direction from the unvalidated heuristic forecast — no champion promoted yet'}">`
        + `${champ ? '🏆 ML Champion' : '📐 Heuristic'}</span>`;
    let html = `<div class="call-horizon"><div class="call-hz-label">${h.horizon} call ${engineBadge}</div>`;

    // Confidence signal (only present when confidence >= threshold).
    if (sig) {
        const scls = sig.side === 'BUY' ? 'buy' : 'sell';
        html += `<div class="call-signal ${scls} ${sig.validated ? 'validated' : ''}">
            ⚡ ${sig.side} SIGNAL · ${(sig.confidence * 100).toFixed(0)}%
            <span class="call-sig-note">${sig.note}</span></div>`;
    }

    if (call) {
        const up = call.direction === 'UP';
        _callDueAt[h.horizon] = Date.parse(call.due_at);
        // Settle time in the viewer's own clock (East Africa Time etc.).
        const settleClock = new Date(_callDueAt[h.horizon]).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
        // Price to beat = the reference the FINAL price at settlement must
        // cross: an UP call wins if the final price is above it, DOWN below.
        const beat = call.price_at_call;
        html += `<div class="call-main ${up ? 'up' : 'down'}">
                ${up ? '▲' : '▼'} ${call.direction} → $${formatPrice(call.projected_price)}
                <span class="call-countdown" data-hz="${h.horizon}"></span></div>
            <div class="call-beat">Price to beat <strong>$${formatPrice(beat)}</strong>
                <span class="beat-cond ${up ? 'up' : 'down'}">final must be ${up ? 'above ↑' : 'below ↓'}</span></div>
            <div class="call-sub">settles at <strong>${settleClock}</strong> · target $${formatPrice(call.projected_price)} · confidence ${(call.probability * 100).toFixed(0)}%</div>`;
        // The beat line on the chart: watch the live candle cross it.
        _callPriceLines[h.horizon] = candleSeries.createPriceLine({
            price: beat,
            color: up ? '#00FFC2' : '#FF3366',
            lineWidth: 1,
            lineStyle: LightweightCharts.LineStyle.Solid,
            axisLabelVisible: true,
            title: `${h.horizon} beat`,
        });
    } else {
        html += '<div class="call-sub">Opening first call…</div>';
    }

    // Performance for this horizon.
    if (stats.n > 0) {
        const acc = (stats.accuracy * 100).toFixed(0);
        const miss = (stats.avg_miss_pct * 100).toFixed(2);
        const edge = stats.accuracy >= 0.55 ? 'edge' : stats.accuracy <= 0.45 ? 'poor' : 'flat';
        const dots = (h.recent || []).map(r =>
            `<span class="call-dot ${r.correct ? 'hit' : 'miss'}">${r.correct ? '✓' : '✗'}</span>`).join('');
        html += `<div class="call-score">Last ${stats.n}: <strong class="acc-${edge}">${acc}%</strong> correct · avg miss ${miss}%</div>
                 <div class="call-dots">${dots}</div>`;
    } else {
        html += '<div class="call-sub dim">score builds as calls settle…</div>';
    }
    html += '</div>';
    return html;
}

function updateCallCountdown() {
    document.querySelectorAll('.call-countdown').forEach(el => {
        const hz = el.dataset.hz;
        const due = _callDueAt[hz];
        if (!due) return;
        const remaining = Math.max(0, Math.floor((due - Date.now()) / 1000));
        const m = Math.floor(remaining / 60), s = remaining % 60;
        el.textContent = remaining > 0 ? `settles in ${m}:${String(s).padStart(2, '0')}` : 'settling…';
    });
}
setInterval(updateCallCountdown, 1000);

// --- Chart patterns: trendlines on the chart + card list ---
let _patternSeries = [];

const PATTERN_LABELS = {
    ascending_triangle: 'Ascending Triangle', descending_triangle: 'Descending Triangle',
    symmetric_triangle: 'Symmetric Triangle', rising_wedge: 'Rising Wedge', falling_wedge: 'Falling Wedge',
    bullish_rectangle: 'Bullish Rectangle', bearish_rectangle: 'Bearish Rectangle',
    bull_flag: 'Bull Flag', bear_flag: 'Inverted Flag', head_shoulders: 'Head & Shoulders',
    inverse_head_shoulders: 'Inverse H&S', double_top: 'Double Top', double_bottom: 'Double Bottom',
    triple_bottom: 'Triple Bottom', cup_handle: 'Cup & Handle',
};

function biasColor(bias) {
    return bias === 'bullish' ? '#00FFC2' : bias === 'bearish' ? '#FF3366' : '#FFB800';
}

async function loadPatterns() {
    try {
        const data = await apiScoped(`/api/pro/patterns/${currentSymbol}?timeframe=${currentTimeframe}`);
        if (data === null) return;
        _patternSeries.forEach(removeSeriesSafe);
        _patternSeries = [];

        const content = document.getElementById('patterns-content');
        if (!data.patterns || data.patterns.length === 0) {
            content.textContent = 'No patterns at the right edge';
            return;
        }
        data.patterns.forEach(p => {
            p.lines.forEach(line => {
                const s = chart.addLineSeries({
                    color: biasColor(p.bias),
                    lineWidth: 1,
                    lineStyle: line.role === 'level' ? LightweightCharts.LineStyle.Dotted : LightweightCharts.LineStyle.Solid,
                    priceLineVisible: false,
                    lastValueVisible: false,
                    crosshairMarkerVisible: false,
                });
                s.setData([{ time: line.t0, value: line.p0 }, { time: line.t1, value: line.p1 }]);
                _patternSeries.push(s);
            });
        });
        content.innerHTML = data.patterns.map(p => `
            <div class="pattern-row">
                <span class="pattern-name" style="color:${biasColor(p.bias)}">${PATTERN_LABELS[p.name] || p.name}</span>
                <span class="pattern-bias">${p.bias}</span>
                <span class="pattern-conf">geom ${(p.confidence * 100).toFixed(0)}%</span>
            </div>`).join('');
    } catch (e) { /* ignore */ }
}

// --- Next-3-candles forecast card ---
let _forecastInFlight = null;

let _projectionSeries = null;

function drawProjection(data) {
    // Dashed path from the last closed candle through the next 3: direction
    // per horizon from the model, magnitude = the symbol's median absolute
    // per-candle move on this timeframe (same honest convention as the
    // price calls: the models predict direction, not distance).
    removeSeriesSafe(_projectionSeries); _projectionSeries = null;
    if (!bars.length || !data.horizons || data.horizons.length === 0) return;
    const moves = [];
    for (let i = 1; i < bars.length; i++) moves.push(Math.abs(bars[i].close / bars[i - 1].close - 1));
    moves.sort((a, b) => a - b);
    const typicalMove = moves.length ? moves[Math.floor(moves.length / 2)] : 0;
    const tfSec = TF_SECONDS[currentTimeframe] || 3600;
    const last = bars[bars.length - 1];
    const points = [{ time: last.time, value: last.close }];
    let price = last.close;
    data.horizons.forEach((h, i) => {
        price = price * (1 + (h.probability_up >= 0.5 ? typicalMove : -typicalMove));
        points.push({ time: last.time + (i + 1) * tfSec, value: price });
    });
    _projectionSeries = chart.addLineSeries({
        color: '#FFB800',
        lineWidth: 2,
        lineStyle: LightweightCharts.LineStyle.Dashed,
        priceLineVisible: false,
        lastValueVisible: true,
        title: 'AI path',
        crosshairMarkerVisible: false,
    });
    _projectionSeries.setData(points);
}

async function loadForecast() {
    const requested = `${currentSymbol}|${currentTimeframe}`;
    // Skip only if a request for this SAME view is already in flight - a
    // different view's slow first-train must not block the new view's card.
    if (_forecastInFlight === requested) return;
    _forecastInFlight = requested;
    try {
        document.getElementById('forecast-tf').textContent = currentTimeframe;
        const data = await apiScoped(`/api/pro/forecast/${currentSymbol}?timeframe=${currentTimeframe}`);
        if (data === null) return; // view changed while training
        drawProjection(data);
        const content = document.getElementById('forecast-content');
        const rows = data.horizons.map(h => {
            const up = h.direction === 'UP';
            const shownProb = up ? h.probability_up : 1 - h.probability_up;
            // The trivial baseline is the holdout's MAJORITY class: a model
            // that always guesses the majority direction scores max(up, down)
            // share, so only a hit rate above that is any edge at all.
            const baseline = Math.max(h.up_share_holdout, 1 - h.up_share_holdout);
            const edge = h.hit_rate - baseline;
            return `
                <div class="forecast-row">
                    <span class="forecast-step">+${h.step}</span>
                    <span class="forecast-arrow ${up ? 'up' : 'down'}">${up ? '▲' : '▼'} ${(shownProb * 100).toFixed(0)}%</span>
                    <span class="forecast-hit ${edge > 0 ? '' : 'no-edge'}">hit ${(h.hit_rate * 100).toFixed(0)}% of last ${h.holdout_bars}</span>
                </div>`;
        }).join('');
        content.innerHTML = rows + `
            <div class="forecast-note">Direction of the next 1–3 closed ${data.timeframe} candles.
            Dim hit rates mean no edge over guessing in the recent window.</div>`;
    } catch (e) {
        const content = document.getElementById('forecast-content');
        if (requested === `${currentSymbol}|${currentTimeframe}`) content.textContent = 'Forecast unavailable';
    } finally {
        if (_forecastInFlight === requested) _forecastInFlight = null;
    }
}

// --- Prediction markers on the chart ---
async function loadDecisionMarkers() {
    try {
        const data = await apiScoped(`/api/pro/decisions/${currentSymbol}?limit=200`);
        if (data === null || bars.length === 0) return;
        const tf = TF_SECONDS[currentTimeframe] || 3600;
        const firstTime = bars[0].time;
        const markers = data.decisions
            .filter(d => d.signal === 'BUY' || d.signal === 'SELL')
            .map(d => ({
                // bucket the decision's timestamp onto the candle it fell in
                time: Math.floor(Date.parse(d.timestamp) / 1000 / tf) * tf,
                position: d.signal === 'BUY' ? 'belowBar' : 'aboveBar',
                color: d.signal === 'BUY' ? '#00FFC2' : '#FF3366',
                shape: d.signal === 'BUY' ? 'arrowUp' : 'arrowDown',
                text: `${d.signal}${d.confidence ? ' ' + (d.confidence * 100).toFixed(0) + '%' : ''}`,
            }))
            .filter(m => m.time >= firstTime)
            .sort((a, b) => a.time - b.time);
        candleSeries.setMarkers(markers);
    } catch (e) { /* ignore */ }
}

// --- AI Performance: measured track record of the AI's price calls ---
// Draws the cumulative-accuracy path as an inline SVG sparkline (segments
// colored by which engine made each call), the 50% coin-flip reference, and
// headline hit rate / cumulative edge / count. All real, self-scoring data.
function aiPerfSparkline(path) {
    const W = 260, H = 56, pad = 3;
    if (!path || path.length < 2) {
        return `<svg class="aiperf-spark" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none"></svg>`;
    }
    const n = path.length;
    const x = i => pad + (i / (n - 1)) * (W - 2 * pad);
    const y = a => pad + (1 - a) * (H - 2 * pad);   // accuracy 0..1 -> bottom..top
    const midY = y(0.5);
    // Color each segment by the engine that produced the newer of its points.
    let segs = '';
    for (let i = 1; i < n; i++) {
        const col = path[i].champion_backed ? '#00FFC2' : '#FFB800';
        segs += `<line x1="${x(i - 1).toFixed(1)}" y1="${y(path[i - 1].accuracy).toFixed(1)}" `
              + `x2="${x(i).toFixed(1)}" y2="${y(path[i].accuracy).toFixed(1)}" stroke="${col}" stroke-width="1.6"/>`;
    }
    const lastA = path[n - 1].accuracy;
    return `<svg class="aiperf-spark" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">
        <line x1="${pad}" y1="${midY.toFixed(1)}" x2="${W - pad}" y2="${midY.toFixed(1)}"
              stroke="rgba(255,255,255,0.25)" stroke-width="1" stroke-dasharray="3 3"/>
        ${segs}
        <circle cx="${x(n - 1).toFixed(1)}" cy="${y(lastA).toFixed(1)}" r="2.4"
                fill="${path[n - 1].champion_backed ? '#00FFC2' : '#FFB800'}"/>
    </svg>`;
}

function _pct(v) { return v === null || v === undefined ? '—' : `${(v * 100).toFixed(0)}%`; }

async function loadAiPerformance() {
    const el = document.getElementById('ai-perf-content');
    if (!el) return;
    try {
        const d = await apiScoped(`/api/pro/ai-performance/${currentSymbol}`);
        if (d === null) return;  // request superseded by a symbol switch
        if (!d.n) {
            el.innerHTML = '<div class="call-sub dim">No settled calls yet — the track record builds as calls settle.</div>';
            return;
        }
        const edge = d.cum_edge_pct;
        const edgeCls = edge > 0.01 ? 'pos' : edge < -0.01 ? 'neg' : 'flat';
        const hitCls = d.hit_rate >= 0.55 ? 'pos' : d.hit_rate <= 0.45 ? 'neg' : 'flat';
        const champ = d.champion || {}, heur = d.heuristic || {};
        el.innerHTML = `
            ${aiPerfSparkline(d.path)}
            <div class="aiperf-metrics">
                <div class="aiperf-metric"><div class="m-val ${hitCls}">${_pct(d.hit_rate)}</div><div class="m-lbl">hit rate</div></div>
                <div class="aiperf-metric"><div class="m-val ${edgeCls}">${edge >= 0 ? '+' : ''}${edge.toFixed(2)}%</div><div class="m-lbl">cum. edge</div></div>
                <div class="aiperf-metric"><div class="m-val flat">${d.n}</div><div class="m-lbl">decisions</div></div>
            </div>
            <div class="aiperf-split">
                <div class="sp champion">🏆 Champion <b>${_pct(champ.hit_rate)}</b><span class="call-sub dim">${champ.n || 0} calls</span></div>
                <div class="sp">📐 Heuristic <b>${_pct(heur.hit_rate)}</b><span class="call-sub dim">${heur.n || 0} calls</span></div>
            </div>`;
    } catch (e) {
        el.textContent = 'AI performance unavailable';
    }
}

// --- Top bar / status ---
async function loadStatusBadge() {
    // The LIVE/TESTNET badge is the "am I looking at real money?" indicator
    // - it must track server restarts that flip mode, not just page load.
    try { renderStatus(await api('/api/status')); } catch (e) { /* keep last known */ }
}

function renderStatus(status) {
    document.getElementById('mode-badge').textContent = status.mode.toUpperCase();
    document.getElementById('live-dot').style.color = status.is_live ? '#FF3366' : '#00FFC2';
}

function renderSymbolList(symbols) {
    const select = document.getElementById('symbol-select');
    select.innerHTML = '';
    symbols.forEach(s => {
        const opt = document.createElement('option');
        opt.value = s;
        opt.textContent = s;
        if (s === currentSymbol) opt.selected = true;
        select.appendChild(opt);
    });
    if (symbols.length > 0 && !symbols.includes(currentSymbol)) {
        currentSymbol = symbols[0];
        select.value = currentSymbol;
    }
}

async function renderTimeframeTabs() {
    let timeframes;
    try {
        timeframes = await api('/api/timeframes');
    } catch (e) {
        timeframes = Object.keys(TF_SECONDS); // backend hiccup: same list, hardcoded
    }
    const wrap = document.getElementById('timeframe-tabs');
    wrap.innerHTML = '';
    timeframes.forEach(tf => {
        const btn = document.createElement('button');
        btn.textContent = tf;
        btn.dataset.tf = tf;
        if (tf === currentTimeframe) btn.classList.add('active');
        btn.addEventListener('click', async () => {
            wrap.querySelectorAll('button').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            currentTimeframe = tf;
            _viewEpoch++;
            bars = []; // stop live klines painting onto the old timeframe's series
            document.getElementById('forecast-content').textContent = 'Training model…';
            try { await loadChart(); } catch (e) { console.warn('Chart load error', e); }
            await Promise.all([loadDecisionMarkers(), loadPatterns()]);
            startLiveCandles();
            loadForecast();
        });
        wrap.appendChild(btn);
    });
}

async function loadHealth() {
    const health = await api('/api/health');
    document.getElementById('health-ring').style.color = health.score >= 90 ? '#00FFC2' : '#FFB800';
    document.getElementById('uptime').textContent = `Uptime: ${formatDuration(health.uptime_seconds)}`;
    document.getElementById('engine-status').className = health.score >= 80 ? 'status-green' : 'status-red';
    document.getElementById('learning-status').className = health.recent_error_rate === 0 ? 'status-green' : 'status-red';
    document.getElementById('risk-status').className = health.circuit_breaker_tripped ? 'status-red' : 'status-green';
    document.getElementById('exchange-status').className = 'status-green';
}

// --- Cards ---
function signalClass(sig) {
    return sig === 'BUY' ? 'buy' : sig === 'SELL' ? 'sell' : 'hold';
}

function signalArrow(sig) {
    return sig === 'BUY' ? '↗' : sig === 'SELL' ? '↘' : '→';
}

// Semicircular gauge (like the mockup). `frac` 0..1 fills the arc; color
// keys off the signal. Returns an inline SVG string.
function confidenceGauge(frac, sig) {
    const r = 52, cx = 60, cy = 60;
    const clamped = Math.max(0, Math.min(1, frac || 0));
    // Semicircle from 180deg (left) to 0deg (right).
    const a0 = Math.PI, a1 = Math.PI * (1 - clamped);
    const x0 = cx + r * Math.cos(a0), y0 = cy - r * Math.sin(a0);
    const x1 = cx + r * Math.cos(a1), y1 = cy - r * Math.sin(a1);
    const xEnd = cx + r, yEnd = cy;
    const color = sig === 'BUY' ? '#00FFC2' : sig === 'SELL' ? '#FF3366' : '#FFB800';
    const large = clamped > 0.5 ? 1 : 0;
    return `
      <svg viewBox="0 0 120 74" class="gauge">
        <path d="M ${cx - r} ${cy} A ${r} ${r} 0 0 1 ${xEnd} ${yEnd}" fill="none" stroke="rgba(255,255,255,0.10)" stroke-width="9" stroke-linecap="round"/>
        <path d="M ${x0.toFixed(1)} ${y0.toFixed(1)} A ${r} ${r} 0 ${large} 1 ${x1.toFixed(1)} ${y1.toFixed(1)}" fill="none" stroke="${color}" stroke-width="9" stroke-linecap="round"/>
        <text x="${cx}" y="${cy - 12}" text-anchor="middle" class="gauge-val" fill="${color}">${(clamped * 100).toFixed(0)}%</text>
        <text x="${cx}" y="${cy + 2}" text-anchor="middle" class="gauge-lbl">confidence</text>
      </svg>`;
}

async function loadPrediction() {
    try {
        const data = await apiScoped(`/api/pro/prediction/${currentSymbol}`);
        if (data === null) return;
        const content = document.getElementById('prediction-content');
        const sig = data.signal || 'HOLD';
        // Confidence gauge prefers the model's ensemble confidence; falls
        // back to how far probability_up sits from 0.5 (its conviction).
        const conv = data.confidence != null ? data.confidence
                   : data.probability_up != null ? Math.abs(data.probability_up - 0.5) * 2 : null;
        const pUp = data.probability_up;
        content.innerHTML = `
            <div class="pred-head">
                <div class="pred-signal ${signalClass(sig)}">${signalArrow(sig)} ${sig}</div>
                ${conv != null ? confidenceGauge(conv, sig) : '<div class="gauge-none">no live model<br>for this regime</div>'}
            </div>
            <div class="pred-rows">
                <div class="pred-row"><span>Probability Up</span><span class="${pUp != null && pUp >= 0.5 ? 'pos' : 'neg'}">${pUp != null ? (pUp * 100).toFixed(1) + '%' : '—'}</span></div>
                <div class="pred-row"><span>Probability Down</span><span>${pUp != null ? ((1 - pUp) * 100).toFixed(1) + '%' : '—'}</span></div>
                <div class="pred-row"><span>Regime</span><span>${data.regime || '—'}</span></div>
                <div class="pred-row"><span>Champion</span><span>${data.champion || 'none'}</span></div>
            </div>`;
        document.getElementById('champion-info').textContent = data.champion ? `♛ ${data.champion}` : '—';
    } catch (e) { /* ignore */ }
}

// --- Model Tournament ---
async function loadTournament() {
    try {
        const d = await apiScoped(`/api/pro/tournament/${currentSymbol}`);
        if (d === null) return;
        const content = document.getElementById('tournament-content');
        if (!d.available) {
            content.textContent = 'No tournament run recorded for this symbol yet';
            return;
        }
        const bar = d.bar;
        const regimes = d.regimes.map(r => {
            const m = r.metrics;
            const metricStr = m
                ? `Sharpe ${m.median_sharpe.toFixed(2)} · ${(m.pct_folds_profitable * 100).toFixed(0)}% folds+ · ${m.num_folds} folds`
                : '—';
            const stab = r.stability != null ? `${(r.stability * 100).toFixed(0)}% stable` : `${r.runs} run${r.runs === 1 ? '' : 's'}`;
            return `
                <div class="trn-regime">
                    <div class="trn-regime-head">
                        <span class="trn-regime-name">${r.regime.replace('_', ' ')}</span>
                        <span class="trn-champ ${r.champion ? 'has' : 'none'}">${r.champion || 'no champion'}</span>
                    </div>
                    <div class="trn-metrics">${metricStr} · ${stab}</div>
                    ${r.reason ? `<div class="trn-reason">${r.reason}</div>` : ''}
                </div>`;
        }).join('');

        const lb = d.candidates.slice(0, 8).map(c => {
            const s = c.best_sharpe != null ? c.best_sharpe.toFixed(2) : '—';
            const cleared = c.best_sharpe != null && c.best_sharpe >= bar.min_sharpe;
            return `<div class="trn-lb-row">
                <span class="trn-lb-name">${c.name}</span>
                <span class="trn-lb-sharpe ${cleared ? 'pass' : ''}">${s}</span>
            </div>`;
        }).join('');

        content.innerHTML = `
            ${regimes}
            <div class="trn-lb-title">Candidate voting — best Sharpe (bar ≥ ${bar.min_sharpe})</div>
            ${lb}`;
    } catch (e) { /* ignore */ }
}

// --- Decision Pipeline ---
const PIPELINE_LABELS = {
    market_data: 'Market Data', market_interpreter: 'Market Interpreter',
    feature_extraction: 'Feature Extraction', model_voting: 'Model Voting', risk_check: 'Risk Check',
};
const STATE_ICON = { ok: '✓', pending: '◦', blocked: '✕' };

async function loadPipeline() {
    try {
        const d = await apiScoped(`/api/pro/pipeline/${currentSymbol}`);
        if (d === null) return;
        const content = document.getElementById('pipeline-content');
        const rows = d.stages.map(s => `
            <div class="pipe-row">
                <span class="pipe-icon ${s.state}">${STATE_ICON[s.state] || '◦'}</span>
                <span class="pipe-stage">${PIPELINE_LABELS[s.stage] || s.stage}</span>
                <span class="pipe-detail">${s.detail}</span>
            </div>`).join('');
        const decCls = d.decision === 'BUY' ? 'buy' : d.decision === 'SELL' ? 'sell' : 'hold';
        const decRow = `
            <div class="pipe-row pipe-decision">
                <span class="pipe-icon ${decCls}">→</span>
                <span class="pipe-stage">Decision</span>
                <span class="pipe-detail pred-${decCls === 'hold' ? '' : decCls}"><strong>${d.decision}</strong></span>
            </div>
            <div class="pipe-row">
                <span class="pipe-icon">▸</span>
                <span class="pipe-stage">Execution</span>
                <span class="pipe-detail">${d.execution}</span>
            </div>`;
        const reason = d.hold_reason ? `<div class="pipe-reason">${d.hold_reason}</div>` : '';
        const tfNote = `<div class="pipe-tf">engine decision · ${d.engine_timeframe} timeframe</div>`;
        content.innerHTML = rows + decRow + reason + tfNote;
    } catch (e) { /* ignore */ }
}

// --- Market Interpreter ---
function dnaBar(label, value) {
    // value in [-1,1] for directional, [0,1] for magnitude; render a
    // center-anchored bar so direction reads at a glance.
    const pct = Math.round(Math.abs(value) * 50);
    const pos = value >= 0;
    const color = label === 'volatility' || label === 'liquidity'
        ? '#FFB800' : (pos ? '#00FFC2' : '#FF3366');
    const fill = (label === 'volatility' || label === 'liquidity')
        ? `<div class="dna-fill" style="left:50%;width:${Math.round(value*50)}%;background:${color}"></div>`
        : `<div class="dna-fill" style="${pos ? 'left:50%' : 'right:50%'};width:${pct}%;background:${color}"></div>`;
    return `<div class="dna-row"><span class="dna-label">${label}</span>
        <span class="dna-track"><span class="dna-center"></span>${fill}</span></div>`;
}

function kv(label, value) {
    return `<div class="iv-row"><span class="iv-k">${label}</span><span class="iv-v">${value}</span></div>`;
}
function fmtDur(secs) {
    const h = Math.floor(secs / 3600), m = Math.floor((secs % 3600) / 60);
    return h > 0 ? `${h}h ${m}m` : `${m}m`;
}
function subPanel(title, bodyHtml) {
    return `<div class="iv-sub"><div class="iv-title">${title}</div>${bodyHtml}</div>`;
}

async function loadInterpreter() {
    try {
        const d = await apiScoped(`/api/pro/interpreter/${currentSymbol}?timeframe=${currentTimeframe}`);
        if (d === null) return;
        const content = document.getElementById('interpreter-content');
        if (!d.available) { content.textContent = 'Not enough history to read this timeframe yet'; return; }

        const rb = d.regime_block, tr = d.trend, mo = d.momentum, pr = d.pressure,
              st = d.structure, vo = d.volatility, li = d.liquidity, cl = d.clarity,
              cf = d.interpreter_confidence;

        // Market summary + clarity headline. The reading is on the VIEWED
        // timeframe, which can legitimately differ from the engine's 1h
        // decision - label it so the two are never read as contradictory.
        const clarityColor = cl.value >= 0.66 ? '#00FFC2' : cl.value >= 0.33 ? '#FFB800' : '#FF3366';
        let html = `<div class="interp-tf">reading ${d.timeframe} · live market</div>`;
        html += `<div class="interp-summary">${d.summary}</div>`;
        html += `<div class="interp-clarity-head"><span>Market Clarity</span>
                 <span style="color:${clarityColor}"><strong>${(cl.value*100).toFixed(0)}%</strong></span></div>
                 <div class="iv-meaning">${cl.meaning}</div>`;

        // Regime
        html += subPanel('Regime',
            kv('Current', `<strong style="text-transform:capitalize">${rb.regime.replace('_',' ')}</strong>`) +
            kv('Confidence', `${rb.confidence_pct}%`) +
            kv('Time in regime', fmtDur(rb.time_in_regime_seconds)) +
            (rb.previous ? kv('Previous', rb.previous.replace('_',' ')) : '') +
            kv('Transition', rb.transition));

        // Trend
        html += subPanel('Trend',
            kv('Direction', `<span class="${tr.value>=0?'pos':'neg'}">${tr.direction}</span>`) +
            kv('Strength', `${tr.strength_pct}%`) +
            kv('Slope', tr.slope) +
            kv('Higher TF ('+tr.higher_tf.timeframe+')', tr.higher_tf.state));

        // Momentum
        html += subPanel('Momentum',
            kv('RSI', mo.rsi.toFixed(0)) +
            kv('MACD', mo.macd) +
            kv('Acceleration', mo.acceleration) +
            `<div class="iv-note">${mo.interpretation}</div>`);

        // Pressure (buyers vs sellers bar)
        html += subPanel('Pressure',
            `<div class="pr-bar"><span class="pr-buy" style="width:${pr.buyers_pct}%">${pr.buyers_pct}%</span>` +
            `<span class="pr-sell" style="width:${pr.sellers_pct}%">${pr.sellers_pct}%</span></div>` +
            kv('Dominance', `<span class="${pr.dominance==='buyers'?'pos':pr.dominance==='sellers'?'neg':''}">${pr.dominance}</span>`) +
            kv('Pressure trend', pr.trend));

        // Structure
        html += subPanel('Structure',
            st.pattern
              ? kv('Pattern', `<span style="text-transform:capitalize">${st.pattern.name.replace(/_/g,' ')}</span>`) +
                kv('Confidence', `${st.pattern.confidence_pct}%`) +
                kv('Breakout risk', st.pattern.breakout_risk)
              : `<div class="iv-note">No defined pattern — structure reads ${st.label}.</div>`);

        // Volatility + Liquidity
        html += subPanel('Volatility',
            kv('ATR', `${vo.atr_pct}%`) + kv('Level', vo.label) +
            (vo.vs_recent_pct != null ? kv('vs recent', `${vo.vs_recent_pct>0?'+':''}${vo.vs_recent_pct}%`) : ''));
        html += subPanel('Liquidity',
            kv('Volume', `${li.rel_volume}× avg`) + kv('Level', li.label) +
            `<div class="iv-note">Spread / order-book depth: ${li.orderbook}</div>`);

        // Market DNA + fingerprint
        const dna = Object.entries(d.dna).map(([k, v]) => dnaBar(k, v)).join('');
        const fp = Object.entries(d.fingerprint).map(([k,v]) => `<span class="fp-code">${k}-${v}</span>`).join('');
        html += subPanel('Market DNA', dna + `<div class="fp-row">${fp}</div>`);

        // Why — each factor shows its OWN direction; the header states the
        // net lean (which can honestly be "mixed").
        const arrow = dir => dir === 'up' ? '<span class="pos">↑</span>'
                          : dir === 'down' ? '<span class="neg">↓</span>' : '<span class="neu">•</span>';
        const netCls = n => n === 'up' ? 'pos' : n === 'down' ? 'neg' : 'neu';
        const whyBlock = (title, w) => `<div class="why-group">
                <div class="why-head">${title} — <span class="${netCls(w.net)}">${w.net}</span></div>` +
            w.factors.map(f => `<div class="why-item">${arrow(f.dir)} ${f.text}</div>`).join('') + `</div>`;
        html += subPanel('Why?', whyBlock('Trend', d.why.trend) + whyBlock('Pressure', d.why.pressure));

        // Watch
        html += subPanel('Watch', d.watch.map(w => `<div class="watch-item">▸ ${w}</div>`).join(''));

        // Interpreter confidence footer
        html += `<div class="interp-conf">Interpreter confidence: <strong>${(cf.value*100).toFixed(0)}%</strong> — ${cf.reason}</div>`;

        content.innerHTML = html;
    } catch (e) { /* ignore */ }
}

function fmtAge(iso) {
    if (!iso) return '—';
    const secs = (Date.now() - Date.parse(iso)) / 1000;
    if (secs < 3600) return `${Math.floor(secs / 60)}m ago`;
    if (secs < 86400) return `${Math.floor(secs / 3600)}h ago`;
    return `${Math.floor(secs / 86400)}d ago`;
}

async function loadModelInfo() {
    try {
        const d = await apiScoped(`/api/pro/model-info/${currentSymbol}`);
        if (d === null) return;
        const content = document.getElementById('model-info-content');
        const regimeCell = d.regime
            ? `${d.regime.replace('_', ' ')}${d.regime_stale ? ` <span class="stale-tag">stale · ${fmtAge(d.decision_at)}</span>` : ''}`
            : '—';
        const rows = [
            ['Champion Model', d.champion || 'none promoted'],
            ['Engine Timeframe', d.timeframe],
            ['Current Regime', regimeCell],
            ['Buy / Sell Threshold', `${d.buy_threshold} / ${d.sell_threshold}`],
            ['Features Used', d.feature_count || '—'],
            ['Last Retrain', `${fmtAge(d.last_retrain_at)}`],
            ['Last Promotion', d.last_promotion_at ? fmtAge(d.last_promotion_at) : 'never'],
            ['Retrain Every', `${d.retrain_interval_hours}h`],
        ];
        content.innerHTML = rows.map(([k, v]) =>
            `<div class="mi-row"><span>${k}</span><span class="mi-val">${v}</span></div>`
        ).join('');
    } catch (e) { /* ignore */ }
}

function money(v) {
    if (v == null) return '—';
    return v >= 1000 ? `$${v.toLocaleString(undefined, {maximumFractionDigits: 0})}`
         : v >= 1 ? `$${v.toFixed(2)}` : `$${v.toFixed(4)}`;
}

// Capital polls the real exchange (rate-limited): refresh it on its own
// slower cadence, not every 10s card refresh.
let _capitalTimer = null;
async function loadCapital() {
    try {
        const d = await api('/api/pro/capital');
        const content = document.getElementById('capital-content');
        const tradeable = d.tradeable_quote;
        const locked = d.earn_locked_value;
        const reserved = d.reserved_quote;
        const spot = d.other_spot_value;

        // Honest verdict about whether the engine can actually trade.
        let verdict, vcls;
        if (tradeable >= 10) { verdict = 'Sufficient free balance to trade.'; vcls = 'ok'; }
        else if (tradeable + spot >= 10) { verdict = 'Free quote balance is low; most funds are in non-quote spot assets.'; vcls = 'warn'; }
        else { verdict = 'Below typical exchange minimums — the engine cannot open new positions.'; vcls = 'bad'; }

        let rows = `
            <div class="cap-main ${vcls}">${money(tradeable)} <span class="cap-lbl">free ${d.quote_currency} · tradeable</span></div>
            <div class="cap-row"><span>Reserved (open orders)</span><span>${money(reserved)}</span></div>
            <div class="cap-row"><span>Locked in Earn</span><span>${money(locked)}</span></div>
            <div class="cap-row"><span>Other spot assets</span><span>${money(spot)}</span></div>`;
        if (d.earn_holdings && d.earn_holdings.length) {
            rows += `<div class="cap-sub">Earn-locked (not tradeable): ` +
                d.earn_holdings.map(h => `${h.asset} ${money(h.value)}`).join(' · ') + `</div>`;
        }
        rows += `<div class="cap-verdict ${vcls}">${verdict}</div>`;
        content.innerHTML = rows;
    } catch (e) {
        document.getElementById('capital-content').textContent = 'Balance unavailable';
    }
}

async function loadPosition() {
    try {
        const data = await apiScoped(`/api/pro/position/${currentSymbol}`);
        if (data === null) return;
        const content = document.getElementById('position-content');
        if (!data.open) { content.textContent = 'No open position'; return; }
        const side = data.quantity >= 0 ? 'LONG' : 'SHORT';
        content.innerHTML = `
            <div><strong class="${data.quantity >= 0 ? 'prediction-buy' : 'prediction-sell'}">${side}</strong> ${Math.abs(data.quantity)}</div>
            <div>Mark: $${formatPrice(data.price)}</div>
            ${data.unexplained_quantity ? `<div class="warn">Unexplained qty: ${data.unexplained_quantity}</div>` : ''}
        `;
    } catch (e) { /* ignore */ }
}

async function loadFeatures() {
    try {
        const data = await apiScoped(`/api/pro/features/${currentSymbol}`);
        if (data === null) return;
        const content = document.getElementById('features-content');
        if (!data.features || data.features.length === 0) { content.textContent = 'No features yet'; return; }
        const list = document.createElement('ul');
        list.className = 'feature-list';
        data.features.forEach(f => {
            const li = document.createElement('li');
            li.innerHTML = `<span>${f.name}</span><span>${f.importance.toFixed(3)}</span>`;
            list.appendChild(li);
        });
        content.innerHTML = '';
        content.appendChild(list);
    } catch (e) { /* ignore */ }
}

async function loadTrades() {
    try {
        const data = await apiScoped(`/api/pro/trades/${currentSymbol}?limit=20`);
        if (data === null) return;
        const content = document.getElementById('trades-content');
        if (!data.trades || data.trades.length === 0) { content.textContent = 'No trades yet'; return; }
        const table = document.createElement('table');
        table.className = 'trades-table';
        table.innerHTML = '<tr><th>Time</th><th>Side</th><th>Price</th><th>Size</th><th>PnL</th></tr>';
        data.trades.forEach(t => {
            const row = table.insertRow();
            row.innerHTML = `
                <td>${new Date(t.timestamp).toLocaleTimeString()}</td>
                <td>${t.side}</td>
                <td>${t.price}</td>
                <td>${t.quantity}</td>
                <td class="${t.pnl >= 0 ? 'positive' : 'negative'}">${t.pnl ? t.pnl.toFixed(2) : '—'}</td>
            `;
        });
        content.innerHTML = '';
        content.appendChild(table);
    } catch (e) { /* ignore */ }
}

async function loadCandidates() {
    try {
        const data = await apiScoped(`/api/pro/candidates/${currentSymbol}`);
        if (data === null) return;
        const content = document.getElementById('candidates-content');
        if (!data.candidates || data.candidates.length === 0) { content.textContent = 'No paper candidates yet'; return; }
        const sorted = [...data.candidates].sort((a, b) => b.total_equity - a.total_equity);
        const table = document.createElement('table');
        table.className = 'trades-table';
        table.innerHTML = '<tr><th>Candidate</th><th>Equity</th><th>Trades</th></tr>';
        sorted.forEach(c => {
            const row = table.insertRow();
            row.innerHTML = `<td>${c.candidate}</td><td>${c.total_equity.toFixed(2)}</td><td>${c.num_trades}</td>`;
        });
        content.innerHTML = '';
        content.appendChild(table);
    } catch (e) { /* ignore */ }
}

function formatDuration(seconds) {
    const h = Math.floor(seconds / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    return `${h}h ${m}m`;
}

function startPolling() {
    pollTimer = setInterval(refreshCards, POLL_INTERVAL);
}
