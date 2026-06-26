#ifndef TRADING_ASSISTANT_NATIVE_RISK_LIMITS_H_
#define TRADING_ASSISTANT_NATIVE_RISK_LIMITS_H_

#include <cstdint>
#include <string>
#include <string_view>
#include <vector>

namespace trading_assistant::native_marketdata {

struct RiskLimitRule {
  std::string symbol;
  double max_notional = 0.0;
  std::uint64_t max_quantity = 0;
  std::int64_t max_slippage_bps = 0;
};

std::vector<RiskLimitRule> ParseRiskLimitRules(std::string_view input);
bool ValidateRiskLimitRules(std::string_view input);

}  // namespace trading_assistant::native_marketdata

#endif
