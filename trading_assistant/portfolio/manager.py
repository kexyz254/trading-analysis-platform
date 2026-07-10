"""Portfolio management and allocation."""

import pandas as pd
import numpy as np
from datetime import date
from typing import Any, Dict, List, Tuple, Optional


def max_drawdown_from_equity_curve(equity_curve: List[float]) -> float:
    """Pure function version of PortfolioManager.get_max_drawdown - operates
    on any equity curve (e.g. one reconstructed from persisted journal
    snapshots for a portfolio that isn't live in this process), not just
    self.equity_curve. Single source of truth for the drawdown formula."""
    if len(equity_curve) < 2:
        return 0.0
    peak = equity_curve[0]
    max_dd = 0.0
    for value in equity_curve:
        if value > peak:
            peak = value
        if peak > 0:
            dd = (peak - value) / peak
            if dd > max_dd:
                max_dd = dd
    return max_dd


def sharpe_ratio_from_equity_curve(equity_curve: List[float], risk_free_rate: float = 0.02) -> float:
    """Pure function version of PortfolioManager.get_sharpe_ratio - see
    max_drawdown_from_equity_curve's docstring for why this is extracted."""
    if len(equity_curve) < 2:
        return 0.0
    returns = []
    for i in range(1, len(equity_curve)):
        if equity_curve[i - 1] > 0:
            ret = (equity_curve[i] - equity_curve[i - 1]) / equity_curve[i - 1]
            returns.append(ret)
    if len(returns) < 2:
        return 0.0
    returns_array = np.array(returns)
    excess_returns = returns_array - risk_free_rate / 252
    mean_return = np.mean(excess_returns)
    std_return = np.std(excess_returns)
    # A genuinely flat (or near-flat) equity curve - e.g. a freshly-started
    # paper-trading candidate with zero trades - should read as "no signal
    # yet" (Sharpe 0.0), not as an extreme value. np.std's internal mean-
    # then-subtract computation on a set of IDENTICAL floats can leave a
    # tiny (~1e-20) non-zero residual from floating-point summation order,
    # not real volatility - `== 0` missed this, dividing by ~1e-20 and
    # producing a nonsense Sharpe in the tens of quadrillions. 1e-12 is
    # many orders of magnitude above that noise floor and many orders
    # below any real return series' actual volatility (returns are
    # typically ~0.001-0.1 in magnitude).
    if std_return < 1e-12:
        return 0.0
    return mean_return / std_return * np.sqrt(252)


def wilson_lower_bound(successes: int, n: int, z: float = 1.645) -> float:
    """Lower bound of the Wilson score confidence interval for a binomial
    proportion (e.g. a win rate) - a shrinkage estimate that's
    appropriately conservative with a small/noisy sample (wide interval,
    low lower bound) and converges toward the raw point estimate as the
    sample grows (interval narrows). Used in place of a raw win_rate point
    estimate wherever that estimate feeds a sizing decision (see
    RiskAgent._kelly_position_scale): a small sample's point estimate can
    overstate the true edge, and Kelly sizing is exactly the wrong place
    to compound that overconfidence with a number that's too optimistic.

    z=1.645 is the one-sided 95% bound - deliberately one-sided, since a
    sizing cap should only ever get MORE conservative from uncertainty,
    never more aggressive; the interval's upper bound is irrelevant here.
    Standard technique for small-n binomial proportions - preferred over a
    naive normal approximation, which can't be trusted at small n and can
    produce nonsensical bounds outside [0, 1]."""
    if n <= 0:
        return 0.0
    p_hat = successes / n
    denominator = 1 + z ** 2 / n
    center = (p_hat + z ** 2 / (2 * n)) / denominator
    margin = z * ((p_hat * (1 - p_hat) / n + z ** 2 / (4 * n ** 2)) ** 0.5) / denominator
    return max(0.0, center - margin)


def performance_summary_from_trade_pnls(trades_pnl: List[float]) -> Dict[str, Any]:
    """Pure function version of PortfolioManager.get_performance_summary's
    trade-level stats - see max_drawdown_from_equity_curve's docstring."""
    if not trades_pnl:
        return {}
    winning_trades = [p for p in trades_pnl if p > 0]
    losing_trades = [p for p in trades_pnl if p < 0]
    return {
        "total_trades": len(trades_pnl),
        "winning_trades": len(winning_trades),
        "losing_trades": len(losing_trades),
        "win_rate": len(winning_trades) / len(trades_pnl) if trades_pnl else 0,
        "total_pnl": sum(trades_pnl),
        "avg_win": np.mean(winning_trades) if winning_trades else 0,
        "avg_loss": np.mean(losing_trades) if losing_trades else 0,
        # None (JSON null), not float("inf") - this dict gets returned
        # directly as part of a JSON API response (see webapp/data_access.py
        # real_portfolio_metrics), and Starlette's JSONResponse serializes
        # with allow_nan=False, so a literal inf raises ValueError and 500s
        # the whole endpoint. Found live: exactly this happened once every
        # completed real trade netted $0.00 PnL (no losers), which zeroes
        # out `losing_trades` here. None is also the more honest value -
        # "no losses yet" makes the ratio undefined, not infinite.
        "profit_factor": (sum(winning_trades) / abs(sum(losing_trades))) if losing_trades else None,
    }


