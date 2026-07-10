# Differentiated Signal Research

Compiled 2026-07-10 in response to a review that flagged the live system's
core edge as thin: promoted models' raw `probability_up` output clusters
in ~0.40-0.59 (mean 0.45, stdev ~0.06) - barely off the coin-flip line.
The tournament's 6 candidates (`ml_ensemble`, `sma_crossover`,
`rsi_mean_reversion`, `macd`, `bollinger_bands`, `candlestick_patterns`,
now also `time_series_momentum`) are all variations on one data source:
OHLCV price/volume history from Binance's REST API. This doc surveys what
the wider trading-research literature says works, cross-checks it against
what's actually feasible given this system's current data pipeline, and
proposes a prioritized roadmap for closing the gap.

## What the literature says

**Generic TA + ML on price data alone has a real but modest ceiling.**
Classification accuracy on crypto daily/minute-level direction typically
lands around 55-65%, and even well-specified return-forecasting models
report out-of-sample R^2 in the low single digits (~4.9% in one study),
driven mostly by a short-term reversal effect rather than a durable
directional edge
([PMC12571449](https://pmc.ncbi.nlm.nih.gov/articles/PMC12571449/),
[ResearchGate: Daily Cryptocurrency Returns Forecasting](https://www.researchgate.net/publication/357308204_Daily_Cryptocurrency_Returns_Forecasting_and_Trading_via_Machine_Learning),
[ScienceDirect: Forecasting cryptocurrency returns with ML](https://www.sciencedirect.com/science/article/abs/pii/S0275531923000314)).
This system's own observed 0.40-0.59 probability spread is consistent
with that ceiling, not unusually bad - it's roughly what the literature
says price-only ML should be expected to produce.

**Order-book / microstructure features are the strongest documented edge
- and the one this system can't currently use.** One recent ML study
found order-book-derived features made up 81.3% of the features selected
for direction prediction, dominating price-based signals
([MDPI 2076-3417/15/20/11145](https://www.mdpi.com/2076-3417/15/20/11145)).
Other 2025-2026 work on cross-asset LOB microstructure patterns and
hierarchical/transfer learning across crypto assets reinforces this
([arXiv:2506.05764](https://arxiv.org/html/2506.05764v2),
[Frontiers in Blockchain 10.3389/fbloc.2026.1811716](https://www.frontiersin.org/journals/blockchain/articles/10.3389/fbloc.2026.1811716/full),
[arXiv:2602.00776](https://arxiv.org/html/2602.00776v1)). But this
system's own `agents/features.py` docstring already correctly identifies
why this is unavailable today: *"Binance's REST API only exposes the
CURRENT order book and recent trades, with no historical snapshot
endpoint, so those signals can't be reconstructed for historical training
bars without data leakage."* One practitioner writeup is a useful cautionary
note on top of that: a 2012-microstructure-paper-derived BTC signal that
"worked... but is dying" as the edge gets arbitraged away
([Medium/Coinmonks](https://medium.com/coinmonks/i-used-a-2012-market-microstructure-paper-to-find-alpha-in-btc-it-worked-but-its-dying-500f9bc0fc94)) -
microstructure edges decay fast once documented, so this is not a
guaranteed win even if the data gap were closed.

**Time-series momentum is the one effect that specifically survives
realistic crypto trading costs.** Han, Kang & Ryu (2023) directly tested
both time-series and cross-sectional momentum in crypto under realistic
assumptions (transaction costs, daily fluctuations) and found: *"evidence
of time-series momentum is strong, whereas evidence of cross-sectional
momentum is weak... many [cross-sectional] momentum portfolios are
liquidated and many with statistically significant returns earn
insignificant profits"* once costs are applied
([SSRN 4675565](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4675565)).
This is directly actionable with data this system already has (OHLCV) -
see "What we did this session" below.

**Funding rate / perpetual basis is a genuinely different data source,
already reachable via the same ccxt/Binance stack.** Persistently elevated
positive funding indicates crowded long positioning; funding flipping
sign is a documented contrarian signal; combining funding trend with open
interest trend sharpens it further (climbing funding + climbing OI =
"textbook overcrowded long," often followed by a 5-10% correction within
days) ([Kraken: funding rates as a trading signal](https://www.kraken.com/learn/futures-trading-funding-rate-strategy),
[BingX: funding rate explained](https://bingx.com/en/learn/article/what-is-funding-rate-and-how-use-it-in-crypto-trading)).
This is a cross-market signal (derivatives positioning informing a spot
decision) rather than a same-market microstructure signal, so it doesn't
have the same historical-data gap order-book signals do - Binance exposes
funding rate history for USDⓈ-M perpetuals via a standard REST endpoint,
which ccxt wraps as `fetch_funding_rate_history`.

**On-chain data (exchange flows, whale movements) is directionally
plausible but requires a wholesale new data source.** Large exchange
inflows signal selling pressure; outflows signal accumulation; whale
transfers to exchanges often precede downward moves
([Blofin: Whale Watching](https://blofin.com/en/academy/education/whale-watching-on-chain),
[Nansen: tracking whale movements](https://nansen.ai/post/mastering-onchain-analytics-how-to-use-blockchain-data-to-identify-token-whale-movements)).
Unlike funding rate, this isn't reachable through the exchange API this
system already talks to - it requires integrating a blockchain-analytics
provider (Glassnode, Nansen, CryptoQuant), most of which are paid, and a
new ingestion/storage layer analogous to what OHLCV already has. Real
signal, real integration project, not a quick add.

## Validated against this system's actual constraints

| Signal family | Data already available? | New integration needed | Live-trading risk to add |
|---|---|---|---|
| Time-series momentum | Yes (OHLCV) | None | None - just a new rule candidate |
| Funding rate / basis | No (spot-only OHLCV feed) | ccxt call to the USDⓈ-M perp market for the same base asset; symbol-mapping "BTC/USDT" -> "BTC/USDT:USDT" | Low if added as an observation-only tournament candidate first, same as everything else here |
| Order-book microstructure | No - and can't be backfilled historically (Binance REST has no historical LOB snapshot endpoint) | A live order-book logger that runs for weeks/months before there's enough history to even walk-forward validate against | None until there's data to train on - this is a data-collection project, not a code change |
| On-chain / exchange flow | No | A paid third-party API (Glassnode/Nansen/CryptoQuant) plus a new ingestion pipely | Low once integrated and run through the same tournament gate as everything else |
| Cross-sectional momentum across the discovered symbol universe | Partially (OHLCV per symbol already fetched) | A cross-symbol ranking mechanism - current architecture treats every symbol independently, no shared per-cycle ranking step exists | Structural change to Orchestrator's per-symbol loop, not a drop-in candidate |

## What we did this session

Added `TimeSeriesMomentumStrategy` (`trading_assistant/strategies/implementations.py`)
as a new tournament candidate (`time_series_momentum` in
`StrategyManager.default_candidates()`) - the one effect the literature
specifically flags as surviving realistic crypto trading costs, and it
required zero new data sources. It only affects walk-forward validation
and paper trading right now (`tournament.drive_live_trading: false` in
`config.yaml`); it earns a live decision the same way every other
candidate does, by clearing the significance-tested promotion bar in
`validation/significance.py`.

## Recommended next steps, in priority order

1. **Let the new `time_series_momentum` candidate accumulate tournament
   runs** (already wired, zero further action needed) - see whether it
   clears the promotion bar in any regime, and whether `stability_score()`
   shows it's not just noise.
2. **Funding rate as a new tournament candidate.** Genuinely new data,
   reachable through the existing exchange stack, no historical-data gap.
   Needs: a `fetch_funding_rate_history`-based feed analogous to
   `market_data/feed.py`'s `LiveFeed`, a spot-symbol -> perp-symbol
   mapping, and a rule candidate (e.g. funding-rate z-score + trend as a
   contrarian filter on top of existing signals). Should go through the
   same observation-only tournament gate before ever being considered for
   `drive_live_trading`.
3. **Start logging the live order book now, even with no immediate use.**
   The single biggest documented edge (microstructure) is foreclosed by a
   historical-data gap, not a modeling gap. A background process that
   appends order-book snapshots to the journal starting today means that
   in 3-6 months there's finally enough history to walk-forward validate
   against - waiting until "we're ready to build this" just pushes the
   earliest possible validation date out further.
4. **On-chain data integration** - highest setup cost (new paid API,
   new ingestion layer), evaluate only after 2-3 are further along.

## Sources

- [Evaluating machine learning models for predictive accuracy in cryptocurrency price forecasting (PMC)](https://pmc.ncbi.nlm.nih.gov/articles/PMC12571449/)
- [Daily Cryptocurrency Returns Forecasting and Trading via Machine Learning (ResearchGate)](https://www.researchgate.net/publication/357308204_Daily_Cryptocurrency_Returns_Forecasting_and_Trading_via_Machine_Learning)
- [Forecasting cryptocurrency returns with machine learning (ScienceDirect)](https://www.sciencedirect.com/science/article/abs/pii/S0275531923000314)
- [Machine Learning Analytics for Blockchain-Based Financial Markets: A Confidence-Threshold Framework (MDPI)](https://www.mdpi.com/2076-3417/15/20/11145)
- [Exploring Microstructural Dynamics in Cryptocurrency Limit Order Books (arXiv:2506.05764)](https://arxiv.org/html/2506.05764v2)
- [Microstructure alpha: hierarchical learning and cross-asset transfer in cryptocurrency markets (Frontiers in Blockchain)](https://www.frontiersin.org/journals/blockchain/articles/10.3389/fbloc.2026.1811716/full)
- [Explainable Patterns in Cryptocurrency Microstructure (arXiv:2602.00776)](https://arxiv.org/html/2602.00776v1)
- [I Used a 2012 Market Microstructure Paper to Find Alpha in BTC. It Worked - But It's Dying (Medium/Coinmonks)](https://medium.com/coinmonks/i-used-a-2012-market-microstructure-paper-to-find-alpha-in-btc-it-worked-but-its-dying-500f9bc0fc94)
- [Time-Series and Cross-Sectional Momentum in the Cryptocurrency Market (Han, Kang & Ryu, SSRN 4675565)](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4675565)
- [Cryptocurrency Factor Momentum (Quantitative Finance)](https://www.tandfonline.com/doi/abs/10.1080/14697688.2023.2269999)
- [Funding rates as a trading signal: how to use them strategically (Kraken)](https://www.kraken.com/learn/futures-trading-funding-rate-strategy)
- [Crypto Futures Funding Rate Explained (BingX)](https://bingx.com/en/learn/article/what-is-funding-rate-and-how-use-it-in-crypto-trading)
- [Whale Watching: On-Chain Signals for Crypto Traders (Blofin)](https://blofin.com/en/academy/education/whale-watching-on-chain)
- [Tracking token whale movements (Nansen)](https://nansen.ai/post/mastering-onchain-analytics-how-to-use-blockchain-data-to-identify-token-whale-movements)
