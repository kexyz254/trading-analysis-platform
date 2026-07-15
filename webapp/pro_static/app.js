/**
 * ATS Pro Trading Terminal — vanilla JS
 * Polls /api/pro/* endpoints for live updates. No external frameworks.
 */

let chart, volumeSeries, candleSeries, sma20, sma50, bbUpper, bbLower, tradeMarkers = [];
let currentSymbol = 'BTC/USDT';
let currentTimeframe = '1h';
let pollTimer;
const POLL_INTERVAL = 10000;

// --- Initialization ---
document.addEventListener('DOMContentLoaded', async () => {
    setupChart();
    await loadStatus();
    await loadSymbolList();
    await refreshAll();
    startPolling();

    // Event listeners
    document.getElementById('symbol-select').addEventListener('change', (e) => {
        currentSymbol = e.target.value;
        refreshAll();
    });
    document.querySelectorAll('.timeframe-tabs button').forEach(btn => {
        btn.addEventListener('click', (e) => {
            document.querySelectorAll('.timeframe-tabs button').forEach(b => b.classList.remove('active'));
            e.target.classList.add('active');
            currentTimeframe = e.target.dataset.tf;
            refreshAll();
        });
    });
});

// --- Chart Setup ---
function setupChart() {
    const container = document.getElementById('chart');
    chart = LightweightCharts.createChart(container, {
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
        timeScale: { borderColor: 'rgba(255,255,255,0.2)', timeVisible: true },
    });

    candleSeries = chart.addCandlestickSeries({
        upColor: '#00FFC2',
        downColor: '#FF3366',
        borderDownColor: '#FF3366',
        borderUpColor: '#00FFC2',
        wickDownColor: '#FF3366',
        wickUpColor: '#00FFC2',
    });

    sma20 = chart.addLineSeries({ color: '#FFB800', lineWidth: 1, title: 'SMA 20' });
    sma50 = chart.addLineSeries({ color: '#AA66FF', lineWidth: 1, title: 'SMA 50' });
    bbUpper = chart.addLineSeries({ color: 'rgba(0,255,196,0.3)', lineWidth: 1, lineStyle: LightweightCharts.LineStyle.Dashed, title: 'BB Upper' });
    bbLower = chart.addLineSeries({ color: 'rgba(0,255,196,0.3)', lineWidth: 1, lineStyle: LightweightCharts.LineStyle.Dashed, title: 'BB Lower' });

    // Volume pane
    const volContainer = document.getElementById('volume-chart');
    const volChart = LightweightCharts.createChart(volContainer, {
        layout: { background: { type: 'solid', color: '#0A0E14' }, textColor: '#EAEAEA' },
        grid: { vertLines: { color: 'rgba(255,255,255,0.05)' }, horzLines: { color: 'rgba(255,255,255,0.05)' } },
        rightPriceScale: { visible: false },
        timeScale: { visible: false },
    });
    volumeSeries = volChart.addHistogramSeries({
        color: 'rgba(0,255,196,0.4)',
    });
    // Sync time scale with main chart
    volChart.timeScale().subscribeVisibleTimeRangeChange(() => {});
    chart.timeScale().subscribeVisibleTimeRangeChange((range) => {
        volChart.timeScale().setVisibleRange(range);
    });
}

// --- Data Fetching ---
async function api(url) {
    const res = await fetch(url);
    if (!res.ok) throw new Error(res.statusText);
    return res.json();
}

async function refreshAll() {
    try {
        await Promise.all([
            loadOHLCV(),
            loadPrediction(),
            loadFeatures(),
            loadTrades(),
            loadStatus(),
            loadCandidates()
        ]);
    } catch(e) { console.warn('Refresh error', e); }
}

async function loadStatus() {
    const status = await api('/api/status');
    document.getElementById('mode-badge').textContent = status.mode.toUpperCase();
    document.getElementById('live-dot').style.color = status.is_live ? '#FF3366' : '#00FFC2';
    const health = await api('/api/health');
    document.getElementById('health-ring').style.color = health.score >= 90 ? '#00FFC2' : '#FFB800';
    document.getElementById('uptime').textContent = `Uptime: ${formatDuration(health.uptime_seconds)}`;
    document.getElementById('engine-status').className = health.score >= 80 ? 'status-green' : 'status-red';
    document.getElementById('learning-status').className = health.recent_error_rate === 0 ? 'status-green' : 'status-red';
    document.getElementById('risk-status').className = health.circuit_breaker_tripped ? 'status-red' : 'status-green';
    document.getElementById('exchange-status').className = 'status-green';
}

