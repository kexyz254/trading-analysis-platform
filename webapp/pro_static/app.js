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
    initWorkspaceTabs();
    if (!chart) setupChart();
    let status;
    try {
        status = await api('/api/status');
    } catch (e) {
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
    _lastKlineAt = Date.now();
    startLiveCandles();
    startPolling();

    document.getElementById('symbol-select').addEventListener('change', async (e) => {
        currentSymbol = e.target.value;
        _viewEpoch++;
        bars = [];
        await refreshAll();
        startLiveCandles();
    });
}

// --- Research workspace navigation ---
let _workspaceTabsInitialized = false;

function setWorkspace(name, updateHash = true) {
    const allowed = new Set(['overview', 'price-calls', 'tournament', 'intelligence', 'research', 'execution']);
    const selected = allowed.has(name) ? name : 'overview';
    document.querySelectorAll('[data-workspace-target]').forEach(control => {
        const active = control.dataset.workspaceTarget === selected;
        control.classList.toggle('active', active);
        if (control.getAttribute('role') === 'tab') {
            control.setAttribute('aria-selected', active ? 'true' : 'false');
        }
    });
    document.querySelectorAll('.workspace-card').forEach(card => {
        card.hidden = card.dataset.workspace !== selected;
    });
    if (updateHash && window.history?.replaceState) {
        window.history.replaceState(null, '', `#${selected}`);
    }
    window.dispatchEvent(new CustomEvent('ats:workspace', { detail: { name: selected } }));
}

function initWorkspaceTabs() {
    if (_workspaceTabsInitialized) return;
    _workspaceTabsInitialized = true;
    document.querySelectorAll('[data-workspace-target]').forEach(control => {
        control.addEventListener('click', event => {
            event.preventDefault();
            setWorkspace(control.dataset.workspaceTarget);
        });
    });
    setWorkspace(window.location.hash.slice(1), false);
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

let _viewEpoch = 0;
async function apiScoped(url) {
    const epoch = _viewEpoch;
    const data = await api(url);
    return epoch === _viewEpoch ? data : null;
}

let _lastKlineAt = 0;
let _lastTickerAt = 0;
let _strategyMode = 'unknown';
let _backendMarketStream = null;

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
            loadQuantTournament(),
            loadModelInfo(),
            loadPatterns(),
            loadStatusBadge(),
            loadBankroll(),
        ]);
    } catch (e) { console.warn('Refresh error', e); }
    if (_lastKlineAt && Date.now() - _lastKlineAt > 45000) {
        try { await loadChart(); } catch (e) { /* next poll retries */ }
    }
    await loadForecast();
    await loadLearningHealth();
    await loadMarketInterpreter();
}

// --- Live candle streaming (ws.js) ---
function binancePair(symbol) {
    return symbol.replace('/', '').toUpperCase();
}

function startLiveCandles() {
    setKlineSubscription(currentSymbol, currentTimeframe, onKline);
    setAggTradeSubscription(currentSymbol, onCallTrade);
}

function onKline(k) {
    if (k.interval !== currentTimeframe || k.symbol !== binancePair(currentSymbol)) return;
    _lastKlineAt = Date.now();
    if (!bars.length) return;
    const bar = { time: k.time, open: k.open, high: k.high, low: k.low, close: k.close, volume: k.volume };
    const last = bars[bars.length - 1];
    if (last && bar.time === last.time) {
        bars[bars.length - 1] = bar;
    } else if (!last || bar.time > last.time) {
        bars.push(bar);
        if (bars.length > HISTORY_LIMIT) bars.shift();
    } else {
        return;
    }
    candleSeries.update(bar);
    volumeSeries.update(volPoint(bar));
    recomputeIndicators();
    updatePriceHeader();
}

function onTicker(t) {
    if (t.symbol !== binancePair(currentSymbol)) return;
    _lastTickerAt = Date.now();
    const el = document.getElementById('price-change');
    el.textContent = `${t.changePct >= 0 ? '+' : ''}${t.changePct.toFixed(2)}% (24h)`;
    el.style.color = t.changePct >= 0 ? '#00FFC2' : '#FF3366';
    renderMarketEvidence();
}

