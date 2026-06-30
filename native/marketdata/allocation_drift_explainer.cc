#include "allocation_drift_explainer.h"

#include <algorithm>
#include <cmath>
#include <stdexcept>

namespace marketdata {
namespace {

bool Finite(double value) {
  return std::isfinite(value);
}

double Abs(double value) {
  return value < 0.0 ? -value : value;
}

}  // namespace

AllocationDriftExplainer::AllocationDriftExplainer()
    : default_actionable_drift_(0.01),
      return_sensitivity_(0.35),
      cash_flow_sensitivity_(0.50) {}

void AllocationDriftExplainer::SetDefaultActionableDrift(double value) {
  if (!Finite(value) || value < 0.0) {
    throw std::invalid_argument("actionable drift must be non-negative");
  }
  default_actionable_drift_ = value;
}

void AllocationDriftExplainer::SetReturnSensitivity(double value) {
  if (!Finite(value) || value < 0.0) {
    throw std::invalid_argument("return sensitivity must be non-negative");
  }
  return_sensitivity_ = value;
}

void AllocationDriftExplainer::SetCashFlowSensitivity(double value) {
  if (!Finite(value) || value < 0.0) {
    throw std::invalid_argument("cash-flow sensitivity must be non-negative");
  }
  cash_flow_sensitivity_ = value;
}

std::vector<DriftExplanation> AllocationDriftExplainer::Explain(
    const std::vector<DriftInput>& inputs) const {
  std::vector<DriftExplanation> out;
  out.reserve(inputs.size());

  for (const DriftInput& input : inputs) {
    out.push_back(ExplainOne(input));
  }

  std::sort(out.begin(), out.end(),
            [](const DriftExplanation& a, const DriftExplanation& b) {
              return a.trade_priority > b.trade_priority;
            });

  return out;
}

DriftExplanation AllocationDriftExplainer::ExplainOne(
    const DriftInput& input) const {
  DriftExplanation explanation;
  explanation.symbol = input.symbol;
  explanation.group = input.group;

  if (!Finite(input.current_weight) ||
      !Finite(input.target_weight) ||
      !Finite(input.price_return) ||
      !Finite(input.cash_flow_weight)) {
    explanation.primary_reason = "invalid numeric drift input";
    return explanation;
  }

  explanation.drift = input.target_weight - input.current_weight;

  const double threshold =
      input.minimum_actionable_drift > 0.0
          ? input.minimum_actionable_drift
          : default_actionable_drift_;

  if (input.restricted) {
    explanation.primary_reason = "symbol is restricted";
    explanation.actionable = false;
    explanation.trade_priority = 0.0;
    return explanation;
  }

  const double return_component = Abs(input.price_return) * return_sensitivity_;
  const double flow_component = Abs(input.cash_flow_weight) * cash_flow_sensitivity_;
  const double drift_component = Abs(explanation.drift);

  if (drift_component < threshold) {
    explanation.primary_reason = "drift is below actionable threshold";
    explanation.actionable = false;
  } else if (flow_component > return_component && flow_component > threshold) {
    explanation.primary_reason = "cash flow changed portfolio weights";
    explanation.actionable = true;
  } else if (return_component > threshold) {
    explanation.primary_reason = "price movement changed portfolio weights";
    explanation.actionable = true;
  } else {
    explanation.primary_reason = "target allocation changed";
    explanation.actionable = true;
  }

  explanation.trade_priority = explanation.actionable
                                   ? drift_component + return_component + flow_component
                                   : 0.0;

  return explanation;
}

}  // namespace marketdata
