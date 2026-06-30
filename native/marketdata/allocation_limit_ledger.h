#pragma once

#include <map>
#include <string>
#include <vector>

namespace marketdata {

enum class LimitLedgerStatus {
  kOk,
  kWarn,
  kBreach,
};

struct LimitLedgerRule {
  std::string name;
  std::string scope;
  double soft_limit = 0.0;
  double hard_limit = 0.0;
  bool absolute = true;
};

struct LimitLedgerObservation {
  std::string rule_name;
  std::string scope_key;
  double observed = 0.0;
};

struct LimitLedgerResult {
  std::string rule_name;
  std::string scope_key;
  LimitLedgerStatus status = LimitLedgerStatus::kOk;
  double observed = 0.0;
  double limit = 0.0;
};

class AllocationLimitLedger {
 public:
  void AddRule(const LimitLedgerRule& rule);
  std::vector<LimitLedgerResult> Evaluate(
      const std::vector<LimitLedgerObservation>& observations) const;

 private:
  std::map<std::string, LimitLedgerRule> rules_;
};

std::string LimitLedgerStatusName(LimitLedgerStatus status);

}  // namespace marketdata