// --- Chart data ---
async function loadChart() {
    removeSeriesSafe(_projectionSeries); _projectionSeries = null;
    _patternSeries.forEach(removeSeriesSafe);
    _patternSeries = [];
    const data = await apiScoped(
        `/api/ohlcv?symbol=${encodeURIComponent(currentSymbol)}&timeframe=${currentTimeframe}&limit=${HISTORY_LIMIT}`
    );
    if (data === null) return;
    if (!Array.isArray(data) || data.length === 0) return;
    bars = data.map(b => ({
        time: Math.floor(b.timestamp / 1000),
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
    if (Date.now() - _lastTickerAt > 90000 && bars.length > 1) {
        const prev = bars[bars.length - 2].close;
        const pct = ((last.close - prev) / prev * 100).toFixed(2);
        const el = document.getElementById('price-change');
        el.textContent = `${last.close >= prev ? '+' : ''}${pct}% (bar)`;
        el.style.color = last.close >= prev ? '#00FFC2' : '#FF3366';
    }
}

// --- Paper-only heuristic price-call run ledger ---
let _callPriceLine = null;

function callTimestamp(value) {
    const numeric = Number(value);
    if (!Number.isFinite(numeric)) return null;
    return new Date(numeric < 1e12 ? numeric * 1000 : numeric);
}

function callTime(value) {
    const date = callTimestamp(value);
    if (!date || Number.isNaN(date.getTime())) return '?';
    return date.toLocaleTimeString([], {
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit',
    });
}

function callRunMarkup(call, running) {
    const up = call.direction === 'UP';
    const status = running ? 'RUNNING' : (call.correct ? 'HIT' : 'MISS');
    const endLabel = running ? 'Due' : 'Settled';
    const endValue = running ? call.due_at : call.settled_at;
    const actual = call.actual_price == null
        ? '?'
        : `$${formatPrice(Number(call.actual_price))}`;

    return `
        <div class="call-run ${running ? 'running' : (call.correct ? 'hit' : 'miss')}">
            <div class="call-run-top">
                <span class="call-horizon">${call.horizon_label}</span>
                <span class="call-direction ${up ? 'up' : 'down'}">
                    ${up ? '?' : '?'} ${call.direction}
                </span>
                <span class="call-status">${status}</span>
                ${running
                    ? `<span class="call-countdown" data-due="${Number(call.due_at)}"></span>`
                    : ''}
            </div>
            <div class="call-run-details">
                <span><small>Started</small>${callTime(call.created_at)}</span>
                <span><small>Entry</small>$${formatPrice(Number(call.price_to_beat))}</span>
                <span><small>${endLabel}</small>${callTime(endValue)}</span>
                <span><small>Actual</small>${actual}</span>
            </div>
        </div>`;
}

async function loadPriceCall() {
    try {
        const data = await apiScoped(
            `/api/pro/price-calls/${currentSymbol}?limit=20`
        );
        if (data === null) return;

        const content = document.getElementById('call-content');
        const openCalls = Array.isArray(data.open_call)
            ? data.open_call
            : (data.open_call ? [data.open_call] : []);
        const recent = Array.isArray(data.recent) ? data.recent : [];

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
                title: `${active.horizon_label} heuristic entry`,
            });
        }

        const parts = [
            '<div class="call-model-note">HEURISTIC V1 ? PAPER ONLY ? REAL MARKET PRICES</div>'
        ];

        if (openCalls.length === 0 && recent.length === 0) {
            parts.push(
                '<div class="call-sub">Waiting for the first OHLCV-backed run?</div>'
            );
        } else {
            openCalls
                .sort((a, b) => Number(a.due_at) - Number(b.due_at))
                .forEach(call => parts.push(callRunMarkup(call, true)));

            recent.forEach(call => parts.push(callRunMarkup(call, false)));
        }

        const resolved = Number(data.stats?.total_resolved || 0);
        if (resolved > 0) {
            const winRate = Number(data.stats.win_rate || 0) * 100;
            const avgMiss = Number(data.stats.avg_miss_pct || 0);
            parts.push(`
                <div class="call-score">
                    Resolved ${resolved} ?
                    <strong>${winRate.toFixed(1)}% hit rate</strong> ?
                    ${avgMiss.toFixed(3)}% average miss
                </div>`);
        } else {
            parts.push(
                '<div class="call-sub">Track record begins after the first run settles.</div>'
            );
        }

        content.innerHTML = parts.join('');
        updateCallCountdown();
    } catch (error) {
        console.warn('Price-call refresh failed', error);
    }
}