async function loadSymbolList() {
    const status = await api('/api/status');
    const select = document.getElementById('symbol-select');
    select.innerHTML = '';
    status.symbols.forEach(s => {
        const opt = document.createElement('option');
        opt.value = s;
        opt.textContent = s;
        if (s === currentSymbol) opt.selected = true;
        select.appendChild(opt);
    });
}

async function loadOHLCV() {
    const data = await api(`/api/pro/ohlcv/${currentSymbol}?timeframe=${currentTimeframe}&limit=200`);
    if (!data.bars || data.bars.length === 0) return;
    const bars = data.bars.map(b => ({
        time: b.timestamp,  // expects ISO string (works with Lightweight Charts)
        open: b.open,
        high: b.high,
        low: b.low,
        close: b.close,
        volume: b.volume,
    }));
    candleSeries.setData(bars);
    volumeSeries.setData(bars.map(b => ({ time: b.time, value: b.volume, color: b.close >= b.open ? 'rgba(0,255,196,0.4)' : 'rgba(255,51,102,0.4)' })));

    // Compute simple SMAs (client-side)
    const closes = bars.map(b => b.close);
    const sma20Data = calcSMA(closes, 20).map((v,i) => ({ time: bars[i].time, value: v })).filter(v => v.value !== null);
    const sma50Data = calcSMA(closes, 50).map((v,i) => ({ time: bars[i].time, value: v })).filter(v => v.value !== null);
    sma20.setData(sma20Data);
    sma50.setData(sma50Data);

    // Bollinger Bands (20,2)
    const bb = calcBollinger(closes, 20, 2);
    bbUpper.setData(bb.upper.map((v,i) => ({ time: bars[i].time, value: v })).filter(v => v.value !== null));
    bbLower.setData(bb.lower.map((v,i) => ({ time: bars[i].time, value: v })).filter(v => v.value !== null));

    // Current price display
    const last = bars[bars.length-1];
    document.getElementById('current-price').textContent = `$${last.close.toFixed(2)}`;
    const prev = bars.length > 1 ? bars[bars.length-2].close : last.close;
    const change = last.close - prev;
    const pct = (change / prev * 100).toFixed(2);
    document.getElementById('price-change').textContent = `${change >= 0 ? '+' : ''}${change.toFixed(2)} (${pct}%)`;
    document.getElementById('price-change').style.color = change >= 0 ? '#00FFC2' : '#FF3366';
}

function calcSMA(data, period) {
    const result = [];
    for (let i = 0; i < data.length; i++) {
        if (i < period - 1) { result.push(null); continue; }
        const slice = data.slice(i - period + 1, i + 1);
        result.push(slice.reduce((a,b) => a+b, 0) / period);
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

async function loadPrediction() {
    try {
        const data = await api(`/api/pro/prediction/${currentSymbol}`);
        const content = document.getElementById('prediction-content');
        content.innerHTML = `
            <div><strong>Signal:</strong> <span class="prediction-${data.signal === 'BUY' ? 'buy' : data.signal === 'SELL' ? 'sell' : ''}">${data.signal || 'HOLD'}</span></div>
            <div>Confidence: ${data.confidence ? (data.confidence*100).toFixed(1)+'%' : '—'}</div>
            <div>Probability Up: ${data.probability_up ? (data.probability_up*100).toFixed(1)+'%' : '—'}</div>
            <div>Regime: ${data.regime || '—'}</div>
            <div>Champion: ${data.champion || 'none'}</div>
        `;
    } catch(e) { /* ignore */ }
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
    } catch(e) { /* ignore */ }
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
    } catch(e) { /* ignore */ }
}

async function loadCandidates() {
    // Not yet linked to tournament data, placeholder
}

function formatDuration(seconds) {
    const h = Math.floor(seconds / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    return `${h}h ${m}m`;
}

function startPolling() {
    pollTimer = setInterval(refreshAll, POLL_INTERVAL);
}