class PortfolioManager:
    """Manage portfolio positions and risk."""

    # Both equity_curve and trade_history are appended to every cycle - for
    # the real portfolio AND every paper-trading candidate's own
    # PortfolioManager (ChampionChallengerAgent holds one per symbol per
    # candidate). With no cap, a 24/7 process leaks memory slowly forever.
    # The journal already durably persists the FULL history
    # (equity_snapshots/paper_equity_snapshots/trades tables) and every
    # Sharpe/drawdown/readiness calculation already reads from THAT, not
    # this in-memory list - so trimming the oldest entries here costs
    # nothing real. 10000 is deliberately generous: at one append per
    # cycle, that's ~34 days in-memory even at the default 5-minute poll
    # interval, far more than any live metric needs.
    MAX_HISTORY_LENGTH = 10000

    def __init__(self, initial_capital: float = 100000.0, commission_rate: float = 0.0, slippage_rate: float = 0.0):
        self.initial_capital = initial_capital
        self.capital = initial_capital
        self.positions: Dict[str, Dict] = {}
        self.trade_history: List[Dict] = []
        self.equity_curve: List[float] = [initial_capital]
        # Both default to 0.0 (identical to pre-friction behavior) - opt-in
        # per instance so existing callers/tests are unaffected unless they
        # deliberately ask for realistic friction. Mirrors BacktestEngineV2's
        # own commission/slippage formulas (see backtesting/framework.py)
        # so a live/paper portfolio and a walk-forward backtest agree on
        # what "with friction" actually costs, rather than each inventing
        # its own assumption.
        self.commission_rate = commission_rate
        self.slippage_rate = slippage_rate

    def append_equity_point(self, value: float) -> None:
        """The one path every caller (internal mark-to-market, and
        external callers like ChampionChallengerAgent/PortfolioAgent that
        previously appended straight to portfolio.equity_curve) should use
        to add to the equity curve - keeps it bounded regardless of who's
        appending, rather than every call site needing to remember to trim."""
        self.equity_curve.append(value)
        if len(self.equity_curve) > self.MAX_HISTORY_LENGTH:
            del self.equity_curve[: len(self.equity_curve) - self.MAX_HISTORY_LENGTH]

    def open_position(self, symbol: str, price: float, quantity: float,
                      stop_loss: Optional[float] = None,
                      take_profit: Optional[float] = None) -> bool:
        # Buying: slippage moves the fill price against you (higher).
        execution_price = price * (1 + self.slippage_rate)
        notional = execution_price * quantity
        commission_cost = notional * self.commission_rate
        total_cost = notional + commission_cost
        if total_cost > self.capital:
            return False
        self.positions[symbol] = {
            'price': execution_price,
            'quantity': quantity,
            'cost': total_cost,
            'stop_loss': stop_loss,
            'take_profit': take_profit,
            'timestamp': pd.Timestamp.now()
        }
        self.capital -= total_cost
        self._record_trade('BUY', symbol, execution_price, quantity)
        return True

    def close_position(self, symbol: str, price: float, quantity: Optional[float] = None) -> Optional[float]:
        """Close all or part of a position. `quantity=None` (the default)
        closes the whole thing, unchanged from before partial closes
        existed. A partial `quantity` (< the position's full size) sells
        only that portion, reduces the position's remaining quantity/cost
        basis proportionally, and leaves it open - the caller (partial
        profit-taking) is responsible for re-protecting whatever remains
        with a fresh stop, since this method only knows about capital/PnL
        bookkeeping, not exchange-side orders."""
        if symbol not in self.positions:
            return None
        position = self.positions[symbol]
        close_quantity = position['quantity'] if quantity is None else min(quantity, position['quantity'])
        if close_quantity <= 0:
            return None
        # Selling: slippage moves the fill price against you (lower).
        execution_price = price * (1 - self.slippage_rate)
        # gross_proceeds/commission are computed on the FULL real order -
        # that's what the exchange actually fills and charges a fee on,
        # regardless of how much of it is "explained" capital.
        gross_proceeds = execution_price * close_quantity
        commission_cost = gross_proceeds * self.commission_rate
        net_proceeds = gross_proceeds - commission_cost

        # A position reconciled from a real exchange balance the journal
        # couldn't fully explain (see PortfolioAgent.reconcile_with_exchange)
        # carries an unexplained_quantity this system never paid for and
        # never counted as tracked equity while held (_tracked_quantity).
        # Selling it must stay consistent with that: only the EXPLAINED
        # sub-portion's own proceeds/cost-basis becomes real capital/
        # realized PnL, or the sale would inject a one-time phantom capital
        # jump - the same class of bug already fixed for the held case. A
        # fungible balance has no way to sell "only the explained units",
        # so both sub-portions are assumed to sell in the same proportion
        # they exist in - but their COST bases differ (explained_cost is
        # the sub-portion's own real cost, tracked separately from the
        # blended `cost` used elsewhere, precisely because a flat
        # proportional slice of the BLENDED total would misattribute PnL
        # whenever the two sub-portions were priced differently, e.g. an
        # explained buy at $1 blended with an unexplained balance priced
        # at $2 at reconciliation time).
        unexplained_qty = position.get('unexplained_quantity', 0.0)
        explained_qty_before = position['quantity'] - unexplained_qty
        explained_cost_before = position.get('explained_cost', position['cost'])
        unexplained_close_qty = (
            close_quantity * (unexplained_qty / position['quantity']) if position['quantity'] > 0 else 0.0
        )
        explained_close_qty = close_quantity - unexplained_close_qty
        explained_proceeds_fraction = (explained_close_qty / close_quantity) if close_quantity > 0 else 0.0
        tracked_net_proceeds = net_proceeds * explained_proceeds_fraction
        self.capital += tracked_net_proceeds

        explained_cost_closed = (
            explained_cost_before * (explained_close_qty / explained_qty_before)
            if explained_qty_before > 1e-12 else 0.0
        )
        pnl = tracked_net_proceeds - explained_cost_closed
        # Proportional share of the original (blended explained+unexplained)
        # cost basis for the portion actually being closed - kept for the
        # blended `cost`/`price` fields other code still reads (e.g. the
        # dashboard's positions table), unchanged from before partial
        # closes existed.
        cost_basis_closed = position['cost'] * (close_quantity / position['quantity'])
        self._record_trade('SELL', symbol, execution_price, close_quantity, pnl)
        if close_quantity >= position['quantity']:
            del self.positions[symbol]
        else:
            position['quantity'] -= close_quantity
            position['cost'] -= cost_basis_closed
            position['unexplained_quantity'] = max(0.0, unexplained_qty - unexplained_close_qty)
            position['explained_cost'] = max(0.0, explained_cost_before - explained_cost_closed)
        # NOT self._update_equity_curve(): that appends CASH ONLY, but every
        # caller (PortfolioAgent.mark_to_market, ChampionChallengerAgent)
        # already appends the correct cash+positions total right after
        # calling this - an extra cash-only point here would splice a
        # fabricated drawdown into the curve whenever another symbol still
        # has an open position. See BacktestEngineV2, which has its own
        # separate equity_curve and never calls this method at all.
        return pnl
    
    def check_stop_loss_take_profit(self, symbol: str, current_price: float) -> bool:
        if symbol not in self.positions:
            return False
        position = self.positions[symbol]
        triggered = False
        if position['stop_loss'] and current_price <= position['stop_loss']:
            self.close_position(symbol, position['stop_loss'])
            triggered = True
        elif position['take_profit'] and current_price >= position['take_profit']:
            self.close_position(symbol, position['take_profit'])
            triggered = True
        return triggered
    
    def _tracked_quantity(self, symbol: str) -> float:
        """quantity minus unexplained_quantity - a position reconciled from
        a real exchange balance the journal's trade history couldn't fully
        explain (see PortfolioAgent.reconcile_with_exchange) carries an
        'unexplained_quantity' this system never actually paid for out of
        its own tracked capital (e.g. testnet faucet-seeded balances).
        `quantity` itself must stay the FULL real balance everywhere else
        (selling, protective-stop sizing, correlation/exposure risk - the
        account is genuinely exposed to its price risk regardless of who
        paid for it) - only live valuation excludes it, so faucet dust
        doesn't get counted as strategy trading capital or performance."""
        position = self.positions[symbol]
        return position['quantity'] - position.get('unexplained_quantity', 0.0)

    def get_total_value(self, current_prices: Dict[str, float]) -> float:
        positions_value = sum(
            self._tracked_quantity(sym) * current_prices.get(sym, 0)
            for sym in self.positions
        )
        return self.capital + positions_value

    def get_position_pnl(self, symbol: str, current_price: float) -> Optional[float]:
        if symbol not in self.positions:
            return None
        position = self.positions[symbol]
        return (current_price - position['price']) * self._tracked_quantity(symbol)
    
    def get_total_unrealized_pnl(self, current_prices: Dict[str, float]) -> float:
        return sum(
            self.get_position_pnl(sym, current_prices.get(sym, 0)) or 0
            for sym in self.positions
        )
    
    def _record_trade(self, action: str, symbol: str, price: float,
                      quantity: float, pnl: Optional[float] = None):
        self.trade_history.append({
            'timestamp': pd.Timestamp.now(),
            'action': action,
            'symbol': symbol,
            'price': price,
            'quantity': quantity,
            'value': price * quantity,
            'pnl': pnl
        })
        if len(self.trade_history) > self.MAX_HISTORY_LENGTH:
            del self.trade_history[: len(self.trade_history) - self.MAX_HISTORY_LENGTH]

    def _update_equity_curve(self):
        self.append_equity_point(self.capital)
    
    def get_performance_summary(self) -> Dict:
        if not self.trade_history:
            return {}
        trades_pnl = [t.get('pnl', 0) for t in self.trade_history if t['pnl']]
        return performance_summary_from_trade_pnls(trades_pnl)

    def get_max_drawdown(self) -> float:
        return max_drawdown_from_equity_curve(self.equity_curve)

    def get_sharpe_ratio(self, risk_free_rate: float = 0.02) -> float:
        return sharpe_ratio_from_equity_curve(self.equity_curve, risk_free_rate)
    
    def get_calmar_ratio(self) -> float:
        max_dd = self.get_max_drawdown()
        if max_dd == 0:
            return float('inf')
        if len(self.equity_curve) < 2:
            return 0.0
        total_return = (self.equity_curve[-1] - self.equity_curve[0]) / self.equity_curve[0]
        years = len(self.equity_curve) / 252
        annual_return = (1 + total_return) ** (1 / years) - 1 if years > 0 else 0
        return annual_return / max_dd


