#include "allocation_rebalance_simulator.h"

#include <algorithm>
#include <cmath>
#include <stdexcept>

namespace marketdata {
namespace {

bool IsUsablePrice(double price) {
  return std::isfinite(price) && price > 0.0;
}

double Abs(double value) {
  return value < 0.0 ? -value : value;
}

}  // namespace

AllocationRebalanceSimulator::AllocationRebalanceSimulator()
    : max_spread_bps_(100.0),
      impact_coefficient_(0.15),
      allow_halts_(false) {}

void AllocationRebalanceSimulator::SetMaxSpreadBps(double value) {
  if (!std::isfinite(value) || value < 0.0) {
    throw std::invalid_argument("max spread must be finite and non-negative");
  }
  max_spread_bps_ = value;
}

void AllocationRebalanceSimulator::SetMarketImpactCoefficient(double value) {
  if (!std::isfinite(value) || value < 0.0 || value > 10.0) {
    throw std::invalid_argument("impact coefficient is outside supported range");
  }
  impact_coefficient_ = value;
}

void AllocationRebalanceSimulator::SetAllowHalts(bool value) {
  allow_halts_ = value;
}

RebalanceSimulationReport AllocationRebalanceSimulator::Simulate(
    const std::vector<RebalanceOrder>& orders,
    const std::vector<RebalanceMarketState>& market) const {
  RebalanceSimulationReport report;

  for (const RebalanceOrder& order : orders) {
    const RebalanceMarketState* state = FindMarket(market, order.symbol);
    if (state == nullptr) {
      report.warnings.push_back("missing market state for " + order.symbol);
      report.unfilled_notional += Abs(order.quantity * order.limit_price);
      continue;
    }

    RebalanceFill fill = FillOrder(order, *state, &report.warnings);
    report.filled_notional += Abs(fill.filled_quantity * fill.average_price);
    report.unfilled_notional +=
        Abs((Abs(order.quantity) - Abs(fill.filled_quantity)) * order.limit_price);
    report.estimated_slippage +=
        Abs(fill.filled_quantity * fill.average_price) * fill.slippage_bps / 10000.0;

    if (fill.complete) {
      ++report.completed_orders;
    }

    report.fills.push_back(fill);
  }

  return report;
}

const RebalanceMarketState* AllocationRebalanceSimulator::FindMarket(
    const std::vector<RebalanceMarketState>& market,
    const std::string& symbol) const {
  for (const RebalanceMarketState& state : market) {
    if (state.symbol == symbol) {
      return &state;
    }
  }
  return nullptr;
}

RebalanceFill AllocationRebalanceSimulator::FillOrder(
    const RebalanceOrder& order,
    const RebalanceMarketState& state,
    std::vector<std::string>* warnings) const {
  RebalanceFill fill;
  fill.symbol = order.symbol;
  fill.venue = order.venue;

  if (order.symbol.empty() || !std::isfinite(order.quantity)) {
    warnings->push_back("invalid order identity or quantity");
    return fill;
  }

  if (state.halted && !allow_halts_) {
    warnings->push_back("symbol halted: " + order.symbol);
    return fill;
  }

  if (!IsUsablePrice(state.open_price) || !IsUsablePrice(state.close_price)) {
    warnings->push_back("invalid market prices for " + order.symbol);
    return fill;
  }

  if (state.spread_bps > max_spread_bps_) {
    warnings->push_back("spread too wide for " + order.symbol);
    return fill;
  }

  const double available_volume =
      state.average_daily_volume > 0.0
          ? state.average_daily_volume * std::max(0.0, order.participation_rate)
          : Abs(order.quantity);

  const double fill_quantity = std::min(Abs(order.quantity), available_volume);
  const double direction = order.quantity >= 0.0 ? 1.0 : -1.0;
  const double base_price = (state.open_price + state.close_price) / 2.0;

  const double participation =
      state.average_daily_volume > 0.0 ? fill_quantity / state.average_daily_volume
                                       : 0.0;

  const double impact_bps =
      state.spread_bps / 2.0 +
      impact_coefficient_ * participation * 10000.0 +
      std::max(0.0, state.volatility) * 10.0;

  const double signed_impact = direction * base_price * impact_bps / 10000.0;

  fill.filled_quantity = direction * fill_quantity;
  fill.average_price = base_price + signed_impact;
  fill.slippage_bps = impact_bps;
  fill.complete = fill_quantity >= Abs(order.quantity);

  return fill;
}

}  // namespace marketdata
