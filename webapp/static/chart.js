// Candlestick + equity charts via TradingView's lightweight-charts (loaded from CDN in index.html).

// lightweight-charts parses plain color strings itself (hex/rgba), not through
// the DOM - it cannot consume CSS custom properties directly. Reading the
// computed values once at load, rather than hardcoding a second copy of the
// palette here, keeps style.css's :root as the single source of truth - this
// file's colors always match whatever the CSS theme actually is.
function themeColor(varName) {
  return getComputedStyle(document.documentElement).getPropertyValue(varName).trim();
}
function hexToRgba(hex, alpha) {
  const h = hex.replace("#", "");
  const r = parseInt(h.substring(0, 2), 16);
  const g = parseInt(h.substring(2, 4), 16);
  const b = parseInt(h.substring(4, 6), 16);
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}
const THEME = {
  bg: themeColor("--bg"),
  panel: themeColor("--panel"),
  panelBorder: themeColor("--panel-border"),
  text: themeColor("--text"),
  textSecondary: themeColor("--text-secondary"),
  accent: themeColor("--accent"),
  up: themeColor("--up"),
  down: themeColor("--down"),
  warn: themeColor("--warn"),
};

function toUnixSeconds(isoOrMs) {
  if (typeof isoOrMs === "number") return Math.floor(isoOrMs / 1000);
  const s = isoOrMs.endsWith("Z") || isoOrMs.includes("+") ? isoOrMs : isoOrMs + "Z";
  return Math.floor(new Date(s).getTime() / 1000);
}

// lightweight-charts' default tick/crosshair formatting uses plain JS Date
// getters internally, which resolve to whatever timezone the BROWSER
// happens to be running in - an ambient default, not a guarantee, and it
// silently drifts from the rest of this dashboard (ticker tape, tables,
// clock) which all explicitly force Africa/Nairobi via Intl.DateTimeFormat.
// Same fix as the Python logging timestamp bug: stop trusting an implicit
// "local time" and make the conversion explicit everywhere.
const _chartTimeFmt = new Intl.DateTimeFormat("en-GB", {
  timeZone: "Africa/Nairobi", hour: "2-digit", minute: "2-digit", hour12: false,
});
const _chartDateFmt = new Intl.DateTimeFormat("en-GB", {
  timeZone: "Africa/Nairobi", day: "2-digit", month: "short",
});
const _chartDateTimeFmt = new Intl.DateTimeFormat("en-GB", {
  timeZone: "Africa/Nairobi", day: "2-digit", month: "short", year: "numeric",
  hour: "2-digit", minute: "2-digit", hour12: false,
});

function _nairobiTickMarkFormatter(time, tickMarkType) {
  const date = new Date(time * 1000);
  if (tickMarkType === LightweightCharts.TickMarkType.Year) {
    return new Intl.DateTimeFormat("en-GB", { timeZone: "Africa/Nairobi", year: "numeric" }).format(date);
  }
  if (tickMarkType === LightweightCharts.TickMarkType.Month) {
    return new Intl.DateTimeFormat("en-GB", { timeZone: "Africa/Nairobi", month: "short" }).format(date);
  }
  if (tickMarkType === LightweightCharts.TickMarkType.DayOfMonth) {
    return _chartDateFmt.format(date);
  }
  return _chartTimeFmt.format(date); // Time / TimeWithSeconds
}

function makeChart(containerId, extraOptions = {}) {
  const container = document.getElementById(containerId);
  const chart = LightweightCharts.createChart(container, {
    width: container.clientWidth,
    height: container.clientHeight,
    layout: { background: { color: THEME.panel }, textColor: THEME.text },
    grid: { vertLines: { color: THEME.panelBorder }, horzLines: { color: THEME.panelBorder } },
    timeScale: {
      timeVisible: true, secondsVisible: false, borderColor: THEME.panelBorder,
      tickMarkFormatter: _nairobiTickMarkFormatter,
    },
    localization: {
      timeFormatter: (time) => _chartDateTimeFmt.format(new Date(time * 1000)) + " EAT",
    },
    rightPriceScale: { borderColor: THEME.panelBorder },
    crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
    ...extraOptions,
  });
  new ResizeObserver((entries) => {
    for (const entry of entries) {
      chart.applyOptions({ width: entry.contentRect.width, height: entry.contentRect.height });
    }
  }).observe(container);
  return chart;
}

