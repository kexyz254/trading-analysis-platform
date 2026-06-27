#ifndef TRADING_ASSISTANT_NATIVE_ALLOCATION_PLAN_H_
#define TRADING_ASSISTANT_NATIVE_ALLOCATION_PLAN_H_

#include <cstdint>
#include <string>
#include <string_view>
#include <vector>

namespace trading_assistant::native_marketdata {

struct AllocationTarget {
  std::string account;
  std::string symbol;
  double target_weight = 0.0;
  std::int64_t max_drift_bps = 0;
  bool rebalance_enabled = false;
};

struct AllocationRiskRule {
  std::uint8_t opcode = 0;
  std::int32_t threshold = 0;
  std::int32_t limit = 0;
  std::uint16_t window_seconds = 0;
  std::uint16_t flags = 0;
};

struct AllocationStrategy {
  std::uint8_t type = 0;
  std::string name;
  std::vector<AllocationRiskRule> rules;
};

struct AllocationAccountPlan {
  std::string account;
  std::uint32_t risk_budget_bps = 0;
  std::vector<AllocationStrategy> strategies;
  std::vector<AllocationTarget> targets;
};

struct AllocationPlanDocument {
  std::uint16_t version = 0;
  std::uint16_t flags = 0;
  std::uint64_t plan_id = 0;
  std::vector<AllocationAccountPlan> accounts;
};

std::vector<AllocationTarget> ParseAllocationPlan(std::string_view input);
AllocationPlanDocument ParseAllocationPlanDocument(std::string_view input);
bool ValidateAllocationPlan(std::string_view input);

}  // namespace trading_assistant::native_marketdata

#endif
