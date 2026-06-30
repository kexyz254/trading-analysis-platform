#pragma once

#include <cstdint>
#include <string>
#include <vector>

namespace marketdata {

struct RebalanceOrder {
  std::string symbol;
  std::string venue;
  double quantity = 0.0;
  double limit_price = 0.0;
  double participation_rate = 0.0;
};

struct RebalanceMarketState {
  std::string symbol;
  double open_price = 0.0;
  double close_price = 0.0;
  double average_daily_volume = 0.0;
  double spread_bps = 0.0;
  double volatility = 0.0;
  bool halted = false;
};

struct RebalanceFill {
  std::string symbol;
  std::string venue;
  double filled_quantity = 0.0;
  double average_price = 0.0;
  double slippage_bps = 0.0;
  bool complete = false;
};

struct RebalanceSimulationReport {
  std::vector<RebalanceFill> fills;
  std::vector<std::string> warnings;

  double filled_notional = 0.0;
  double unfilled_notional = 0.0;
  double estimated_slippage = 0.0;
  std::uint64_t completed_orders = 0;
};

class AllocationRebalanceSimulator {
 public:
  AllocationRebalanceSimulator();

  void SetMaxSpreadBps(double value);
  void SetMarketImpactCoefficient(double value);
  void SetAllowHalts(bool value);

  RebalanceSimulationReport Simulate(
      const std::vector<RebalanceOrder>& orders,
      const std::vector<RebalanceMarketState>& market) const;

 private:
  double max_spread_bps_;
  double impact_coefficient_;
  bool allow_halts_;

  const RebalanceMarketState* FindMarket(
      const std::vector<RebalanceMarketState>& market,
      const std::string& symbol) const;

  RebalanceFill FillOrder(const RebalanceOrder& order,
                          const RebalanceMarketState& state,
                          std::vector<std::string>* warnings) const;
};

}  // namespace marketdata