// ---- Equity chart (Overview tab) ----
let equityChart = null;
let equitySeries = null;
let capitalSeries = null;

function drawEquityChart(equity) {
  if (!equityChart) {
    equityChart = makeChart("equity-chart");
    equitySeries = equityChart.addLineSeries({ color: THEME.accent, lineWidth: 2, title: "Total equity" });
    capitalSeries = equityChart.addLineSeries({ color: THEME.textSecondary, lineWidth: 1, title: "Capital (cash)" });
  }
  const equityData = equity.map((e) => ({ time: toUnixSeconds(e.timestamp), value: e.total_equity }));
  const capitalData = equity.map((e) => ({ time: toUnixSeconds(e.timestamp), value: e.capital }));
  equitySeries.setData(equityData);
  capitalSeries.setData(capitalData);
  equityChart.timeScale().fitContent();
}

// ---- Paper equity chart (Paper Trading tab) ----
let paperEquityChart = null;
let paperEquitySeries = null;

async function loadPaperEquityChart(symbol, candidate) {
  const history = await getJSON(
    `/api/paper-equity?symbol=${encodeURIComponent(symbol)}&candidate=${encodeURIComponent(candidate)}`
  );
  if (!paperEquityChart) {
    paperEquityChart = makeChart("paper-equity-chart");
    paperEquitySeries = paperEquityChart.addLineSeries({ color: THEME.warn, lineWidth: 2, title: "Paper equity" });
  }
  if (!history || history.length === 0) {
    document.getElementById("paper-metrics").innerHTML = "";
    return;
  }
  paperEquitySeries.setData(history.map((h) => ({ time: toUnixSeconds(h.timestamp), value: h.total_equity })));
  paperEquityChart.timeScale().fitContent();

  const latest = history[history.length - 1];
  const first = history[0];
  const pnlPct = ((latest.total_equity - first.total_equity) / first.total_equity) * 100;
  const metricsEl = document.getElementById("paper-metrics");
  metricsEl.innerHTML = "";
  [
    ["Paper equity", `$${fmtNum(latest.total_equity)}`, null],
    ["Since first snapshot", `${pnlPct >= 0 ? "+" : ""}${fmtNum(pnlPct)}%`, pnlPct >= 0 ? "up" : "down"],
    ["Trades", latest.num_trades, null],
  ].forEach(([label, value, cls]) => {
    const m = el("div", "metric");
    m.appendChild(el("span", "metric-label", label));
    m.appendChild(el("span", `metric-value${cls ? " " + cls : ""}`, value));
    metricsEl.appendChild(m);
  });
}

// ---- Candlestick chart (Chart tab) ----
let candleChart = null;
let candleSeries = null;
let volumeSeries = null;
let sma20Series = null;
let sma50Series = null;

function sma(values, window) {
  const out = new Array(values.length).fill(null);
  let sum = 0;
  for (let i = 0; i < values.length; i++) {
    sum += values[i];
    if (i >= window) sum -= values[i - window];
    if (i >= window - 1) out[i] = sum / window;
  }
  return out;
}

function ensureCandleChart() {
  if (candleChart) return;
  candleChart = makeChart("candle-chart");
  candleSeries = candleChart.addCandlestickSeries({
    upColor: THEME.up, downColor: THEME.down, borderVisible: false,
    wickUpColor: THEME.up, wickDownColor: THEME.down,
  });
  volumeSeries = candleChart.addHistogramSeries({
    priceFormat: { type: "volume" }, priceScaleId: "",
  });
  volumeSeries.priceScale().applyOptions({ scaleMargins: { top: 0.8, bottom: 0 } });
  sma20Series = candleChart.addLineSeries({ color: THEME.accent, lineWidth: 1, title: "SMA 20" });
  sma50Series = candleChart.addLineSeries({ color: THEME.warn, lineWidth: 1, title: "SMA 50" });
}

