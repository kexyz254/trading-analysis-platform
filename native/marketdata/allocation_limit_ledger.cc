#include "allocation_limit_ledger.h"

#include <cmath>
#include <stdexcept>

namespace marketdata {
namespace {

double Magnitude(double value, bool absolute) {
  return absolute ? std::fabs(value) : value;
}

}  // namespace

void AllocationLimitLedger::AddRule(const LimitLedgerRule& rule) {
  if (rule.name.empty()) {
    throw std::invalid_argument("limit rule requires a name");
  }
  if (!std::isfinite(rule.soft_limit) ||
      !std::isfinite(rule.hard_limit) ||
      rule.hard_limit < rule.soft_limit) {
    throw std::invalid_argument("invalid limit bounds");
  }
  rules_[rule.name] = rule;
}

std::vector<LimitLedgerResult> AllocationLimitLedger::Evaluate(
    const std::vector<LimitLedgerObservation>& observations) const {
  std::vector<LimitLedgerResult> results;

  for (const LimitLedgerObservation& observation : observations) {
    const auto rule_it = rules_.find(observation.rule_name);
    if (rule_it == rules_.end()) {
      continue;
    }

    const LimitLedgerRule& rule = rule_it->second;
    const double value = Magnitude(observation.observed, rule.absolute);

    LimitLedgerResult result;
    result.rule_name = observation.rule_name;
    result.scope_key = observation.scope_key;
    result.observed = value;

    if (value >= rule.hard_limit) {
      result.status = LimitLedgerStatus::kBreach;
      result.limit = rule.hard_limit;
    } else if (value >= rule.soft_limit) {
      result.status = LimitLedgerStatus::kWarn;
      result.limit = rule.soft_limit;
    } else {
      result.status = LimitLedgerStatus::kOk;
      result.limit = rule.soft_limit;
    }

    results.push_back(result);
  }

  return results;
}

std::string LimitLedgerStatusName(LimitLedgerStatus status) {
  switch (status) {
    case LimitLedgerStatus::kOk:
      return "ok";
    case LimitLedgerStatus::kWarn:
      return "warn";
    case LimitLedgerStatus::kBreach:
      return "breach";
  }
  return "unknown";
}

}  // namespace marketdata