function updateCallCountdown() {
    document.querySelectorAll('.call-countdown[data-due]').forEach(el => {
        const due = callTimestamp(el.dataset.due);
        if (!due) return;

        const remaining = Math.max(
            0,
            Math.floor((due.getTime() - Date.now()) / 1000)
        );

        if (remaining === 0) {
            el.textContent = 'settling?';
            return;
        }

        const minutes = Math.floor(remaining / 60);
        const seconds = remaining % 60;
        el.textContent = `${minutes}m ${String(seconds).padStart(2, '0')}s`;
    });
}

setInterval(() => updateCallCountdown(), 50);
setInterval(() => renderMarketEvidence(), 1000);

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
    if (_forecastInFlight === requested) return;
    _forecastInFlight = requested;
    try {
        document.getElementById('forecast-tf').textContent = currentTimeframe;
        const data = await apiScoped(`/api/pro/forecast/${currentSymbol}?timeframe=${currentTimeframe}`);
        if (data === null) return;
        drawProjection(data);
        const content = document.getElementById('forecast-content');
        const rows = data.horizons.map(h => {
            const up = h.direction === 'UP';
            const shownProb = up ? h.probability_up : 1 - h.probability_up;
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

// --- Top bar / status ---
async function loadStatusBadge() {
    try { renderStatus(await api('/api/status')); } catch (e) { /* keep last known */ }
}

function renderStatus(status) {
    _strategyMode = String(status.mode || 'unknown').toUpperCase();
    renderMarketEvidence();
}

function updateExchangeEvidence(market) {
    _backendMarketStream = market?.stream || null;
    renderMarketEvidence();
}

function renderMarketEvidence() {
    const stream = _backendMarketStream;
    const lastMessage = Number(stream?.last_message_at_ms);
    const backendAge = Number.isFinite(lastMessage) ? Math.max(0, Date.now() - lastMessage) : null;
    const browserAge = _lastTickerAt ? Math.max(0, Date.now() - _lastTickerAt) : null;
    const freshLimit = Number(stream?.freshness_limit_ms || 15000);
    const backendFresh = Boolean(stream?.connected) && backendAge !== null && backendAge <= freshLimit;
    const browserFresh = browserAge !== null && browserAge <= 15000;
    const live = backendFresh && browserFresh;
    const verifying = stream === null;
    const offline = stream && !stream.connected;
    const state = live ? 'LIVE' : verifying ? 'VERIFYING' : offline ? 'OFFLINE' : 'STALE';
    const color = live ? '#00FFC2' : offline ? '#FF3366' : '#FFB800';
    const statusClass = live ? 'status-green' : offline ? 'status-red' : 'status-yellow';
    const age = backendAge === null ? 'no message' : `${Math.round(backendAge / 1000)}s old`;

    const badge = document.getElementById('mode-badge');
    badge.textContent = `MARKET ${state}`;
    badge.title = `Binance public market data; strategy mode: ${_strategyMode}`;
    badge.dataset.state = state.toLowerCase();

    const dot = document.getElementById('live-dot');
    dot.style.color = color;
    dot.title = `Backend Binance stream: ${state} (${age}); strategy mode: ${_strategyMode}`;

    const exchange = document.getElementById('exchange-status');
    exchange.className = statusClass;
    exchange.textContent = `Binance data: ● ${state}${backendAge === null ? '' : ` (${age})`}`;
    exchange.title = stream?.last_error
        ? `Last backend stream error: ${stream.last_error}`
        : 'Verified using backend aggregate-trade stream freshness and the browser ticker stream.';
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
        timeframes = Object.keys(TF_SECONDS);
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
            bars = [];
            document.getElementById('forecast-content').textContent = 'Training model…';
            try { await loadChart(); } catch (e) { console.warn('Chart load error', e); }
            await Promise.all([loadDecisionMarkers(), loadPatterns()]);
            startLiveCandles();
            await loadForecast();
            await loadLearningHealth();
            await loadMarketInterpreter();
        });
        wrap.appendChild(btn);
    });
}