let currentSymbol = null;
let currentTimeframe = null;
let lastUpdatedAt = null;
let windowFirstClose = null;
const heroPrevPrices = {};

function updateHeroPrice(symbol, price, changePct) {
  const priceEl = document.getElementById("hero-price");
  const changeEl = document.getElementById("hero-change");
  const prev = heroPrevPrices[symbol];
  priceEl.textContent = fmtNum(price, price < 10 ? 4 : 2);
  changeEl.textContent = `${changePct >= 0 ? "+" : ""}${fmtNum(changePct)}% (window)`;
  changeEl.className = `hero-change ${changePct >= 0 ? "up-text" : "down-text"}`;

  if (prev !== undefined && price !== prev) {
    priceEl.classList.remove("flash-up", "flash-down");
    void priceEl.offsetWidth;
    priceEl.classList.add(price > prev ? "flash-up" : "flash-down");
  }
  heroPrevPrices[symbol] = price;
}

function tickLiveUpdatedLabel() {
  const label = document.getElementById("live-updated");
  if (!label) return;
  if (lastUpdatedAt === null) { label.textContent = ""; return; }
  const secs = Math.round((Date.now() - lastUpdatedAt) / 1000);
  label.textContent = `updated ${secs}s ago`;
}
setInterval(tickLiveUpdatedLabel, 1000);

async function loadCandles(symbol, timeframe) {
  ensureCandleChart();
  const candles = await getJSON(`/api/ohlcv?symbol=${encodeURIComponent(symbol)}&timeframe=${timeframe}&limit=300`);
  if (!candles || candles.length === 0) {
    document.getElementById("chart-caption").textContent = "No candle data available for this symbol/timeframe.";
    return;
  }
  const candleData = candles.map((c) => ({
    time: toUnixSeconds(c.timestamp), open: c.open, high: c.high, low: c.low, close: c.close,
  }));
  const volumeData = candles.map((c) => ({
    time: toUnixSeconds(c.timestamp), value: c.volume,
    color: c.close >= c.open ? hexToRgba(THEME.up, 0.5) : hexToRgba(THEME.down, 0.5),
  }));
  const closes = candles.map((c) => c.close);
  const sma20 = sma(closes, 20);
  const sma50 = sma(closes, 50);

  candleSeries.setData(candleData);
  volumeSeries.setData(volumeData);
  sma20Series.setData(candleData.map((c, i) => ({ time: c.time, value: sma20[i] })).filter((d) => d.value !== null));
  sma50Series.setData(candleData.map((c, i) => ({ time: c.time, value: sma50[i] })).filter((d) => d.value !== null));
  candleChart.timeScale().fitContent();

  const last = candles[candles.length - 1];
  const first = candles[0];
  windowFirstClose = first.close;
  const changePct = ((last.close - first.close) / first.close) * 100;
  const highs = candles.map((c) => c.high);
  const lows = candles.map((c) => c.low);

  updateHeroPrice(symbol, last.close, changePct);
  lastUpdatedAt = Date.now();
  tickLiveUpdatedLabel();

  const metricsEl = document.getElementById("chart-metrics");
  metricsEl.innerHTML = "";
  const metrics = [
    [`Change (${candles.length} bars)`, `${changePct >= 0 ? "+" : ""}${fmtNum(changePct)}%`, changePct >= 0 ? "up" : "down"],
    ["High", fmtNum(Math.max(...highs), 4), null],
    ["Low", fmtNum(Math.min(...lows), 4), null],
  ];
  metrics.forEach(([label, value, cls]) => {
    const m = el("div", "metric");
    m.appendChild(el("span", "metric-label", label));
    m.appendChild(el("span", `metric-value${cls ? " " + cls : ""}`, value));
    metricsEl.appendChild(m);
  });

  const tracked = new Set((SYSTEM_STATUS && SYSTEM_STATUS.symbols) || []);
  document.getElementById("chart-caption").textContent = tracked.has(symbol)
    ? `${symbol} is in the system's actively traded/learned universe.`
    : `${symbol} is shown for monitoring only — not currently traded by the system.`;
}

