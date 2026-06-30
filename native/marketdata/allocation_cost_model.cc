#include "allocation_cost_model.h"

#include <algorithm>
#include <cmath>
#include <stdexcept>

namespace marketdata {
namespace {

double Abs(double value) {
  return value < 0.0 ? -value : value;
}

bool PositiveFinite(double value) {
  return std::isfinite(value) && value > 0.0;
}

}  // namespace

AllocationCostModel::AllocationCostModel()
    : impact_coefficient_(0.20),
      urgency_multiplier_(1.50),
      maximum_participation_rate_(0.15) {}

void AllocationCostModel::SetImpactCoefficient(double value) {
  if (!std::isfinite(value) || value < 0.0 || value > 10.0) {
    throw std::invalid_argument("impact coefficient outside supported range");
  }
  impact_coefficient_ = value;
}

void AllocationCostModel::SetUrgencyMultiplier(double value) {
  if (!std::isfinite(value) || value < 1.0 || value > 20.0) {
    throw std::invalid_argument("urgency multiplier outside supported range");
  }
  urgency_multiplier_ = value;
}

void AllocationCostModel::SetMaximumParticipationRate(double value) {
  if (!std::isfinite(value) || value <= 0.0 || value > 1.0) {
    throw std::invalid_argument("participation rate must be within (0, 1]");
  }
  maximum_participation_rate_ = value;
}

AllocationCostReport AllocationCostModel::Estimate(
    const std::vector<AllocationCostInput>& orders) const {
  AllocationCostReport report;

  for (const AllocationCostInput& order : orders) {
    AllocationCostEstimate estimate = EstimateOne(order, &report.warnings);
    report.total_notional += estimate.notional;
    report.total_cost += estimate.total_cost;
    report.estimates.push_back(estimate);
  }

  return report;
}

AllocationCostEstimate AllocationCostModel::EstimateOne(
    const AllocationCostInput& input,
    std::vector<std::string>* warnings) const {
  AllocationCostEstimate estimate;
  estimate.symbol = input.symbol;
  estimate.venue = input.venue;

  if (input.symbol.empty()) {
    warnings->push_back("cost model skipped row with missing symbol");
    return estimate;
  }

  if (!PositiveFinite(input.reference_price) ||
      !std::isfinite(input.signed_quantity) ||
      !std::isfinite(input.spread_bps) ||
      !std::isfinite(input.fee_bps)) {
    warnings->push_back("cost model received invalid numeric fields for " + input.symbol);
    return estimate;
  }

  const double quantity = Abs(input.signed_quantity);
  estimate.notional = quantity * input.reference_price;
  estimate.spread_cost = estimate.notional * std::max(0.0, input.spread_bps) / 20000.0;
  estimate.fee_cost = estimate.notional * std::max(0.0, input.fee_bps) / 10000.0;

  double participation = 0.0;
  if (input.average_daily_volume > 0.0) {
    participation = quantity / input.average_daily_volume;
  }

  if (participation > maximum_participation_rate_) {
    warnings->push_back("order participation exceeds cost-model maximum for " + input.symbol);
  }

  estimate.market_impact_cost =
      estimate.notional *
      impact_coefficient_ *
      participation *
      (1.0 + std::max(0.0, input.volatility));

  if (input.urgent) {
    estimate.urgency_cost =
        (estimate.spread_cost + estimate.market_impact_cost) *
        (urgency_multiplier_ - 1.0);
  }

  estimate.total_cost =
      estimate.spread_cost +
      estimate.fee_cost +
      estimate.market_impact_cost +
      estimate.urgency_cost;

  return estimate;
}

}  // namespace marketdata