async function loadHealth() {
    const health = await api('/api/health');
    document.getElementById('health-ring').style.color = health.score >= 90 ? '#00FFC2' : '#FFB800';
    document.getElementById('uptime').textContent = `Engine uptime: ${formatDuration(health.uptime_seconds)}`;
    document.getElementById('engine-status').className = health.score == null
        ? 'status-yellow' : health.score >= 80 ? 'status-green' : 'status-red';
    document.getElementById('risk-status').className = health.circuit_breaker_tripped ? 'status-red' : 'status-green';
    renderMarketEvidence();
}

function learningStatusClass(state) {
    if (state === 'ACTIVE' || state === 'READY') return 'status-green';
    if (state === 'DEGRADED') return 'status-red';
    return 'status-yellow';
}

function fmtEpochMs(value) {
    const number = Number(value);
    if (!Number.isFinite(number) || number <= 0) return '—';
    return fmtAge(new Date(number).toISOString());
}

async function loadLearningHealth() {
    const data = await apiScoped(`/api/pro/learning-health/${currentSymbol}?timeframe=${currentTimeframe}`);
    if (data === null) return;
    const paths = data.paths || {};
    const legacy = paths.legacy_learning || {};
    const forecast = paths.forecast_ml || {};
    const tournament = paths.tournament_ml || {};
    const tournamentHorizons = tournament.horizons || {};
    const tournamentStates = Object.values(tournamentHorizons).map(item => item.state);
    const tournamentState = tournamentStates.includes('DEGRADED')
        ? 'DEGRADED'
        : tournamentStates.includes('READY') ? 'READY'
        : tournamentStates.length ? 'WARMING' : 'NO_EVIDENCE';

    const footer = [
        ['legacy-learning-status', 'Legacy ML', legacy.state],
        ['forecast-learning-status', 'Forecast ML', forecast.state],
        ['tournament-learning-status', 'Tournament ML', tournamentState],
    ];
    footer.forEach(([id, label, state]) => {
        const el = document.getElementById(id);
        el.className = learningStatusClass(state);
        el.textContent = `${label}: ● ${state || 'NO_EVIDENCE'}`;
    });

    const tournamentRows = Object.entries(tournamentHorizons).map(([horizon, item]) => {
        const identity = item.model_fingerprint ? item.model_fingerprint.slice(0, 12) : '—';
        const feedback = Number(item.settled_feedback_count || 0);
        return `<div class="mi-row"><span>Tournament ML ${horizon}</span><span class="mi-val ${learningStatusClass(item.state)}">${item.state} · n=${item.training_samples || 0} · ${identity}</span></div>
            <div class="metric-note">Inference ${fmtEpochMs(item.last_inference_at_ms)} · feedback ${feedback} settled · ${item.decision_state || '—'}</div>`;
    }).join('');
    const content = document.getElementById('learning-health-content');
    content.innerHTML = `
        <div class="mi-row"><span>Legacy LearningAgent</span><span class="mi-val ${learningStatusClass(legacy.state)}">${legacy.state}</span></div>
        <div class="metric-note">Retrain ${fmtEpochMs(legacy.last_training_attempt_ms)} · inference ${fmtEpochMs(legacy.last_inference_ms)} · ${legacy.feature_count || 0} SHAP features</div>
        <div class="mi-row"><span>Next 3 Candles ${forecast.timeframe || currentTimeframe}</span><span class="mi-val ${learningStatusClass(forecast.state)}">${forecast.state}</span></div>
        <div class="metric-note">Train ${fmtEpochMs(forecast.trained_at_ms)} · inference ${fmtEpochMs(forecast.last_inference_at_ms)} · dashboard only</div>
        ${tournamentRows || '<div class="mi-row"><span>Tournament ML</span><span class="mi-val status-yellow">NO EVIDENCE</span></div>'}
        <div class="metric-note">Paths are isolated. Interpreter authority: none.</div>`;
}

function interpreterClass(value) {
    const normalized = String(value || '').toUpperCase();
    if (['BULLISH', 'READY', 'HIGH', 'TRADE_ELIGIBLE'].includes(normalized)) return 'positive';
    if (['BEARISH', 'DEGRADED'].includes(normalized)) return 'negative';
    return 'warn';
}

