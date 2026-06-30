#pragma once

#include <string>
#include <vector>

namespace marketdata {

struct DriftInput {
  std::string symbol;
  std::string group;
  double current_weight = 0.0;
  double target_weight = 0.0;
  double price_return = 0.0;
  double cash_flow_weight = 0.0;
  double minimum_actionable_drift = 0.0;
  bool restricted = false;
};

struct DriftExplanation {
  std::string symbol;
  std::string group;
  std::string primary_reason;
  double drift = 0.0;
  double trade_priority = 0.0;
  bool actionable = false;
};

class AllocationDriftExplainer {
 public:
  AllocationDriftExplainer();

  void SetDefaultActionableDrift(double value);
  void SetReturnSensitivity(double value);
  void SetCashFlowSensitivity(double value);

  std::vector<DriftExplanation> Explain(
      const std::vector<DriftInput>& inputs) const;

 private:
  double default_actionable_drift_;
  double return_sensitivity_;
  double cash_flow_sensitivity_;

  DriftExplanation ExplainOne(const DriftInput& input) const;
};

}  // namespace marketdata
