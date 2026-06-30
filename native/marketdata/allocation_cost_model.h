#pragma once

#include <string>
#include <vector>

namespace marketdata {

struct AllocationCostInput {
  std::string symbol;
  std::string venue;
  double signed_quantity = 0.0;
  double reference_price = 0.0;
  double spread_bps = 0.0;
  double fee_bps = 0.0;
  double average_daily_volume = 0.0;
  double volatility = 0.0;
  bool urgent = false;
};

struct AllocationCostEstimate {
  std::string symbol;
  std::string venue;
  double notional = 0.0;
  double spread_cost = 0.0;
  double fee_cost = 0.0;
  double market_impact_cost = 0.0;
  double urgency_cost = 0.0;
  double total_cost = 0.0;
};

struct AllocationCostReport {
  std::vector<AllocationCostEstimate> estimates;
  std::vector<std::string> warnings;
  double total_notional = 0.0;
  double total_cost = 0.0;
};

class AllocationCostModel {
 public:
  AllocationCostModel();

  void SetImpactCoefficient(double value);
  void SetUrgencyMultiplier(double value);
  void SetMaximumParticipationRate(double value);

  AllocationCostReport Estimate(
      const std::vector<AllocationCostInput>& orders) const;

 private:
  double impact_coefficient_;
  double urgency_multiplier_;
  double maximum_participation_rate_;

  AllocationCostEstimate EstimateOne(
      const AllocationCostInput& input,
      std::vector<std::string>* warnings) const;
};

}  // namespace marketdata