function interpreterLabel(value) {
    return String(value || '—').replaceAll('_', ' ');
}

function escapeInterpreter(value) {
    return String(value ?? '—')
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;')
        .replaceAll("'", '&#039;');
}

let _interpreterInFlight = null;
async function loadMarketInterpreter() {
    const requested = `${currentSymbol}|${currentTimeframe}`;
    if (_interpreterInFlight === requested) return;
    _interpreterInFlight = requested;
    try {
        document.getElementById('interpreter-tf').textContent = currentTimeframe;
        const data = await apiScoped(`/api/pro/market-interpreter/${currentSymbol}?timeframe=${currentTimeframe}`);
        if (data === null) return;
        const market = data.market_context || {};
        const chart = data.chart_context || {};
        const paths = data.ai_paths || {};
        const legacy = paths.legacy_strategy || {};
        const forecast = paths.forecast_ml || {};
        const tournament = paths.tournament_ml || {};
        const economics = data.economics || {};
        const patterns = (chart.leading_patterns || []).map(item =>
            `<span class="interpreter-chip ${interpreterClass(item.bias)}">${escapeInterpreter(interpreterLabel(item.name))} · ${escapeInterpreter(item.bias)}</span>`
        ).join('');
        const conflicts = (data.conflicts || []).map(item =>
            `<span class="interpreter-chip negative">${escapeInterpreter(interpreterLabel(item))}</span>`
        ).join('');
        const warnings = (data.warnings || []).map(item =>
            `<span class="interpreter-chip warn">${escapeInterpreter(interpreterLabel(item))}</span>`
        ).join('');
        const score = data.alignment_score == null ? '—' : `${Number(data.alignment_score).toFixed(1)}`;
        const content = document.getElementById('market-interpreter-content');
        content.innerHTML = `
            <div class="interpreter-head">
                <div>
                    <span class="interpreter-outlook ${interpreterClass(data.outlook)}">${escapeInterpreter(interpreterLabel(data.outlook))}</span>
                    <span class="interpreter-state">${escapeInterpreter(data.state)} · ${escapeInterpreter(data.conviction)} alignment</span>
                </div>
                <div class="interpreter-score"><strong>${score}</strong><small>alignment / 100<br>not probability</small></div>
            </div>
            <p class="interpreter-summary">${escapeInterpreter(data.summary)}</p>
            <div class="interpreter-grid">
                <section>
                    <small>Market structure</small>
                    <strong class="${interpreterClass(market.regime)}">${escapeInterpreter(interpreterLabel(market.regime))}</strong>
                    <span>${market.regime_confidence == null ? 'No selected-timeframe state' : `${(Number(market.regime_confidence) * 100).toFixed(1)}% regime confidence`}</span>
                    <span>Closed candle ${fmtEpochMs(market.latest_closed_candle_ms)}</span>
                </section>
                <section>
                    <small>Chart geometry</small>
                    <strong>${escapeInterpreter(interpreterLabel(chart.state))}</strong>
                    <span>${Number(chart.bullish || 0)} bullish · ${Number(chart.bearish || 0)} bearish · geometry only</span>
                </section>
                <section>
                    <small>AI paths</small>
                    <strong>${escapeInterpreter(forecast.direction || 'NO EVIDENCE')}</strong>
                    <span>Forecast ${escapeInterpreter(interpreterLabel(forecast.state))} · ${Number(forecast.validated_horizons || 0)}/${Number(forecast.total_horizons || 0)} validated</span>
                    <span>Forecast as of ${fmtEpochMs(forecast.as_of_ms)}</span>
                    <span>Legacy ${escapeInterpreter(legacy.direction)} (${escapeInterpreter(interpreterLabel(legacy.state))})</span>
                    <span>Tournament ${escapeInterpreter(interpreterLabel(tournament.state))} · direction protected</span>
                </section>
                <section>
                    <small>Economic gate</small>
                    <strong class="${interpreterClass(economics.state)}">${escapeInterpreter(interpreterLabel(economics.state))}</strong>
                    <span>${escapeInterpreter(economics.action || 'No matching cycle')} · ${escapeInterpreter(economics.direction || '—')}</span>
                    <span>Simulated execution only</span>
                </section>
            </div>
            ${patterns ? `<div class="interpreter-strip"><small>Right-edge symbols</small>${patterns}</div>` : ''}
            ${conflicts ? `<div class="interpreter-strip"><small>Conflicts</small>${conflicts}</div>` : ''}
            ${warnings ? `<div class="interpreter-strip"><small>Evidence gaps</small>${warnings}</div>` : ''}
            <div class="metric-note">Read only · cannot train · cannot trade · cannot alter risk or promotion.</div>`;
    } catch (error) {
        if (requested === `${currentSymbol}|${currentTimeframe}`) {
            document.getElementById('market-interpreter-content').textContent =
                'Interpreter unavailable — source modules remain isolated and continue running.';
        }
    } finally {
        if (_interpreterInFlight === requested) _interpreterInFlight = null;
    }
}

