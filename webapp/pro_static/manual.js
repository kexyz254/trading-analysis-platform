(() => {
  let symbol = 'BTC/USDT';
  let csrfToken = null;
  const $ = id => document.getElementById(id);

  const money = value =>
    value == null ? '—' :
    Number(value).toLocaleString(undefined,{maximumFractionDigits:8});

  async function request(url, options={}) {
    const response = await fetch(url, {
      ...options,
      headers: {'Content-Type':'application/json', ...(options.headers||{})}
    });

    if (!response.ok) {
      const body = await response.json().catch(() => ({}));
      throw new Error(body.detail || `HTTP ${response.status}`);
    }
    return response.json();
  }

  async function loadStatus() {
    const data = await request('/api/pro/manual/status');
    $('authority-badge').textContent =
      `${data.execution_mode.toUpperCase()} · READ ONLY`;
  }

  async function loadMarket() {
    $('selected-symbol').textContent = symbol;

    const data = await request(
      `/api/pro/manual/quote/${encodeURIComponent(symbol)}`
    );

    const t = data.ticker || {};
    $('last-price').textContent = money(t.last);
    $('bid-ask').textContent = `${money(t.bid)} / ${money(t.ask)}`;

    if (t.bid && t.ask) {
      const mid = (Number(t.bid)+Number(t.ask))/2;
      $('spread').textContent =
        (((Number(t.ask)-Number(t.bid))/mid)*100).toFixed(5)+'%';
    } else {
      $('spread').textContent = '—';
    }

    loadIntelligence();
  }

  async function loadIntelligence() {
    const encoded = encodeURIComponent(symbol);

    const [prediction, interpreter, position] =
      await Promise.allSettled([
        request(`/api/pro/prediction/${encoded}`),
        request(`/api/pro/market-interpreter/${encoded}`),
        request(`/api/pro/position/${encoded}`)
      ]);

    if (prediction.status === 'fulfilled') {
      const p = prediction.value || {};
      $('prediction').textContent =
        p.signal || p.prediction || p.direction || 'No signal';
      const c = p.confidence;
      $('confidence').textContent =
        c == null ? '—' : `${(Number(c)*100).toFixed(1)}%`;
    } else {
      $('prediction').textContent = 'Unavailable';
      $('confidence').textContent = '—';
    }

    if (interpreter.status === 'fulfilled') {
      const x = interpreter.value || {};
      $('interpretation').textContent =
        x.interpretation ||
        x.market_state ||
        x.summary ||
        'Available';
    } else {
      $('interpretation').textContent = 'Unavailable';
    }

    if (position.status === 'fulfilled') {
      const p = position.value;
      $('position').textContent =
        p && Object.keys(p).length ? 'Position detected' : 'Flat';
    } else {
      $('position').textContent = 'Unavailable';
    }
  }

  let searchTimer;

  $('market-search').addEventListener('input', event => {
    clearTimeout(searchTimer);

    searchTimer = setTimeout(async () => {
      const q = event.target.value.trim();
      if (!q) {
        $('market-results').innerHTML = '';
        return;
      }

      try {
        const markets = await request(
          `/api/pro/manual/markets?q=${encodeURIComponent(q)}&quote=USDT&limit=30`
        );

        $('market-results').innerHTML = markets.map(m =>
          `<div class="market-result" data-symbol="${m.symbol}">
             <strong>${m.symbol}</strong>
             <span> · min ${m.min_notional ?? '—'} ${m.quote}</span>
           </div>`
        ).join('');
      } catch (error) {
        $('market-results').textContent = error.message;
      }
    }, 180);
  });

  $('market-results').addEventListener('click', event => {
    const row = event.target.closest('[data-symbol]');
    if (!row) return;

    symbol = row.dataset.symbol;
    $('market-search').value = symbol;
    $('market-results').innerHTML = '';

    loadMarket().catch(console.error);
  });

  $('order-type').addEventListener('change', event => {
    $('limit-price-wrap').hidden = event.target.value !== 'limit';
  });

  $('preview-button').addEventListener('click', async () => {
    try {
      const payload = {
        request_id: crypto.randomUUID(),
        symbol,
        side: $('side').value,
        order_type: $('order-type').value,
        quantity: Number($('quantity').value)
      };

      if (payload.order_type === 'limit') {
        payload.limit_price = Number($('limit-price').value);
      }

      const data = await request('/api/pro/manual/prepare', {
        method:'POST',
        headers:{'X-CSRF-Token': csrfToken},
        body:JSON.stringify(payload)
      });

      const p = data.preview;
      const r = data.risk;

      $('preview-empty').hidden = true;
      $('preview-data').hidden = false;

      $('p-qty').textContent = money(p.quantity);
      $('p-price').textContent = money(p.estimated_price);
      $('p-notional').textContent = money(p.estimated_notional);
      $('p-balance').textContent =
        `${money(p.available_quote)} ${p.quote_currency}`;
      $('p-spread').textContent =
        p.spread_pct == null ? '—' :
        `${(Number(p.spread_pct)*100).toFixed(5)}%`;

      $('p-decision').textContent =
        r.approved ? 'PREVIEW CLEARED' : 'BLOCKED';

      $('blockers').textContent =
        (r.blockers || []).join(' · ');

      $('warnings').textContent =
        (r.warnings || []).join(' · ');
    } catch (error) {
      $('preview-empty').hidden = false;
      $('preview-data').hidden = true;
      $('preview-empty').textContent = error.message;
    }
  });

  request('/api/pro/auth/csrf')
    .then(data => { csrfToken = data.csrf_token; })
    .then(loadStatus)
    .then(loadMarket)
    .catch(error => {
      $('authority-badge').textContent = 'WORKSPACE UNAVAILABLE';
      console.error(error);
    });
})();
