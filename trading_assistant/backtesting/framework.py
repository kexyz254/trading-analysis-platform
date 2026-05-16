import numpy as np
import pandas as pd
from typing import List, Dict, Any, Tuple, Optional
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

class OrderDirection(Enum):
    LONG = 1
    SHORT = -1
    FLAT = 0

@dataclass
class Position:
    symbol: str
    direction: OrderDirection
    entry_price: float
    entry_time: datetime
    quantity: float
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    current_price: float = 0
    pnl: float = 0
    pnl_pct: float = 0

@dataclass
class Trade:
    symbol: str
    direction: OrderDirection
    entry_price: float
    entry_time: datetime
    exit_price: float
    exit_time: datetime
    quantity: float
    gross_pnl: float
    net_pnl: float
    commission: float
    slippage: float

@dataclass
class BacktestResult:
    initial_capital: float
    final_capital: float
    total_return: float
    total_return_pct: float
    sharpe_ratio: float
    sortino_ratio: float
    max_drawdown: float
    max_drawdown_pct: float
    win_rate: float
    profit_factor: float
    num_trades: int
    winning_trades: int
    losing_trades: int
    avg_win: float
    avg_loss: float
    expectancy: float
    total_commission: float
    total_slippage: float
    trades: List[Trade] = field(default_factory=list)
    equity_curve: List[float] = field(default_factory=list)

