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
            loadBankroll(),
        ]);
    } catch (e) { console.warn('Refresh error', e); }
    if (_lastKlineAt && Date.now() - _lastKlineAt > 45000) {
        try { await loadChart(); } catch (e) { /* next poll retries */ }
    }
    loadForecast();
}

// --- Live candle streaming (ws.js) ---
function binancePair(symbol) {
    return symbol.replace('/', '').toUpperCase();
}

function startLiveCandles() {
    setKlineSubscription(currentSymbol, currentTimeframe, onKline);
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

// --- 5-minute self-scoring price call card ---
let _callPriceLine = null;
let _callDueAt = null;

async function loadPriceCall() {
    try {
        const data = await apiScoped(`/api/pro/price-calls/${currentSymbol}`);
        if (data === null) return;
        const content = document.getElementById('call-content');

        if (_callPriceLine) { candleSeries.removePriceLine(_callPriceLine); _callPriceLine = null; }
        _callDueAt = null;

        const parts = [];
        const call = data.open_call;
        if (call) {
            const up = call.direction === 'UP';
            _callDueAt = Date.parse(call.due_at);
            parts.push(`
                <div class="call-main ${up ? 'up' : 'down'}">
                    ${up ? '▲' : '▼'} ${call.direction} to $${formatPrice(call.projected_price)}
                    <span class="call-countdown" id="call-countdown"></span>
                </div>
                <div class="call-sub">from $${formatPrice(call.price_at_call)} · model ${(call.probability * 100).toFixed(0)}% · typical 5m move ${(call.expected_move_pct * 100).toFixed(2)}%</div>`);
            _callPriceLine = candleSeries.createPriceLine({
                price: call.projected_price,
                color: up ? '#00FFC2' : '#FF3366',
                lineWidth: 1,
                lineStyle: LightweightCharts.LineStyle.Dashed,
                axisLabelVisible: true,
                title: `5m ${call.direction}`,
            });
        } else {
            parts.push('<div class="call-sub">No open call (calls run for configured symbols only)</div>');
        }

        if (data.stats && data.stats.n > 0) {
            const acc = (data.stats.accuracy * 100).toFixed(0);
            const miss = (data.stats.avg_miss_pct * 100).toFixed(2);
            const dots = data.recent.map(r =>
                `<span class="call-dot ${r.correct ? 'hit' : 'miss'}" title="${r.direction} ${r.correct ? 'correct' : 'wrong'}">${r.correct ? '✓' : '✗'}</span>`
            ).join('');
            parts.push(`<div class="call-score">Last ${data.stats.n}: <strong>${acc}%</strong> correct · avg miss ${miss}%</div>`);
            parts.push(`<div class="call-dots">${dots}</div>`);
        } else {
            parts.push('<div class="call-sub">No settled calls yet — score appears after the first few settle.</div>');
        }
        content.innerHTML = parts.join('');
        updateCallCountdown();
    } catch (e) { /* ignore */ }
}

function updateCallCountdown() {
    const el = document.getElementById('call-countdown');
    if (!el || !_callDueAt) return;
    const remaining = Math.max(0, Math.floor((_callDueAt - Date.now()) / 1000));
    const m = Math.floor(remaining / 60), s = remaining % 60;
    el.textContent = remaining > 0 ? `settles in ${m}:${String(s).padStart(2, '0')}` : 'settling…';
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
        <text x="${cx}" y="${cy + 2}" text-anchor="middle" class="gauge-lbl">confidence</text>
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
                <div class="pred-row"><span>Probability Up</span><span class="${pUp != null && pUp >= 0.5 ? 'pos' : 'neg'}">${pUp != null ? (pUp * 100).toFixed(1) + '%' : '—'}</span></div>
                <div class="pred-row"><span>Probability Down</span><span>${pUp != null ? ((1 - pUp) * 100).toFixed(1) + '%' : '—'}</span></div>
                <div class="pred-row"><span>Regime</span><span>${data.regime || '—'}</span></div>
                <div class="pred-row"><span>Champion</span><span>${data.champion || 'none'}</span></div>
            </div>`;
        document.getElementById('champion-info').textContent = data.champion ? `♛ ${data.champion}` : '—';
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
        const rows = [
            ['Champion Model', d.champion || 'none promoted'],
            ['Engine Timeframe', d.timeframe],
            ['Current Regime', d.regime || '—'],
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