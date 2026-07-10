// Live market data via a direct browser -> Binance WebSocket connection.
//
// Why: every other part of this dashboard reads OUR OWN computed state
// (decisions, trades, paper bankrolls) via REST polling, which is correct -
// that data only changes when a cycle runs. But raw market price is Binance's
// data, not ours; polling our own backend for it just adds a hop and a cache
// TTL on top of Binance's own latency. Connecting the browser straight to
// Binance's public stream removes both, and costs our backend nothing since
// it's a fully client-side connection, not something the server sits in
// front of.
//
// One shared connection is used for everything (ticker tape + chart live
// updates), managed via Binance's SUBSCRIBE/UNSUBSCRIBE control messages
// rather than reconnecting for every symbol/timeframe change.

let _ws = null;
let _wsBaseUrl = null;
let _reconnectAttempt = 0;
let _reconnectTimer = null;
let _subscribedStreams = new Set();
let _tickerHandler = null;
let _klineHandler = null;
let _currentKlineStream = null;

function _binanceSymbol(symbol) {
  return symbol.replace("/", "").toLowerCase(); // "BTC/USDT" -> "btcusdt"
}

function _send(payload) {
  if (_ws && _ws.readyState === WebSocket.OPEN) {
    _ws.send(JSON.stringify(payload));
  }
}

function _subscribe(streams) {
  const toAdd = streams.filter((s) => !_subscribedStreams.has(s));
  if (toAdd.length === 0) return;
  toAdd.forEach((s) => _subscribedStreams.add(s));
  _send({ method: "SUBSCRIBE", params: toAdd, id: Date.now() });
}

function _unsubscribe(streams) {
  const toRemove = streams.filter((s) => _subscribedStreams.has(s));
  if (toRemove.length === 0) return;
  toRemove.forEach((s) => _subscribedStreams.delete(s));
  _send({ method: "UNSUBSCRIBE", params: toRemove, id: Date.now() });
}

function _handleMessage(event) {
  let data;
  try {
    data = JSON.parse(event.data);
  } catch {
    return;
  }
  if (data.result !== undefined || data.id !== undefined) return; // SUBSCRIBE/UNSUBSCRIBE ack, not market data

  if (data.e === "24hrTicker" && _tickerHandler) {
    _tickerHandler({
      symbol: data.s, // e.g. "BTCUSDT" (no slash - caller must match against its own symbol formatting)
      last: parseFloat(data.c),
      changePct: parseFloat(data.P),
    });
  } else if (data.e === "kline" && _klineHandler) {
    const k = data.k;
    _klineHandler({
      symbol: data.s,
      interval: k.i,
      time: Math.floor(k.t / 1000),
      open: parseFloat(k.o),
      high: parseFloat(k.h),
      low: parseFloat(k.l),
      close: parseFloat(k.c),
      volume: parseFloat(k.v),
      isClosed: k.x,
    });
  }
}

function _connect() {
  _ws = new WebSocket(_wsBaseUrl);

  _ws.onopen = () => {
    _reconnectAttempt = 0;
    // Re-subscribe everything on reconnect - a fresh socket has no memory
    // of prior subscriptions even though our own bookkeeping does.
    const streams = Array.from(_subscribedStreams);
    _subscribedStreams.clear();
    _subscribe(streams);
  };

  _ws.onmessage = _handleMessage;

  _ws.onclose = () => {
    const delay = Math.min(1000 * Math.pow(2, _reconnectAttempt), 15000);
    _reconnectAttempt += 1;
    _reconnectTimer = setTimeout(_connect, delay);
  };

  _ws.onerror = () => {
    _ws.close();
  };
}

function initLiveFeed(wsBaseUrl) {
  _wsBaseUrl = wsBaseUrl;
  _connect();
}

function subscribeTickers(symbols, handler) {
  _tickerHandler = handler;
  _subscribe(symbols.map((s) => `${_binanceSymbol(s)}@ticker`));
}

function setKlineSubscription(symbol, timeframe, handler) {
  _klineHandler = handler;
  const newStream = `${_binanceSymbol(symbol)}@kline_${timeframe}`;
  if (_currentKlineStream && _currentKlineStream !== newStream) {
    _unsubscribe([_currentKlineStream]);
  }
  if (_currentKlineStream !== newStream) {
    _subscribe([newStream]);
    _currentKlineStream = newStream;
  }
}

function clearKlineSubscription() {
  if (_currentKlineStream) {
    _unsubscribe([_currentKlineStream]);
    _currentKlineStream = null;
  }
}