// --- Cards ---
function signalClass(sig) {
    return sig === 'BUY' ? 'buy' : sig === 'SELL' ? 'sell' : 'hold';
}

function signalArrow(sig) {
    return sig === 'BUY' ? '↗' : sig === 'SELL' ? '↘' : '→';
}

function confidenceGauge(frac, sig) {
    const r = 52, cx = 60, cy = 60;
    const clamped = Math.max(0, Math.min(1, frac || 0));
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
        <text x="${cx}" y="${cy + 2}" text-anchor="middle" class="gauge-lbl">signal score</text>
      </svg>`;
}

async function loadPrediction() {
    try {
        const data = await apiScoped(`/api/pro/prediction/${currentSymbol}`);
        if (data === null) return;
        const content = document.getElementById('prediction-content');
        const sig = data.signal || 'HOLD';
        const conv = data.confidence != null ? data.confidence
                   : data.probability_up != null ? Math.abs(data.probability_up - 0.5) * 2 : null;
        const pUp = data.probability_up;
        content.innerHTML = `
            <div class="pred-head">
                <div class="pred-signal ${signalClass(sig)}">${signalArrow(sig)} ${sig}</div>
                ${conv != null ? confidenceGauge(conv, sig) : '<div class="gauge-none">no live model<br>for this regime</div>'}
            </div>
            <div class="pred-rows">
                <div class="pred-row"><span>Model probability Up</span><span class="${pUp != null && pUp >= 0.5 ? 'pos' : 'neg'}">${pUp != null ? (pUp * 100).toFixed(1) + '%' : '—'}</span></div>
                <div class="pred-row"><span>Model probability Down</span><span>${pUp != null ? ((1 - pUp) * 100).toFixed(1) + '%' : '—'}</span></div>
                <div class="pred-row"><span>Regime</span><span>${data.regime || '—'}</span></div>
                <div class="pred-row"><span>Champion</span><span>${data.champion || 'none promoted'}</span></div>
            </div>
            <div class="metric-note">Signal score and directional probability are separate model outputs.</div>`;
        document.getElementById('champion-info').textContent = data.champion ? `♛ ${data.champion}` : '—';
    } catch (e) { /* ignore */ }
}

function fmtAge(iso) {
    if (!iso) return '—';
    const parsed = Date.parse(iso);
    if (!Number.isFinite(parsed)) return 'invalid timestamp';
    const secs = Math.max(0, (Date.now() - parsed) / 1000);
    if (secs < 60) return `${Math.floor(secs)}s ago`;
    if (secs < 3600) return `${Math.floor(secs / 60)}m ago`;
    if (secs < 86400) return `${Math.floor(secs / 3600)}h ago`;
    return `${Math.floor(secs / 86400)}d ago`;
}

async function loadModelInfo() {
    try {
        const d = await apiScoped(`/api/pro/model-info/${currentSymbol}`);
        if (d === null) return;
        const content = document.getElementById('model-info-content');
        content.classList.remove('warn');
        const rows = [
            ['Champion Model', d.champion || 'none promoted'],
            ['Engine Timeframe', d.timeframe],
            ['Current Regime', d.regime || '—'],
            ['Buy / Sell Threshold', `${d.buy_threshold} / ${d.sell_threshold}`],
            ['Features Used', d.feature_count || '—'],
            ['Signal Generated', fmtAge(d.decision_at)],
            ['Last Retrain', `${fmtAge(d.last_retrain_at)}`],
            ['Last Promotion', d.last_promotion_at ? fmtAge(d.last_promotion_at) : 'never'],
            ['Retrain Every', `${d.retrain_interval_hours}h`],
        ];
        content.innerHTML = rows.map(([k, v]) =>
            `<div class="mi-row"><span>${k}</span><span class="mi-val">${v}</span></div>`
        ).join('') + (d.warnings?.length
            ? `<div class="metric-note warn">Partial data: ${d.warnings.join('; ')}</div>` : '');
    } catch (e) {
        const content = document.getElementById('model-info-content');
        content.textContent = 'Model information unavailable — check the backend log.';
        content.classList.add('warn');
    }
}

async function loadPosition() {
    try {
        const data = await apiScoped(`/api/pro/position/${currentSymbol}`);
        if (data === null) return;
        const content = document.getElementById('position-content');
        if (!data.open) { content.textContent = 'No open position'; return; }
        const side = data.quantity >= 0 ? 'LONG' : 'SHORT';
        const asOfMs = Date.parse(data.as_of);
        const ageMs = Number.isFinite(asOfMs) ? Math.max(0, Date.now() - asOfMs) : null;
        const stale = ageMs === null || ageMs > 30 * 60 * 1000;
        content.innerHTML = `
            <div><strong class="${data.quantity >= 0 ? 'prediction-buy' : 'prediction-sell'}">${side}</strong> ${Math.abs(data.quantity)}</div>
            <div>Mark: $${formatPrice(data.price)}</div>
            <div class="strategy-mark-time ${stale ? 'warn' : ''}">${stale ? 'STALE MARK · ' : 'As of '}${data.as_of ? fmtAge(data.as_of) : 'timestamp unavailable'}</div>
            ${data.unexplained_quantity ? `<div class="warn">Unexplained qty: ${data.unexplained_quantity}</div>` : ''}
        `;
    } catch (e) {
        document.getElementById('position-content').textContent = 'Strategy position unavailable.';
    }
}

async function loadFeatures() {
    try {
        const data = await apiScoped(`/api/pro/features/${currentSymbol}`);
        if (data === null) return;
        const content = document.getElementById('features-content');
        if (!data.features || data.features.length === 0) {
            content.textContent = 'No legacy champion SHAP features yet. Tournament ML telemetry is shown separately.';
            return;
        }
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
        if (!data.candidates || data.candidates.length === 0) { content.textContent = 'No legacy strategy accounts recorded'; return; }
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

// --- Bankroll Simulation Card (NEW) ---
async function loadBankroll() {
    try {
        const d = await api('/api/pro/bankroll');
        renderBankrollCard(d);
    } catch (e) { console.warn('Bankroll error', e); }
}

function renderBankrollCard(data) {
    const el = document.getElementById('bankroll-content');
    if (!el) return;
    let html = '';
    for (const tf of ['5m', '15m']) {
        const s = data[tf];
        if (!s) continue;
        const pnlClass = s['Net PnL $'] >= 0 ? 'positive' : 'negative';
        html += `<div class="bankroll-horizon">
            <strong>${tf} Simulated</strong>
            <div>Bankroll: <span class="${pnlClass}">$${s['Current Bankroll'].toFixed(2)}</span> (${s['Net PnL %'] >= 0 ? '+' : ''}${s['Net PnL %'].toFixed(1)}%)</div>
            <div>Trades: ${s['Total Trades']} | Win: ${s['Winrate %'].toFixed(1)}% | PF: ${s['Profit Factor'].toFixed(2)}</div>
            <div>Max DD: $${s['Max Drawdown $'].toFixed(2)} (${s['Max Drawdown %'].toFixed(1)}%)</div>
            <div>Fees: $${s['Total Fees $'].toFixed(4)} | Slippage: $${s['Total Slippage $'].toFixed(4)}</div>
        </div><hr>`;
    }
    el.innerHTML = html || 'No bankroll data yet.';
}

function formatDuration(seconds) {
    const h = Math.floor(seconds / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    return `${h}h ${m}m`;
}

function startPolling() {
    pollTimer = setInterval(refreshCards, POLL_INTERVAL);
}
