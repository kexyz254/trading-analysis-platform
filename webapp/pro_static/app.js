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
document.addEventListener('DOMContentLoaded', async () => {
    setupChart();
    const status = await api('/api/status');
    renderStatus(status);
    renderSymbolList(status.symbols);
    await renderTimeframeTabs();
    initLiveFeed(status.ws_base_url);
    subscribeTickers(status.symbols, onTicker);
    await refreshAll();
    startLiveCandles();
    startPolling();

    document.getElementById('symbol-select').addEventListener('change', async (e) => {
        currentSymbol = e.target.value;
        await refreshAll();
        startLiveCandles();
    });
});

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
        ]);
    } catch (e) { console.warn('Refresh error', e); }
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
    if (k.isClosed) recomputeIndicators();
    updatePriceHeader();
}

function onTicker(t) {
    if (t.symbol !== binancePair(currentSymbol)) return;
    const el = document.getElementById('price-change');
    el.textContent = `${t.changePct >= 0 ? '+' : ''}${t.changePct.toFixed(2)}% (24h)`;
    el.style.color = t.changePct >= 0 ? '#00FFC2' : '#FF3366';
}

// --- Chart data ---
async function loadChart() {
    const data = await api(
        `/api/ohlcv?symbol=${encodeURIComponent(currentSymbol)}&timeframe=${currentTimeframe}&limit=${HISTORY_LIMIT}`
    );
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
    document.getElementById('current-price').textContent = `$${formatPrice(bars[bars.length - 1].close)}`;
}

// --- Prediction markers on the chart ---
async function loadDecisionMarkers() {
    try {
        const data = await api(`/api/pro/decisions/${currentSymbol}?limit=200`);
        if (bars.length === 0) return;
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

// --- Top bar / status ---
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
    const timeframes = await api('/api/timeframes');
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
            try { await loadChart(); } catch (e) { console.warn('Chart load error', e); }
            await loadDecisionMarkers();
            startLiveCandles();
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
async function loadPrediction() {
    try {
        const data = await api(`/api/pro/prediction/${currentSymbol}`);
        const content = document.getElementById('prediction-content');
        content.innerHTML = `
            <div><strong>Signal:</strong> <span class="prediction-${data.signal === 'BUY' ? 'buy' : data.signal === 'SELL' ? 'sell' : ''}">${data.signal || 'HOLD'}</span></div>
            <div>Confidence: ${data.confidence ? (data.confidence * 100).toFixed(1) + '%' : '—'}</div>
            <div>Probability Up: ${data.probability_up ? (data.probability_up * 100).toFixed(1) + '%' : '—'}</div>
            <div>Regime: ${data.regime || '—'}</div>
            <div>Champion: ${data.champion || 'none'}</div>
        `;
        document.getElementById('champion-info').textContent = data.champion ? `♛ ${data.champion}` : '—';
    } catch (e) { /* ignore */ }
}

async function loadPosition() {
    try {
        const data = await api(`/api/pro/position/${currentSymbol}`);
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
        const data = await api(`/api/pro/features/${currentSymbol}`);
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
        const data = await api(`/api/pro/trades/${currentSymbol}?limit=20`);
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
        const data = await api(`/api/pro/candidates/${currentSymbol}`);
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
