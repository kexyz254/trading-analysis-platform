// ATS Pro Terminal – Full Error Logging
let chart, candleSeries, volumeSeries, sma20, sma50, bbUpper, bbLower;
let currentSymbol = 'BTC/USDT', currentTimeframe = '1h', pollTimer;
const POLL = 10000;

function el(id) { return document.getElementById(id); }
function log(msg) { const s = el('status-text'); if (s) s.textContent = msg; console.log(msg); }

async function api(url) {
    const r = await fetch(url);
    if (!r.ok) throw new Error(`${r.status}: ${r.statusText}`);
    return r.json();
}

function setupChart() {
    try {
        const container = el('chart');
        chart = LightweightCharts.createChart(container, {
            layout: { background: { type: 'solid', color: '#0A0E14' }, textColor: '#EAEAEA' },
            grid: { vertLines: { color: 'rgba(255,255,255,0.05)' }, horzLines: { color: 'rgba(255,255,255,0.05)' } },
            crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
            rightPriceScale: { borderColor: 'rgba(255,255,255,0.2)' },
            timeScale: { borderColor: 'rgba(255,255,255,0.2)', timeVisible: true },
        });
        candleSeries = chart.addCandlestickSeries({
            upColor: '#00FFC2', downColor: '#FF3366',
            borderDownColor: '#FF3366', borderUpColor: '#00FFC2',
            wickDownColor: '#FF3366', wickUpColor: '#00FFC2',
        });
        sma20 = chart.addLineSeries({ color: '#FFB800', lineWidth: 1 });
        sma50 = chart.addLineSeries({ color: '#AA66FF', lineWidth: 1 });
        bbUpper = chart.addLineSeries({ color: 'rgba(0,255,196,0.3)', lineWidth: 1, lineStyle: LightweightCharts.LineStyle.Dashed });
        bbLower = chart.addLineSeries({ color: 'rgba(0,255,196,0.3)', lineWidth: 1, lineStyle: LightweightCharts.LineStyle.Dashed });

        const vc = el('volume-chart');
        const volChart = LightweightCharts.createChart(vc, {
            layout: { background: { type: 'solid', color: '#0A0E14' }, textColor: '#EAEAEA' },
            grid: { vertLines: { color: 'rgba(255,255,255,0.05)' }, horzLines: { color: 'rgba(255,255,255,0.05)' } },
            rightPriceScale: { visible: false }, timeScale: { visible: false },
        });
        volumeSeries = volChart.addHistogramSeries({ color: 'rgba(0,255,196,0.4)' });
        chart.timeScale().subscribeVisibleTimeRangeChange(range => volChart.timeScale().setVisibleRange(range));
        log('Chart ready');
    } catch(e) { log('Chart init failed: ' + e.message); }
}

async function loadStatus() {
    try {
        const s = await api('/api/status');
        el('mode-badge').textContent = s.mode.toUpperCase();
        el('live-dot').style.color = s.is_live ? '#FF3366' : '#00FFC2';
    } catch(e) { log('Status error: ' + e.message); }
    try {
        const h = await api('/api/health');
        el('health-ring').style.color = h.score >= 90 ? '#00FFC2' : '#FFB800';
        el('uptime').textContent = `Uptime: ${Math.floor(h.uptime_seconds/3600)}h ${Math.floor((h.uptime_seconds%3600)/60)}m`;
    } catch(e) { log('Health error: ' + e.message); }
}

async function loadSymbolList() {
    try {
        const s = await api('/api/status');
        const sel = el('symbol-select');
        sel.innerHTML = '';
        s.symbols.forEach(sym => {
            const o = document.createElement('option');
            o.value = sym; o.textContent = sym;
            if (sym === currentSymbol) o.selected = true;
            sel.appendChild(o);
        });
    } catch(e) { log('Symbol list error: ' + e.message); }
}