class RiskManager:
    """Risk management utilities."""
    
    def __init__(self, max_position_size: float = 0.1, max_daily_loss: float = 0.02):
        self.max_position_size = max_position_size
        self.max_daily_loss = max_daily_loss
        self.daily_pnl = 0
        self.current_day = None
    
    def calculate_position_size(self, capital: float, risk_per_trade: float = 0.02,
                                stop_loss_pct: float = 0.01) -> float:
        # Notional (quote-currency) size such that a stop-loss at stop_loss_pct
        # away caps the loss at risk_amount. The denominator must be
        # stop_loss_pct alone - dividing by (stop_loss_pct * capital) made
        # capital cancel out of the whole formula, so every position sized
        # to a fixed ~risk_per_trade/stop_loss_pct notional regardless of
        # actual capital (e.g. the same tiny size for $10k or $10m).
        risk_amount = capital * risk_per_trade
        position_size = risk_amount / stop_loss_pct
        return min(position_size, capital * self.max_position_size)
    
    def check_daily_loss_limit(self, today_pnl: float = 0.0) -> bool:
        if self.daily_pnl + today_pnl < -self.max_daily_loss:
            return False
        return True

    def reset_if_new_day(self, today: Optional[date] = None) -> None:
        """Reset the daily PnL counter when the calendar day rolls over.

        Nothing previously called this, so `daily_pnl` accumulated forever
        and `check_daily_loss_limit` was checked against a counter that
        never reset - daily-loss gating was effectively inert.
        """
        today = today or date.today()
        if self.current_day != today:
            self.daily_pnl = 0.0
            self.current_day = today

    def record_trade_pnl(self, pnl_fraction: float, today: Optional[date] = None) -> None:
        """Accumulate a realized trade's PnL, expressed as a fraction of capital, into today's total."""
        self.reset_if_new_day(today)
        self.daily_pnl += pnl_fraction
    
    def calculate_kelly_fraction(self, win_rate: float, avg_win: float, avg_loss: float) -> float:
        if avg_loss == 0:
            return 0
        b = avg_win / abs(avg_loss)
        kelly = (win_rate * b - (1 - win_rate)) / b
        return max(0, min(kelly, 0.25))
    
    def calculate_var(self, returns: pd.Series, confidence: float = 0.95) -> float:
        return np.percentile(returns, (1 - confidence) * 100)
    
    def calculate_cvar(self, returns: pd.Series, confidence: float = 0.95) -> float:
        var = self.calculate_var(returns, confidence)
        return returns[returns <= var].mean()