function setLivePolling(enabled) {
  if (!enabled) {
    clearKlineSubscription();
    return;
  }
  // Live candle updates via the same direct-to-Binance WebSocket as the
  // ticker tape (see ws.js) - the currently-forming candle updates in real
  // time as trades happen, rather than re-fetching the whole 300-bar
  // history over REST every few seconds.
  setKlineSubscription(currentSymbol, currentTimeframe, (k) => {
    const expectedSymbol = currentSymbol.replace("/", "").toUpperCase();
    if (k.symbol !== expectedSymbol || k.interval !== currentTimeframe) return; // stale message from a just-replaced subscription
    if (!candleSeries) return;

    candleSeries.update({ time: k.time, open: k.open, high: k.high, low: k.low, close: k.close });
    volumeSeries.update({
      time: k.time, value: k.volume,
      color: k.close >= k.open ? hexToRgba(THEME.up, 0.5) : hexToRgba(THEME.down, 0.5),
    });
    // series.update() only appends/replaces a data point - it does NOT move
    // the visible viewport. fitContent() ran once at load and never again,
    // so without this the chart's own DATA stays live (proven separately)
    // while the VISIBLE window silently falls further behind real time
    // every second - exactly what reads as "the chart's time is wrong."
    candleChart.timeScale().scrollToRealTime();

    const changePct = windowFirstClose ? ((k.close - windowFirstClose) / windowFirstClose) * 100 : 0;
    updateHeroPrice(currentSymbol, k.close, changePct);
    lastUpdatedAt = Date.now();
    tickLiveUpdatedLabel();
  });
}

async function selectSymbolTimeframe(symbol, timeframe) {
  currentSymbol = symbol;
  currentTimeframe = timeframe;
  document.querySelectorAll(".tf-btn").forEach((b) => b.classList.toggle("active", b.dataset.tf === timeframe));
  await loadCandles(symbol, timeframe);
  const liveToggle = document.getElementById("live-toggle");
  if (liveToggle && liveToggle.checked) setLivePolling(true); // re-subscribe kline stream to the new symbol/timeframe
}

async function initChartTab() {
  const timeframes = await getJSON("/api/timeframes");
  const tfGroup = document.getElementById("tf-group");
  tfGroup.innerHTML = "";
  timeframes.forEach((tf) => {
    const btn = el("button", "tf-btn", tf);
    btn.dataset.tf = tf;
    btn.addEventListener("click", () => selectSymbolTimeframe(currentSymbol, tf));
    tfGroup.appendChild(btn);
  });

  const trackedSymbols = (SYSTEM_STATUS && SYSTEM_STATUS.symbols) || [];
  const allSymbols = MARKET_ROWS.map((r) => r.symbol);
  const symbolOptions = [...new Set([...trackedSymbols, ...allSymbols])];
  const select = document.getElementById("chart-symbol");
  select.innerHTML = "";
  symbolOptions.forEach((s) => {
    const opt = el("option", null, s);
    opt.value = s;
    select.appendChild(opt);
  });
  select.addEventListener("change", () => selectSymbolTimeframe(select.value, currentTimeframe));

  document.getElementById("live-toggle").addEventListener("change", (e) => setLivePolling(e.target.checked));

  // Default to 1m: the system trades on SYSTEM_STATUS.timeframe (shown in the
  // header stat), but 1m is the most visually informative view for "is this
  // actually live" - a few dollars of movement is invisible on a 1h chart
  // spanning weeks, but obvious tick-to-tick on 1m.
  const initialTf = timeframes.includes("1m") ? "1m" : timeframes[0];
  select.value = symbolOptions[0];
  await selectSymbolTimeframe(symbolOptions[0], initialTf);

  const liveToggle = document.getElementById("live-toggle");
  setLivePolling(liveToggle.checked);
}