class BacktestEngineV2:
    def __init__(self, initial_capital: float = 100000.0, 
                 commission: float = 0.001,
                 slippage: float = 0.0005):
        self.initial_capital = initial_capital
        self.capital = initial_capital
        self.commission = commission
        self.slippage = slippage
        self.positions: List[Position] = []
        self.trades: List[Trade] = []
        self.equity_curve = [initial_capital]
        self.current_index = 0
        
    def reset(self):
        self.capital = self.initial_capital
        self.positions = []
        self.trades = []
        self.equity_curve = [self.initial_capital]
        self.current_index = 0
    
    def _calculate_position_size(self, capital: float, price: float, 
                                   risk_pct: float = 0.02, 
                                   stop_loss_pct: float = 0.01) -> float:
        risk_amount = capital * risk_pct
        stop_distance = price * stop_loss_pct
        quantity = risk_amount / stop_distance
        return quantity
    
    def _apply_commission(self, value: float) -> float:
        return value * self.commission
    
    def _apply_slippage(self, price: float, direction: OrderDirection) -> float:
        if direction == OrderDirection.LONG:
            return price * (1 + self.slippage)
        else:
            return price * (1 - self.slippage)
    
    def open_position(self, symbol: str, direction: OrderDirection, 
                      price: float, timestamp: datetime,
                      quantity: Optional[float] = None,
                      risk_pct: float = 0.02) -> bool:
        if quantity is None:
            quantity = self._calculate_position_size(self.capital, price, risk_pct)
        
        execution_price = self._apply_slippage(price, direction)
        cost = execution_price * quantity
        commission_cost = self._apply_commission(cost)
        
        if cost + commission_cost > self.capital:
            return False
        
        position = Position(
            symbol=symbol,
            direction=direction,
            entry_price=execution_price,
            entry_time=timestamp,
            quantity=quantity
        )
        self.positions.append(position)
        self.capital -= (cost + commission_cost)
        return True
    
    def close_position(self, position: Position, price: float, timestamp: datetime) -> Trade:
        execution_price = self._apply_slippage(price, 
            OrderDirection.LONG if position.direction == OrderDirection.SHORT else OrderDirection.SHORT)
        proceeds = execution_price * position.quantity
        commission_cost = self._apply_commission(proceeds)
        
        gross_pnl = proceeds - (position.entry_price * position.quantity)
        if position.direction == OrderDirection.SHORT:
            gross_pnl = -gross_pnl
        
        net_pnl = gross_pnl - commission_cost
        
        trade = Trade(
            symbol=position.symbol,
            direction=position.direction,
            entry_price=position.entry_price,
            entry_time=position.entry_time,
            exit_price=execution_price,
            exit_time=timestamp,
            quantity=position.quantity,
            gross_pnl=gross_pnl,
            net_pnl=net_pnl,
            commission=commission_cost,
            slippage=abs(execution_price - price) * position.quantity
        )
        
        self.trades.append(trade)
        self.capital += proceeds - commission_cost
        self.positions.remove(position)
        return trade
    
    def update_stops(self, current_price: float):
        for pos in self.positions:
            if pos.stop_loss and current_price <= pos.stop_loss:
                self.close_position(pos, pos.stop_loss, datetime.now())
            elif pos.take_profit and current_price >= pos.take_profit:
                self.close_position(pos, pos.take_profit, datetime.now())
    
    def update_equity(self, current_prices: Dict[str, float]):
        positions_value = 0
        for pos in self.positions:
            pos.current_price = current_prices.get(pos.symbol, pos.entry_price)
            if pos.direction == OrderDirection.LONG:
                pos.pnl = (pos.current_price - pos.entry_price) * pos.quantity
            else:
                pos.pnl = (pos.entry_price - pos.current_price) * pos.quantity
            pos.pnl_pct = pos.pnl / (pos.entry_price * pos.quantity)
            positions_value += pos.current_price * pos.quantity
        
        total_value = self.capital + positions_value
        self.equity_curve.append(total_value)
        return total_value
    
    def get_result(self) -> BacktestResult:
        if not self.trades:
            return BacktestResult(
                initial_capital=self.initial_capital,
                final_capital=self.capital,
                total_return=0,
                total_return_pct=0,
                sharpe_ratio=0,
                sortino_ratio=0,
                max_drawdown=0,
                max_drawdown_pct=0,
                win_rate=0,
                profit_factor=0,
                num_trades=0,
                winning_trades=0,
                losing_trades=0,
                avg_win=0,
                avg_loss=0,
                expectancy=0,
                total_commission=0,
                total_slippage=0
            )
        
        final_capital = self.equity_curve[-1] if self.equity_curve else self.capital
        total_return = final_capital - self.initial_capital
        total_return_pct = (total_return / self.initial_capital) * 100
        
        winning_trades = [t for t in self.trades if t.net_pnl > 0]
        losing_trades = [t for t in self.trades if t.net_pnl < 0]
        win_rate = len(winning_trades) / len(self.trades) if self.trades else 0
        profit_factor = abs(sum(t.net_pnl for t in winning_trades) / sum(t.net_pnl for t in losing_trades)) if losing_trades else float('inf')
        
        returns = pd.Series(self.equity_curve).pct_change().dropna()
        sharpe_ratio = returns.mean() / returns.std() * np.sqrt(252) if returns.std() != 0 else 0
        
        downside_returns = returns[returns < 0]
        sortino_ratio = returns.mean() / downside_returns.std() * np.sqrt(252) if downside_returns.std() != 0 else 0
        
        rolling_max = pd.Series(self.equity_curve).expanding().max()
        drawdown = (pd.Series(self.equity_curve) - rolling_max) / rolling_max
        max_drawdown = drawdown.min()
        max_drawdown_pct = max_drawdown * 100
        
        avg_win = sum(t.net_pnl for t in winning_trades) / len(winning_trades) if winning_trades else 0
        avg_loss = sum(t.net_pnl for t in losing_trades) / len(losing_trades) if losing_trades else 0
        expectancy = win_rate * avg_win + (1 - win_rate) * avg_loss if avg_loss else 0
        
        return BacktestResult(
            initial_capital=self.initial_capital,
            final_capital=final_capital,
            total_return=total_return,
            total_return_pct=total_return_pct,
            sharpe_ratio=sharpe_ratio,
            sortino_ratio=sortino_ratio,
            max_drawdown=max_drawdown,
            max_drawdown_pct=max_drawdown_pct,
            win_rate=win_rate,
            profit_factor=profit_factor,
            num_trades=len(self.trades),
            winning_trades=len(winning_trades),
            losing_trades=len(losing_trades),
            avg_win=avg_win,
            avg_loss=avg_loss,
            expectancy=expectancy,
            total_commission=sum(t.commission for t in self.trades),
            total_slippage=sum(t.slippage for t in self.trades),
            trades=self.trades,
            equity_curve=self.equity_curve
        )
