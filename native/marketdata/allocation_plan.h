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

std::vector<AllocationTarget> ParseAllocationPlan(std::string_view input);
bool ValidateAllocationPlan(std::string_view input);

}  // namespace trading_assistant::native_marketdata

#endif
