#include "native/marketdata/risk_limits.h"

#include "native/marketdata/numeric_parser.h"
#include "native/marketdata/string_utils.h"

namespace trading_assistant::native_marketdata {

std::vector<RiskLimitRule> ParseRiskLimitRules(std::string_view input) {
  std::vector<RiskLimitRule> rules;

  if (input.size() > 131072) {
    return {};
  }

  std::size_t offset = 0;

  while (offset <= input.size()) {
    std::size_t next = input.find('\n', offset);
    if (next == std::string_view::npos) next = input.size();

    const std::string_view line = input.substr(offset, next - offset);
    offset = next + 1;

    if (TrimAscii(line).empty()) {
      if (next == input.size()) break;
      continue;
    }

    const auto fields = SplitLine(line, ',', 8);
    if (fields.size() != 4) {
      return {};
    }

    if (!IsAsciiSymbol(fields[0], 24)) {
      return {};
    }

    const auto max_notional = ParseFiniteDouble(fields[1]);
    const auto max_quantity = ParseUnsigned64(fields[2]);
    const auto slippage = ParseSigned64(fields[3]);

    if (!max_notional.has_value() || !max_quantity.has_value() || !slippage.has_value()) {
      return {};
    }

    if (*max_notional <= 0.0 || *max_notional > 1000000000000.0) {
      return {};
    }

    if (!IsValidQuantity(*max_quantity)) {
      return {};
    }

    if (!IsReasonableBasisPoints(*slippage)) {
      return {};
    }

    rules.push_back(RiskLimitRule{fields[0], *max_notional, *max_quantity, *slippage});

    if (rules.size() > 4096) {
      return {};
    }

    if (next == input.size()) break;
  }

  return rules;
}

bool ValidateRiskLimitRules(std::string_view input) {
  return !ParseRiskLimitRules(input).empty();
}

}  // namespace trading_assistant::native_marketdata