async function loadOHLCV() {
    try {
        const url = `/api/pro/ohlcv?symbol=${encodeURIComponent(currentSymbol)}&timeframe=${currentTimeframe}&limit=200`;
        const d = await api(url);
        if (!d.bars || d.bars.length < 2) {
            log('OHLCV empty or too few bars');
            return;
        }
        const bars = d.bars.map(b => ({ time: b.timestamp, open: b.open, high: b.high, low: b.low, close: b.close, volume: b.volume }));
        candleSeries.setData(bars);
        volumeSeries.setData(bars.map(b => ({ time: b.time, value: b.volume, color: b.close >= b.open ? 'rgba(0,255,196,0.4)' : 'rgba(255,51,102,0.4)' })));

        const closes = bars.map(b => b.close);
        const sma20Data = [], sma50Data = [];
        for (let i = 0; i < closes.length; i++) {
            if (i >= 19) sma20Data.push({ time: bars[i].time, value: closes.slice(i-19, i+1).reduce((a,b)=>a+b,0)/20 });
            if (i >= 49) sma50Data.push({ time: bars[i].time, value: closes.slice(i-49, i+1).reduce((a,b)=>a+b,0)/50 });
        }
        sma20.setData(sma20Data); sma50.setData(sma50Data);

        const bbUpperData = [], bbLowerData = [];
        for (let i = 19; i < closes.length; i++) {
            const slice = closes.slice(i-19, i+1);
            const mean = slice.reduce((a,b)=>a+b,0)/20;
            const std = Math.sqrt(slice.reduce((s,v)=>s+Math.pow(v-mean,2),0)/20);
            bbUpperData.push({ time: bars[i].time, value: mean + 2*std });
            bbLowerData.push({ time: bars[i].time, value: mean - 2*std });
        }
        bbUpper.setData(bbUpperData); bbLower.setData(bbLowerData);

        const last = bars[bars.length-1], prev = bars.length > 1 ? bars[bars.length-2].close : last.close;
        const change = last.close - prev, pct = (change/prev*100).toFixed(2);
        el('current-price').textContent = `$${last.close.toFixed(2)}`;
        el('price-change').textContent = `${change>=0?'+':''}${change.toFixed(2)} (${pct}%)`;
        el('price-change').style.color = change >= 0 ? '#00FFC2' : '#FF3366';
        log(`OHLCV OK (${bars.length} bars)`);
    } catch(e) { log('OHLCV error: ' + e.message); }
}

async function loadPrediction() {
    try {
        const d = await api(`/api/pro/prediction?symbol=${encodeURIComponent(currentSymbol)}`);
        const s = d.signal || 'HOLD';
        const cls = s === 'BUY' ? 'prediction-buy' : s === 'SELL' ? 'prediction-sell' : '';
        el('prediction-content').innerHTML = `
            <div><strong>Signal:</strong> <span class="${cls}">${s}</span></div>
            <div>Confidence: ${d.confidence ? (d.confidence*100).toFixed(1)+'%' : '—'}</div>
            <div>Probability Up: ${d.probability_up ? (d.probability_up*100).toFixed(1)+'%' : '—'}</div>
            <div>Regime: ${d.regime || '—'}</div>
            <div>Champion: ${d.champion || 'none'}</div>`;
        log('Prediction OK');
    } catch(e) { log('Prediction error: ' + e.message); }
}

async function loadFeatures() {
    try {
        const d = await api(`/api/pro/features?symbol=${encodeURIComponent(currentSymbol)}`);
        const c = el('features-content');
        if (!d.features || !d.features.length) { c.textContent = 'No features yet'; return; }
        let html = '<ul class="feature-list">';
        d.features.forEach(f => { html += `<li><span>${f.name}</span><span>${f.importance.toFixed(3)}</span></li>`; });
        html += '</ul>';
        c.innerHTML = html;
        log('Features OK');
    } catch(e) { log('Features error: ' + e.message); }
}

async function loadTrades() {
    try {
        const d = await api(`/api/pro/trades?symbol=${encodeURIComponent(currentSymbol)}&limit=20`);
        const c = el('trades-content');
        if (!d.trades || !d.trades.length) { c.textContent = 'No trades yet'; log('Trades empty'); return; }
        let html = '<table class="trades-table"><tr><th>Time</th><th>Side</th><th>Price</th><th>Size</th><th>PnL</th></tr>';
        d.trades.forEach(t => {
            html += `<tr><td>${new Date(t.timestamp).toLocaleTimeString()}</td><td>${t.side}</td><td>${t.price}</td><td>${t.quantity}</td><td class="${t.pnl>=0?'positive':'negative'}">${t.pnl ? t.pnl.toFixed(2) : '—'}</td></tr>`;
        });
        html += '</table>'; c.innerHTML = html;
        log('Trades OK');
    } catch(e) { log('Trades error: ' + e.message); }
}

async function refreshAll() {
    await Promise.all([loadStatus(), loadOHLCV(), loadPrediction(), loadFeatures(), loadTrades()]);
}

async function boot() {
    setupChart();
    await loadSymbolList();
    await refreshAll();
    pollTimer = setInterval(refreshAll, POLL);
    log('Dashboard ready');
}
document.addEventListener('DOMContentLoaded', () => {
    try { boot(); } catch(e) { log('Boot error: ' + e.message); }
});
document.addEventListener('DOMContentLoaded', () => {
    setTimeout(() => {
        const sel = el('symbol-select');
        if (sel) sel.addEventListener('change', e => { currentSymbol = e.target.value; refreshAll(); });
        document.querySelectorAll('.timeframe-tabs button').forEach(btn => {
            btn.addEventListener('click', e => {
                document.querySelectorAll('.timeframe-tabs button').forEach(b => b.classList.remove('active'));
                e.target.classList.add('active');
                currentTimeframe = e.target.dataset.tf;
                refreshAll();
            });
        });
    }, 500);
});
