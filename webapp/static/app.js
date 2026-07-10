// Shared helpers + Overview/Markets tabs. Candlestick chart logic lives in chart.js.

const NAIROBI_TZ = "Africa/Nairobi";

function fmtNairobi(isoString, opts) {
  if (!isoString) return "—";
  const d = new Date(isoString.endsWith("Z") || isoString.includes("+") ? isoString : isoString + "Z");
  return new Intl.DateTimeFormat("en-GB", {
    timeZone: NAIROBI_TZ,
    year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", second: "2-digit",
    hour12: false,
    ...opts,
  }).format(d).replace(",", "");
}

function fmtNum(v, digits = 2) {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  return Number(v).toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

async function getJSON(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${url} -> HTTP ${res.status}`);
  return res.json();
}

function el(tag, className, text) {
  const e = document.createElement(tag);
  if (className) e.className = className;
  if (text !== undefined) e.textContent = text;
  return e;
}

// Styled table-cell content, built as a real Node (textContent-based, so it
// auto-escapes) rather than an HTML string - renderTable's formatter path
// innerHTML's any STRING a formatter returns, which would be a stored-XSS
// footgun the moment a formatter interpolates real exchange/user-supplied
// text (a symbol name, a free-text message) into a template literal instead
// of a fixed internal value. Every formatter that wraps a value in a
// `<span class="...">` should build it with this helper, not a template
// literal, regardless of whether the current value happens to be trusted.
function safeSpan(text, className) {
  return el("span", className, text);
}

const PREFERS_REDUCED_MOTION = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

// Generic tween for "big number" displays - snaps instantly instead of
// animating when the viewer has asked for reduced motion, rather than
// silently ignoring that preference.
function animateCounter(element, from, to, opts = {}) {
  const { duration = 600, format = (v) => fmtNum(v) } = opts;
  if (PREFERS_REDUCED_MOTION || from === null || from === undefined || !Number.isFinite(from)) {
    element.textContent = format(to);
    return;
  }
  const start = performance.now();
  function tick(now) {
    const t = Math.min(1, (now - start) / duration);
    const eased = 1 - Math.pow(1 - t, 3);
    element.textContent = format(from + (to - from) * eased);
    if (t < 1) requestAnimationFrame(tick);
    else element.textContent = format(to);
  }
  requestAnimationFrame(tick);
}

// Shown only until a container's first real render replaces it (every
// render function already clears the container as its first action) -
// never left showing over stale-but-real content on subsequent refreshes.
function showSkeleton(containerId, rows = 3) {
  const container = document.getElementById(containerId);
  if (!container) return;
  container.innerHTML = "";
  for (let i = 0; i < rows; i++) container.appendChild(el("div", "skeleton-row"));
}

function renderTable(container, columns, rows, formatters = {}) {
  container.innerHTML = "";
  if (!rows || rows.length === 0) {
    container.appendChild(el("div", "empty-msg", "No data yet."));
    return;
  }
  const table = document.createElement("table");
  const thead = document.createElement("thead");
  const headRow = document.createElement("tr");
  columns.forEach((c) => headRow.appendChild(el("th", null, c.label)));
  thead.appendChild(headRow);
  table.appendChild(thead);

  const tbody = document.createElement("tbody");
  rows.forEach((row) => {
    const tr = document.createElement("tr");
    columns.forEach((c) => {
      const td = document.createElement("td");
      const raw = row[c.key];
      const fmt = formatters[c.key];
      if (fmt) {
        const result = fmt(raw, row);
        if (result instanceof Node) td.appendChild(result);
        else td.innerHTML = result;
      } else {
        td.textContent = raw === null || raw === undefined ? "—" : raw;
      }
      tr.appendChild(td);
    });
    tbody.appendChild(tr);
  });
  table.appendChild(tbody);
  container.appendChild(table);
}

// ---- Clock ----
function tickClock() {
  document.getElementById("clock").textContent = fmtNairobi(new Date().toISOString()) + " EAT";
}
setInterval(tickClock, 1000);
tickClock();

// ---- Tabs ----
document.querySelectorAll(".tab-btn").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".tab-btn").forEach((b) => b.classList.remove("active"));
    document.querySelectorAll(".tab-panel").forEach((p) => p.classList.remove("active"));
    btn.classList.add("active");
    document.getElementById(`tab-${btn.dataset.tab}`).classList.add("active");
  });
});

// ---- Status header ----
let SYSTEM_STATUS = null;

async function loadStatus() {
  const status = await getJSON("/api/status");
  SYSTEM_STATUS = status;
  const modeEl = document.getElementById("stat-mode");
  modeEl.textContent = status.mode.toUpperCase();
  modeEl.classList.toggle("mode-live", status.is_live);
  modeEl.classList.toggle("mode-testnet", !status.is_live);
  document.getElementById("stat-symbols").textContent = status.symbols.length;
  document.getElementById("stat-tournament").textContent = status.tournament_enabled
    ? (status.tournament_drive_live_trading ? "ON (live)" : "ON (observation)")
    : "off";
  document.getElementById("stat-timeframe").textContent = status.timeframe;
  document.getElementById("live-banner").classList.toggle("hidden", !status.is_live);
  const marketsCaption = document.getElementById("markets-caption");
  if (!status.is_live) {
    marketsCaption.textContent += " Testnet mode: this reflects Binance testnet's own (smaller) market list, not mainnet.";
  }
  return status;
}

// ---- Overview tab ----
async function loadOverview() {
  const journalData = await getJSON("/api/journal");

  renderEquity(journalData.equity);
  renderPositions(journalData.equity);
  renderCircuitBreaker(journalData.circuit_breaker);
  renderDecisions(journalData.decisions);
  renderTrades(journalData.trades);

  const alertRows = await getJSON("/api/alerts?limit=100");
  renderAlerts(alertRows);
  renderAlertTicker(alertRows);
  maybeToast(alertRows);

  const retrain = await getJSON("/api/retrain-events");
  renderRetrainEvents(retrain);

  const stability = await getJSON("/api/stability");
  renderTournament(stability);

  const realPerf = await getJSON("/api/real-performance");
  renderRealPerformance(realPerf);
  renderQuickStats(journalData, realPerf);
}

// ---- System health (sidebar ring + topbar dot + uptime) ----
// Backed by GET /api/health, which is itself computed from real
// system_heartbeat/circuit_breaker rows the engine process writes to the
// journal (see trading_assistant/health.py) - never a fabricated number.
// A database with no heartbeat rows yet (old engine build, fresh DB)
// reports score: null, label: "unknown", rendered honestly as
// "Unavailable" rather than defaulting to a fake "healthy".
const HEALTH_RING_CIRCUMFERENCE = 157; // matches the SVG semicircle path (r=50): pi * 50 ~= 157
let _healthUptimeBaseSeconds = null;
let _healthUptimeFetchedAt = null;

function fmtUptime(totalSeconds) {
  if (totalSeconds === null || totalSeconds === undefined) return "Uptime: —";
  const s = Math.max(0, Math.floor(totalSeconds));
  const days = Math.floor(s / 86400);
  const hours = Math.floor((s % 86400) / 3600);
  const mins = Math.floor((s % 3600) / 60);
  if (days > 0) return `Uptime: ${days}d ${hours}h`;
  if (hours > 0) return `Uptime: ${hours}h ${mins}m`;
  return `Uptime: ${mins}m`;
}

function renderHealthRing(health) {
  const fill = document.getElementById("health-ring-fill");
  const scoreEl = document.getElementById("health-ring-score");
  const labelEl = document.getElementById("health-label");
  const uptimeEl = document.getElementById("health-uptime");
  const topbarDot = document.getElementById("topbar-health-dot");
  if (!fill || !scoreEl || !labelEl || !uptimeEl || !topbarDot) return;

  const label = health.label || "unknown";
  fill.classList.remove("healthy", "degraded", "critical");
  topbarDot.classList.remove("healthy", "degraded", "critical");
  if (label !== "unknown") {
    fill.classList.add(label);
    topbarDot.classList.add(label);
  }
  topbarDot.title = `System health: ${label}`;

  const hasScore = health.score !== null && health.score !== undefined;
  const frac = hasScore ? health.score / 100 : 0;
  fill.style.strokeDasharray = `${HEALTH_RING_CIRCUMFERENCE * frac} ${HEALTH_RING_CIRCUMFERENCE}`;
  scoreEl.textContent = hasScore ? String(health.score) : "—";
  labelEl.textContent = label === "unknown" ? "Unavailable" : label.charAt(0).toUpperCase() + label.slice(1);

  if (health.uptime_seconds === null || health.uptime_seconds === undefined) {
    _healthUptimeBaseSeconds = null;
    _healthUptimeFetchedAt = null;
    uptimeEl.textContent = "Uptime: —";
  } else {
    _healthUptimeBaseSeconds = health.uptime_seconds;
    _healthUptimeFetchedAt = Date.now();
    uptimeEl.textContent = fmtUptime(_healthUptimeBaseSeconds);
  }
}

function tickUptime() {
  if (_healthUptimeBaseSeconds === null || _healthUptimeFetchedAt === null) return;
  const uptimeEl = document.getElementById("health-uptime");
  if (!uptimeEl) return;
  const elapsed = (Date.now() - _healthUptimeFetchedAt) / 1000;
  uptimeEl.textContent = fmtUptime(_healthUptimeBaseSeconds + elapsed);
}
setInterval(tickUptime, 1000);

async function loadHealth() {
  const health = await getJSON("/api/health");
  renderHealthRing(health);
}

// ---- Sidebar: alert ticker ----
function renderAlertTicker(alertRows) {
  const container = document.getElementById("alert-ticker");
  container.innerHTML = "";
  if (!alertRows || alertRows.length === 0) {
    container.appendChild(el("div", "empty-msg", "No alerts yet."));
    return;
  }
  const recent = [...alertRows].reverse().slice(0, 10); // most recent first, same convention as renderAlerts
  recent.forEach((a) => {
    const item = el("div", `alert-ticker-item severity-${a.severity}`);
    item.title = a.message;
    item.appendChild(el("span", "alert-ticker-dot"));
    const body = el("div", "alert-ticker-body");
    body.appendChild(el("div", "alert-ticker-message", a.message));
    body.appendChild(el("div", "alert-ticker-time", fmtNairobi(a.timestamp)));
    item.appendChild(body);
    container.appendChild(item);
  });
}

// ---- Toast notifications ----
// Diffs each alerts poll against the ids/keys already seen so only
// genuinely NEW alerts toast - not a decorative timer, a real new-data
// detector. The very first call (page just loaded) only records what's
// already there rather than toasting a backlog of pre-existing alerts.
const _seenAlertKeys = new Set();
let _toastBootstrapped = false;

function maybeToast(alertRows) {
  if (!alertRows || alertRows.length === 0) return;
  if (!_toastBootstrapped) {
    alertRows.forEach((a) => _seenAlertKeys.add(`${a.timestamp}|${a.category}`));
    _toastBootstrapped = true;
    return;
  }
  alertRows.forEach((a) => {
    const key = `${a.timestamp}|${a.category}`;
    if (_seenAlertKeys.has(key)) return;
    _seenAlertKeys.add(key);
    showToast(a);
  });
}

function showToast(alert) {
  const container = document.getElementById("toast-container");
  if (!container) return;
  const toast = el("div", `toast severity-${alert.severity}`);
  toast.appendChild(el("span", "toast-dot"));
  const body = el("div", "toast-body");
  body.appendChild(el("div", "toast-category", alert.category));
  body.appendChild(el("div", "toast-message", alert.message));
  toast.appendChild(body);
  container.appendChild(toast);
  requestAnimationFrame(() => toast.classList.add("entering"));
  setTimeout(() => {
    toast.classList.remove("entering");
    toast.classList.add("leaving");
    setTimeout(() => toast.remove(), 400);
  }, 6000);
}

// ---- Sidebar: quick stats ----
// Every value here is derived from data loadOverview already fetched (no
// extra network call) - "today's PnL" specifically has no dedicated
// endpoint, so it's computed client-side from the same equity snapshots
// already shown on the equity chart: latest equity minus the first
// snapshot recorded on today's Nairobi calendar date. Genuinely "no data
// yet today" (e.g. right after midnight, before the next cycle's
// snapshot) shows "—", never a fabricated zero.
function nairobiDateKey(isoString) {
  if (!isoString) return null;
  const d = new Date(isoString.endsWith("Z") || isoString.includes("+") ? isoString : isoString + "Z");
  return new Intl.DateTimeFormat("en-CA", { timeZone: NAIROBI_TZ }).format(d);
}

let _lastQsEquity = null;

function renderQuickStats(journalData, realPerf) {
  const equity = journalData.equity || [];
  const trades = journalData.trades || [];
  const todayKey = nairobiDateKey(new Date().toISOString());

  const equityEl = document.getElementById("qs-equity");
  const pnlEl = document.getElementById("qs-pnl-today");
  const tradesEl = document.getElementById("qs-trades-today");
  const winrateEl = document.getElementById("qs-winrate");

  if (equity.length === 0) {
    equityEl.textContent = "—";
    pnlEl.textContent = "—";
    pnlEl.className = "quick-stat-value";
  } else {
    const latest = equity[equity.length - 1];
    const fmtEquity = (v) => `$${fmtNum(v)}`;
    if (_lastQsEquity !== null && _lastQsEquity !== latest.total_equity) {
      animateCounter(equityEl, _lastQsEquity, latest.total_equity, { format: fmtEquity });
    } else {
      equityEl.textContent = fmtEquity(latest.total_equity);
    }
    _lastQsEquity = latest.total_equity;

    const startOfToday = equity.find((e) => nairobiDateKey(e.timestamp) === todayKey);
    if (startOfToday) {
      const pnl = latest.total_equity - startOfToday.total_equity;
      pnlEl.textContent = `${pnl >= 0 ? "+" : ""}$${fmtNum(pnl)}`;
      pnlEl.className = `quick-stat-value ${pnl >= 0 ? "up" : "down"}`;
    } else {
      pnlEl.textContent = "—";
      pnlEl.className = "quick-stat-value";
    }
  }

  const tradesToday = trades.filter((t) => nairobiDateKey(t.timestamp) === todayKey);
  tradesEl.textContent = String(tradesToday.length);

  winrateEl.textContent =
    realPerf && realPerf.win_rate !== null && realPerf.win_rate !== undefined
      ? `${fmtNum(realPerf.win_rate * 100, 0)}%`
      : "—";
}

function renderRealPerformance(perf) {
  const metricsEl = document.getElementById("real-performance-metrics");
  metricsEl.innerHTML = "";
  const metrics = [
    ["Sharpe ratio", fmtNum(perf.sharpe_ratio, 2), perf.sharpe_ratio >= 0 ? "up" : "down"],
    ["Max drawdown", `${fmtNum(perf.max_drawdown * 100, 2)}%`, null],
    ["Completed trades", perf.total_trades, null],
    ["Win rate", `${fmtNum((perf.win_rate || 0) * 100, 0)}%`, null],
  ];
  metrics.forEach(([label, value, cls]) => {
    const m = el("div", "metric");
    m.appendChild(el("span", "metric-label", label));
    m.appendChild(el("span", `metric-value${cls ? " " + cls : ""}`, value));
    metricsEl.appendChild(m);
  });
}

// ---- Readiness tab ----
async function loadReadinessTab() {
  const [report, stabilityRows, driftRows, backtestLiveRows, executionQuality, tradeAuditRows] = await Promise.all([
    getJSON("/api/readiness"),
    getJSON("/api/stability"),
    getJSON("/api/drift"),
    getJSON("/api/backtest-live-correlation"),
    getJSON("/api/execution-quality"),
    getJSON("/api/trade-audit?limit=100"),
  ]);

  const verdictEl = document.getElementById("readiness-verdict");
  verdictEl.className = `readiness-verdict ${report.overall_ready ? "ready" : "not-ready"}`;
  verdictEl.textContent = report.overall_ready
    ? "✓ READY — every criterion below is met"
    : "✗ NOT READY — see criteria below for exactly why";

  const criteriaEl = document.getElementById("readiness-criteria");
  criteriaEl.innerHTML = "";
  report.criteria.forEach((c) => {
    const card = el("div", "criterion-card");
    const icon = el("span", `criterion-icon ${c.passed ? "pass" : "fail"}`, c.passed ? "✓" : "✗");
    const text = el("div");
    text.appendChild(el("div", "criterion-name", c.name));
    text.appendChild(el("div", "criterion-detail", c.detail));
    card.appendChild(icon);
    card.appendChild(text);
    criteriaEl.appendChild(card);
  });

  renderStabilityTable("stability-table", stabilityRows);
  renderDriftTable("drift-table", driftRows);
  renderExecutionQualityTable("execution-quality-table", executionQuality.by_symbol);
  renderTradeAuditTable("trade-audit-table", tradeAuditRows);
  renderBacktestLiveTable("backtest-live-table", backtestLiveRows);
}

function renderTradeAuditTable(containerId, rows) {
  const container = document.getElementById(containerId);
  if (!rows || rows.length === 0) {
    container.innerHTML = '<div class="empty-msg">No realized trades yet.</div>';
    return;
  }
  renderTable(container, [
    { key: "exit_timestamp", label: "Closed (EAT)" },
    { key: "symbol", label: "Symbol" },
    { key: "side", label: "Side" },
    { key: "entry_price", label: "Entry" },
    { key: "exit_price", label: "Exit" },
    { key: "quantity", label: "Qty" },
    { key: "strategy_name", label: "Strategy" },
    { key: "strategy_version", label: "Version" },
    { key: "model_confidence", label: "Confidence" },
    { key: "market_regime", label: "Regime" },
    { key: "realized_pnl", label: "Realized PnL" },
    { key: "exit_reason", label: "Exit reason" },
  ], rows, {
    exit_timestamp: (v) => fmtNairobi(v),
    entry_price: (v) => (v === null || v === undefined ? "—" : fmtNum(v, 4)),
    exit_price: (v) => fmtNum(v, 4),
    model_confidence: (v) => (v === null || v === undefined ? "—" : fmtNum(v, 3)),
    strategy_name: (v) => (v ? safeSpan(v) : "—"),
    strategy_version: (v) => (v ? safeSpan(v) : "—"),
    market_regime: (v) => (v ? safeSpan(v) : "—"),
    realized_pnl: (v) =>
      v === null || v === undefined ? "—" : safeSpan(fmtNum(v, 4), v >= 0 ? "up-text" : "down-text"),
  });
}

function renderExecutionQualityTable(containerId, rows) {
  const container = document.getElementById(containerId);
  if (!rows || rows.length === 0) {
    container.innerHTML = '<div class="empty-msg">No real orders executed yet.</div>';
    return;
  }
  renderTable(container, [
    { key: "symbol", label: "Symbol" },
    { key: "num_orders", label: "Orders" },
    { key: "avg_slippage_pct", label: "Avg slippage" },
    { key: "worst_slippage_pct", label: "Worst slippage" },
    { key: "avg_latency_ms", label: "Avg latency" },
  ], rows, {
    avg_slippage_pct: (v) => (v === null ? "—" : safeSpan(`${(v * 100).toFixed(2)}%`, v > 0 ? "down-text" : "up-text")),
    worst_slippage_pct: (v) => (v === null ? "—" : safeSpan(`${(v * 100).toFixed(2)}%`, v > 0 ? "down-text" : "up-text")),
    avg_latency_ms: (v) => (v === null ? "—" : `${fmtNum(v, 0)} ms`),
  });
}

function renderBacktestLiveTable(containerId, rows) {
  const container = document.getElementById(containerId);
  if (!rows || rows.length === 0) {
    container.innerHTML = '<div class="empty-msg">No symbol/candidate pair has enough live paper trades yet to compare against its backtest.</div>';
    return;
  }
  const displayRows = rows.map((r) => ({
    ...r,
    agrees: (r.backtest_sharpe >= 0) === (r.live_sharpe >= 0),
  }));
  renderTable(container, [
    { key: "symbol", label: "Symbol" },
    { key: "candidate", label: "Candidate" },
    { key: "backtest_sharpe", label: "Backtest Sharpe" },
    { key: "live_sharpe", label: "Live paper Sharpe" },
    { key: "live_trades", label: "Live trades" },
    { key: "agrees", label: "Direction agrees" },
  ], displayRows, {
    backtest_sharpe: (v) => fmtNum(v, 2),
    live_sharpe: (v) => fmtNum(v, 2),
    agrees: (v) => safeSpan(v ? "yes" : "no", v ? "up-text" : "down-text"),
  });
}

function renderDriftTable(containerId, rows) {
  const container = document.getElementById(containerId);
  if (!rows || rows.length === 0) {
    container.innerHTML = '<div class="empty-msg">No drift checks recorded yet - a model must be promoted for a symbol before its live feature distribution can be compared against a training baseline.</div>';
    return;
  }
  const displayRows = rows.map((r) => ({
    ...r,
    worst_feature: r.summary.worst_features[0] ? r.summary.worst_features[0].feature : "n/a",
    worst_psi: r.summary.worst_features[0] ? r.summary.worst_features[0].psi : null,
  }));
  renderTable(container, [
    { key: "symbol", label: "Symbol" },
    { key: "overall_severity", label: "Severity" },
    { key: "worst_feature", label: "Worst-drifted feature" },
    { key: "worst_psi", label: "PSI" },
    { key: "timestamp", label: "Checked at" },
  ], displayRows, {
    overall_severity: (v) => {
      const cls = v === "significant" ? "down-text" : v === "moderate" ? "" : "up-text";
      return safeSpan(v, cls);
    },
    worst_psi: (v) => (v === null || v === undefined ? "—" : fmtNum(v, 4)),
  });
}

function renderEquity(equity) {
  const metricsEl = document.getElementById("equity-metrics");
  metricsEl.innerHTML = "";
  if (!equity || equity.length === 0) {
    document.getElementById("equity-chart").innerHTML =
      '<div class="empty-msg">No equity snapshots recorded yet - the system needs at least one full cycle to mark to market.</div>';
    return;
  }
  drawEquityChart(equity);

  const latest = equity[equity.length - 1];
  const first = equity[0];
  const pnlPct = ((latest.total_equity - first.total_equity) / first.total_equity) * 100;
  const metrics = [
    ["Current equity", `$${fmtNum(latest.total_equity)}`, null],
    ["Cash (capital)", `$${fmtNum(latest.capital)}`, null],
    ["Return since first snapshot", `${pnlPct >= 0 ? "+" : ""}${fmtNum(pnlPct)}%`, pnlPct >= 0 ? "up" : "down"],
  ];
  metrics.forEach(([label, value, cls]) => {
    const m = el("div", "metric");
    m.appendChild(el("span", "metric-label", label));
    m.appendChild(el("span", `metric-value${cls ? " " + cls : ""}`, value));
    metricsEl.appendChild(m);
  });
}

function renderPositions(equity) {
  const container = document.getElementById("positions-table");
  if (!equity || equity.length === 0) {
    container.innerHTML = '<div class="empty-msg">No open positions as of the latest snapshot.</div>';
    return;
  }
  const latest = equity[equity.length - 1];
  const rows = Object.entries(latest.positions || {}).map(([symbol, info]) => ({ symbol, ...info }));
  renderTable(container, [
    { key: "symbol", label: "Symbol" },
    { key: "quantity", label: "Quantity" },
    { key: "price", label: "Price" },
    { key: "unexplained_quantity", label: "Source" },
  ], rows, {
    quantity: (v) => fmtNum(v, 6),
    price: (v) => fmtNum(v, 4),
    // Balances a real exchange fetch found but this system's own trade
    // history couldn't fully explain (e.g. a testnet faucet grant) are
    // held and risk-managed like any real position, but their value is
    // deliberately excluded from equity/PnL - see
    // PortfolioManager._tracked_quantity. Flagged here so "why does this
    // symbol show a position I never bought" has an answer in the UI
    // itself, not just in a log line.
    unexplained_quantity: (v, row) => {
      const unexplained = v || 0;
      if (unexplained <= 0) return safeSpan("strategy", "badge badge-yes");
      const label = unexplained >= row.quantity ? "untracked balance" : "partly untracked";
      return safeSpan(label, "badge badge-warn");
    },
  });
}

function renderCircuitBreaker(cb) {
  const line = document.getElementById("circuit-breaker-status");
  if (!cb) {
    line.textContent = "No circuit breaker events recorded — never tripped.";
    line.className = "status-line";
    return;
  }
  if (cb.tripped) {
    line.textContent = `TRIPPED at ${fmtNairobi(cb.timestamp)} EAT — ${cb.reason}`;
    line.className = "status-line status-tripped";
  } else {
    line.textContent = `OK (last reset at ${fmtNairobi(cb.timestamp)} EAT)`;
    line.className = "status-line status-ok";
  }
}

function renderAlerts(alertRows) {
  const container = document.getElementById("alerts-table");
  if (!alertRows || alertRows.length === 0) {
    container.innerHTML = '<div class="empty-msg">No alerts raised yet.</div>';
    return;
  }
  const displayRows = [...alertRows].reverse(); // most recent first
  renderTable(container, [
    { key: "timestamp", label: "Time" },
    { key: "severity", label: "Severity" },
    { key: "category", label: "Category" },
    { key: "message", label: "Message" },
  ], displayRows, {
    severity: (v) => {
      const cls = v === "critical" ? "down-text" : v === "warning" ? "" : "up-text";
      return safeSpan(v, cls);
    },
  });
}

function renderDecisions(decisions) {
  const container = document.getElementById("decisions-table");
  const rows = (decisions || []).map((d) => ({
    ...d,
    regime: d.details && d.details.regime,
    champion: d.details && d.details.champion,
    probability_up: d.details && d.details.probability_up,
    market_condition: d.details && d.details.market_condition,
  }));
  renderTable(container, [
    { key: "timestamp", label: "Time (EAT)" },
    { key: "symbol", label: "Symbol" },
    { key: "signal", label: "Signal" },
    { key: "probability_up", label: "P(up)" },
    { key: "confidence", label: "Confidence" },
    { key: "regime", label: "Regime" },
    { key: "market_condition", label: "Condition" },
    { key: "risk_approved", label: "Risk OK" },
    { key: "risk_reason", label: "Risk reason" },
    { key: "action", label: "Action" },
    { key: "price", label: "Price" },
    { key: "quantity", label: "Qty" },
    { key: "champion", label: "Champion" },
  ], rows, {
    timestamp: (v) => fmtNairobi(v),
    confidence: (v) => (v === null || v === undefined ? "—" : fmtNum(v, 3)),
    probability_up: (v) => (v === null || v === undefined ? "—" : fmtNum(v, 3)),
    risk_approved: (v) => safeSpan(v ? "yes" : "no", `badge ${v ? "badge-yes" : "badge-no"}`),
    price: (v) => fmtNum(v, 4),
    signal: (v) => safeSpan(v, v === "BUY" ? "up-text" : v === "SELL" ? "down-text" : ""),
    market_condition: (v) => (v ? safeSpan(v, "down-text") : safeSpan("—", "")),
  });
}

function renderTrades(trades) {
  const container = document.getElementById("trades-table");
  const metricsEl = document.getElementById("trades-metrics");
  metricsEl.innerHTML = "";
  if (trades && trades.length) {
    const realized = trades.filter((t) => t.pnl !== null && t.pnl !== undefined);
    if (realized.length) {
      const sum = realized.reduce((acc, t) => acc + t.pnl, 0);
      const m = el("div", "metric");
      m.appendChild(el("span", "metric-label", "Cumulative realized PnL"));
      m.appendChild(el("span", `metric-value ${sum >= 0 ? "up" : "down"}`, `$${fmtNum(sum)}`));
      metricsEl.appendChild(m);
    }
  }
  renderTable(container, [
    { key: "timestamp", label: "Time (EAT)" },
    { key: "symbol", label: "Symbol" },
    { key: "side", label: "Side" },
    { key: "price", label: "Price" },
    { key: "quantity", label: "Qty" },
    { key: "pnl", label: "PnL" },
  ], trades, {
    timestamp: (v) => fmtNairobi(v),
    price: (v) => fmtNum(v, 4),
    pnl: (v) => (v === null || v === undefined ? "—" : safeSpan(`$${fmtNum(v)}`, v >= 0 ? "up-text" : "down-text")),
  });
}

function renderRetrainEvents(events) {
  const container = document.getElementById("retrain-table");
  // Backend stores metrics nested as {candidate: {...}, baseline: {...}} -
  // the candidate is the model just trained THIS retrain, baseline is
  // whatever was previously promoted (null if none yet), so a promotion
  // decision can be reviewed against what it actually had to beat.
  const rows = (events || []).map((e) => {
    const candidate = e.metrics && e.metrics.candidate;
    const baseline = e.metrics && e.metrics.baseline;
    return {
      ...e,
      median_sharpe: candidate && candidate.median_sharpe,
      pct_folds_profitable: candidate && candidate.pct_folds_profitable,
      num_folds: candidate && candidate.num_folds,
      baseline_sharpe: baseline && baseline.median_sharpe,
    };
  });
  renderTable(container, [
    { key: "timestamp", label: "Time (EAT)" },
    { key: "symbol", label: "Symbol" },
    { key: "promoted", label: "Promoted" },
    { key: "reason", label: "Reason" },
    { key: "median_sharpe", label: "Median Sharpe" },
    { key: "baseline_sharpe", label: "vs. baseline Sharpe" },
    { key: "pct_folds_profitable", label: "% Folds profitable" },
    { key: "num_folds", label: "Folds" },
  ], rows, {
    timestamp: (v) => fmtNairobi(v),
    // Rows recorded before this column existed (pre-migration) have no
    // symbol - shown honestly as unknown rather than blank.
    symbol: (v) => v || "—",
    promoted: (v) => safeSpan(v ? "yes" : "no", `badge ${v ? "badge-yes" : "badge-no"}`),
    median_sharpe: (v) => (v === null || v === undefined ? "—" : fmtNum(v, 3)),
    baseline_sharpe: (v) => (v === null || v === undefined ? "no prior model" : fmtNum(v, 3)),
    pct_folds_profitable: (v) => (v === null || v === undefined ? "—" : fmtNum(v * 100, 0) + "%"),
  });
}

function renderStabilityTable(containerId, rows) {
  const container = document.getElementById(containerId);
  if (!rows || rows.length === 0) {
    container.innerHTML = '<div class="empty-msg">No tournament runs recorded yet.</div>';
    return;
  }
  const displayRows = rows.map((r) => ({ ...r, current_winner: r.current_winner || "No Champion" }));
  renderTable(container, [
    { key: "symbol", label: "Symbol" },
    { key: "regime", label: "Regime" },
    { key: "current_winner", label: "Current winner" },
    { key: "num_runs", label: "Runs" },
    { key: "stability_score", label: "Stability" },
  ], displayRows, {
    current_winner: (v) => safeSpan(v, v === "No Champion" ? "down-text" : "up-text"),
    stability_score: (v) => {
      if (v === null || v === undefined) return "n/a (needs ≥2 runs)";
      const cls = v >= 0.8 ? "up-text" : v >= 0.5 ? "" : "down-text";
      return safeSpan(`${fmtNum(v * 100, 0)}%`, cls);
    },
  });
}

function renderTournament(stabilityRows) {
  renderStabilityTable("tournament-table", stabilityRows);
}

// ---- Live ticker tape (always visible, independent of active tab) ----
// Prices arrive over a direct browser->Binance WebSocket (see ws.js), not
// polling - this REST call only does the INITIAL paint (chip creation +
// starting values) before the first live tick arrives.
const tickerPrevPrices = {};
let BINANCE_TO_APP_SYMBOL = {};

function updateTickerChip(symbol, last, changePct) {
  const container = document.getElementById("ticker-tape");
  let chip = container.querySelector(`[data-symbol="${CSS.escape(symbol)}"]`);
  if (!chip) {
    chip = el("div", "ticker-chip");
    chip.dataset.symbol = symbol;
    chip.appendChild(el("span", "ticker-symbol", symbol.replace("/USDT", "")));
    chip.appendChild(el("span", "ticker-price"));
    chip.appendChild(el("span", "ticker-change"));
    container.appendChild(chip);
  }
  const priceEl = chip.querySelector(".ticker-price");
  const changeEl = chip.querySelector(".ticker-change");
  const prev = tickerPrevPrices[symbol];
  priceEl.textContent = fmtNum(last, last < 10 ? 4 : 2);
  changeEl.textContent = `${changePct >= 0 ? "+" : ""}${fmtNum(changePct)}%`;
  changeEl.className = `ticker-change ${changePct >= 0 ? "up-text" : "down-text"}`;

  if (prev !== undefined && last !== prev) {
    chip.classList.remove("flash-up", "flash-down");
    void chip.offsetWidth; // restart CSS animation
    chip.classList.add(last > prev ? "flash-up" : "flash-down");
    setTimeout(() => chip.classList.remove("flash-up", "flash-down"), 700);
  }
  tickerPrevPrices[symbol] = last;
}

async function loadTickerTape() {
  const markets = await getJSON("/api/markets?quote=USDT");
  const tracked = (SYSTEM_STATUS && SYSTEM_STATUS.symbols) || [];
  const bySymbol = new Map(markets.map((m) => [m.symbol, m]));
  const symbols = tracked.length ? tracked : markets.slice(0, 8).map((m) => m.symbol);

  BINANCE_TO_APP_SYMBOL = {};
  symbols.forEach((symbol) => {
    BINANCE_TO_APP_SYMBOL[symbol.replace("/", "").toUpperCase()] = symbol;
    const row = bySymbol.get(symbol);
    if (row) updateTickerChip(symbol, row.last, row.change_24h_pct);
  });

  return symbols;
}

function startLiveTickerFeed(symbols) {
  subscribeTickers(symbols, (update) => {
    const appSymbol = BINANCE_TO_APP_SYMBOL[update.symbol];
    if (appSymbol) updateTickerChip(appSymbol, update.last, update.changePct);
  });
}

// ---- Markets tab ----
let MARKET_ROWS = [];
let marketsView = "table";

async function loadMarkets() {
  MARKET_ROWS = await getJSON("/api/markets?quote=USDT");
  applyMarketFilters();
}

function applyMarketFilters() {
  const search = document.getElementById("market-search").value.trim().toUpperCase();
  const minVolume = parseFloat(document.getElementById("market-min-volume").value) || 0;
  const sortKey = document.getElementById("market-sort").value;
  const direction = document.getElementById("market-direction").value;

  let rows = MARKET_ROWS.filter((r) => {
    if (search && !r.symbol.toUpperCase().includes(search)) return false;
    if (minVolume > 0 && (r.quote_volume_24h || 0) < minVolume) return false;
    return true;
  });
  rows = rows.slice().sort((a, b) => {
    const av = a[sortKey] || 0, bv = b[sortKey] || 0;
    return direction === "asc" ? av - bv : bv - av;
  });

  document.getElementById("markets-count").textContent = `${rows.length} of ${MARKET_ROWS.length} pairs shown.`;

  const tracked = new Set((SYSTEM_STATUS && SYSTEM_STATUS.symbols) || []);
  const displayRows = rows.map((r) => ({ ...r, tracked: tracked.has(r.symbol) }));

  document.getElementById("markets-table").classList.toggle("hidden", marketsView !== "table");
  document.getElementById("markets-grid").classList.toggle("hidden", marketsView !== "grid");

  if (marketsView === "grid") {
    renderMarketsGrid(displayRows);
    return;
  }
  renderTable(document.getElementById("markets-table"), [
    { key: "symbol", label: "Symbol" },
    { key: "last", label: "Last" },
    { key: "change_24h_pct", label: "24h %" },
    { key: "quote_volume_24h", label: "24h Volume" },
    { key: "high_24h", label: "24h High" },
    { key: "low_24h", label: "24h Low" },
    { key: "tracked", label: "Tracked" },
  ], displayRows, {
    last: (v) => fmtNum(v, 4),
    high_24h: (v) => fmtNum(v, 4),
    low_24h: (v) => fmtNum(v, 4),
    quote_volume_24h: (v) => fmtNum(v, 0),
    change_24h_pct: (v) => safeSpan(`${v >= 0 ? "+" : ""}${fmtNum(v)}%`, v >= 0 ? "up-text" : "down-text"),
    tracked: (v) => safeSpan(v ? "yes" : "no", `badge ${v ? "badge-yes" : "badge-no"}`),
  });
}

// Reuses the exact filtered/sorted array applyMarketFilters already
// produces - the grid is a different LAYOUT of the same data, not a
// second source of truth.
function renderMarketsGrid(rows) {
  const container = document.getElementById("markets-grid");
  container.innerHTML = "";
  if (!rows || rows.length === 0) {
    container.appendChild(el("div", "empty-msg", "No markets match the current filters."));
    return;
  }
  rows.forEach((r) => {
    const card = el("div", "market-card");
    const head = el("div", "market-card-head");
    head.appendChild(el("span", "market-card-symbol", r.symbol));
    if (r.tracked) head.appendChild(el("span", "market-card-tracked", "TRACKED"));
    card.appendChild(head);
    card.appendChild(el("div", "market-card-price", fmtNum(r.last, r.last < 10 ? 4 : 2)));
    const changeCls = r.change_24h_pct >= 0 ? "up-text" : "down-text";
    card.appendChild(
      el("div", `market-card-change ${changeCls}`, `${r.change_24h_pct >= 0 ? "+" : ""}${fmtNum(r.change_24h_pct)}% (24h)`)
    );
    const foot = el("div", "market-card-foot");
    foot.appendChild(el("span", null, `Vol $${fmtNum(r.quote_volume_24h, 0)}`));
    card.appendChild(foot);
    container.appendChild(card);
  });
}

["market-search", "market-min-volume", "market-sort", "market-direction"].forEach((id) => {
  document.getElementById(id).addEventListener("input", applyMarketFilters);
  document.getElementById(id).addEventListener("change", applyMarketFilters);
});

document.querySelectorAll("#markets-view-toggle .view-toggle-btn").forEach((btn) => {
  btn.addEventListener("click", () => {
    marketsView = btn.dataset.view;
    document.querySelectorAll("#markets-view-toggle .view-toggle-btn").forEach((b) =>
      b.classList.toggle("active", b === btn)
    );
    applyMarketFilters();
  });
});

// ---- Paper Trading tab ----
let PAPER_ROWS = [];

async function loadPaperLeaderboard() {
  PAPER_ROWS = await getJSON("/api/paper-leaderboard");
  renderTable(document.getElementById("paper-leaderboard-table"), [
    { key: "symbol", label: "Symbol" },
    { key: "candidate", label: "Strategy" },
    { key: "total_equity", label: "Paper equity" },
    { key: "pnl_pct", label: "PnL %" },
    { key: "num_trades", label: "Trades" },
    { key: "timestamp", label: "Last updated (EAT)" },
  ], PAPER_ROWS, {
    total_equity: (v) => `$${fmtNum(v)}`,
    pnl_pct: (v) => safeSpan(`${v >= 0 ? "+" : ""}${fmtNum(v)}%`, v >= 0 ? "up-text" : "down-text"),
    timestamp: (v) => fmtNairobi(v),
  });

  const symbolSelect = document.getElementById("paper-symbol");
  const candidateSelect = document.getElementById("paper-candidate");
  const wasEmpty = symbolSelect.options.length === 0;
  if (wasEmpty) {
    const symbols = [...new Set(PAPER_ROWS.map((r) => r.symbol))];
    symbols.forEach((s) => {
      const opt = el("option", null, s);
      opt.value = s;
      symbolSelect.appendChild(opt);
    });
    symbolSelect.addEventListener("change", refreshPaperCandidateOptions);
    candidateSelect.addEventListener("change", () => loadPaperEquityChart(symbolSelect.value, candidateSelect.value));
    if (symbols.length) refreshPaperCandidateOptions();
  }
}

function refreshPaperCandidateOptions() {
  const symbolSelect = document.getElementById("paper-symbol");
  const candidateSelect = document.getElementById("paper-candidate");
  const candidates = PAPER_ROWS.filter((r) => r.symbol === symbolSelect.value).map((r) => r.candidate);
  candidateSelect.innerHTML = "";
  candidates.forEach((c) => {
    const opt = el("option", null, c);
    opt.value = c;
    candidateSelect.appendChild(opt);
  });
  if (candidates.length) loadPaperEquityChart(symbolSelect.value, candidateSelect.value);
}

// ---- Auto-refresh controller ----
// A single coordinator for every polled panel, instead of the scattered,
// independent setInterval calls this dashboard used to have. Three things
// a professional dashboard needs that plain setInterval doesn't give you:
//   1. Never overlap a task's own fetches - a slow response must not let
//      the next tick pile another request on top of one still in flight.
//   2. Pause entirely while the tab is hidden - no point spending API
//      calls (or the exchange's rate limit) refreshing a page nobody is
//      looking at - and catch up immediately, not on the next stale tick,
//      the moment it becomes visible again.
//   3. Surface actual refresh health in the UI (a pulsing "live" dot, an
//      "updated Xs ago" readout, a visible failure state) rather than
//      silently succeeding or failing in the background.
// Briefly highlights every panel in the currently-visible tab after a
// successful refresh - reuses the existing fadeIn keyframe's spirit
// (data-updated is its own CSS animation, respects reduced-motion).
function flashActivePanels() {
  const activeTab = document.querySelector(".tab-panel.active");
  if (!activeTab) return;
  activeTab.querySelectorAll(".panel").forEach((p) => {
    p.classList.remove("data-updated");
    void p.offsetWidth;
    p.classList.add("data-updated");
  });
}

const AutoRefresh = (() => {
  const tasks = [];
  let paused = false;
  let lastSuccessAt = null;

  function register(name, intervalMs, fn) {
    tasks.push({ name, intervalMs, fn, lastRun: 0, inFlight: false });
  }

  async function runTask(task) {
    if (task.inFlight) return;
    task.inFlight = true;
    try {
      await task.fn();
      lastSuccessAt = Date.now();
      if (!paused) setStatus("ok");
      flashActivePanels();
    } catch (err) {
      console.error(`[refresh:${task.name}]`, err);
      setStatus("error");
    } finally {
      task.inFlight = false;
      task.lastRun = Date.now();
    }
  }

  function tick() {
    if (paused || document.hidden) return;
    const now = Date.now();
    tasks.forEach((task) => {
      if (now - task.lastRun >= task.intervalMs) runTask(task);
    });
  }

  function refreshAllNow() {
    tasks.forEach(runTask);
  }

  function setPaused(next) {
    paused = next;
    setStatus(paused ? "paused" : "ok");
  }

  function setStatus(state) {
    const dot = document.getElementById("refresh-dot");
    const text = document.getElementById("refresh-text");
    if (!dot || !text) return;
    dot.classList.remove("stale", "error");
    if (state === "error") {
      dot.classList.add("error");
      text.textContent = "Update failed - retrying";
    } else if (state === "paused") {
      dot.classList.add("stale");
      text.textContent = "Paused";
    } else if (document.hidden) {
      dot.classList.add("stale");
      text.textContent = "Idle (tab hidden)";
    } else {
      text.textContent = lastSuccessAt ? "Live" : "Connecting…";
    }
  }

  // Keeps the "Updated Xs ago" readout moving between actual refreshes,
  // without that being what decides the dot's color/state.
  function tickAge() {
    if (paused || document.hidden || !lastSuccessAt) return;
    const text = document.getElementById("refresh-text");
    if (!text) return;
    const secs = Math.round((Date.now() - lastSuccessAt) / 1000);
    text.textContent = secs < 2 ? "Updated just now" : `Updated ${secs}s ago`;
  }

  document.addEventListener("visibilitychange", () => {
    setStatus(paused ? "paused" : "ok");
    if (!document.hidden && !paused) refreshAllNow();
  });

  setInterval(tick, 1000);
  setInterval(tickAge, 1000);

  return {
    register,
    refreshAllNow,
    setPaused,
    get paused() { return paused; },
  };
})();

document.getElementById("refresh-now-btn").addEventListener("click", () => {
  const icon = document.getElementById("refresh-icon");
  icon.classList.remove("spinning");
  void icon.offsetWidth; // restart the animation even if it just played
  icon.classList.add("spinning");
  AutoRefresh.refreshAllNow();
});

document.getElementById("refresh-toggle-btn").addEventListener("click", (event) => {
  const btn = event.currentTarget;
  const next = !AutoRefresh.paused;
  AutoRefresh.setPaused(next);
  btn.textContent = next ? "Resume" : "Pause";
  btn.title = next ? "Resume auto-refresh" : "Pause auto-refresh";
  btn.setAttribute("aria-pressed", String(next));
});

document.getElementById("sidebar-collapse-btn").addEventListener("click", (event) => {
  const sidebar = document.getElementById("sidebar");
  const collapsed = sidebar.classList.toggle("collapsed");
  event.currentTarget.setAttribute("aria-expanded", String(!collapsed));
});

// ---- Boot ----
async function boot() {
  // Only for containers likely visible on first paint (Overview tab is the
  // default active one) - real render functions overwrite these the
  // moment their own fetch resolves, so a fast connection barely sees them.
  ["positions-table", "alerts-table", "decisions-table", "markets-table"].forEach((id) => showSkeleton(id));

  await loadStatus();
  await loadHealth();
  await loadOverview();
  await loadMarkets();
  const tickerSymbols = await loadTickerTape();
  await initChartTab(); // defined in chart.js

  // Live prices from here on: a direct browser->Binance WebSocket, not
  // polling our own backend (see ws.js). Zero backend load, sub-second
  // latency instead of a 3-30s poll+cache round trip.
  initLiveFeed(SYSTEM_STATUS.ws_base_url);
  startLiveTickerFeed(tickerSymbols);

  await loadPaperLeaderboard();
  await loadReadinessTab();
  document.getElementById("footer-text").textContent =
    `Dashboard connected · ${fmtNairobi(new Date().toISOString())} EAT`;

  // Registered, not fired directly - AutoRefresh's own 1s tick loop owns
  // scheduling for all of these from here on (see the controller above).
  AutoRefresh.register("overview", 15000, loadOverview);
  AutoRefresh.register("markets", 15000, loadMarkets);
  // Slow reconciliation poll, not the primary price-update path - a
  // safety net in case the WebSocket silently drops, or the tracked
  // symbol set changes.
  AutoRefresh.register("ticker-tape", 60000, loadTickerTape);
  AutoRefresh.register("paper-leaderboard", 10000, loadPaperLeaderboard);
  AutoRefresh.register("readiness", 30000, loadReadinessTab);
  AutoRefresh.register("health", 10000, loadHealth);
}

boot().catch((err) => {
  console.error(err);
  document.getElementById("footer-text").textContent = `Connection error: ${err.message}`;
  const text = document.getElementById("refresh-text");
  if (text) text.textContent = "Connection error";
  const dot = document.getElementById("refresh-dot");
  if (dot) dot.classList.add("error");
});
