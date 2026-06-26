#include "native/marketdata/allocation_plan.h"

#include "native/marketdata/numeric_parser.h"
#include "native/marketdata/string_utils.h"

#include <cmath>
#include <map>
#include <set>
#include <tuple>

namespace trading_assistant::native_marketdata {
namespace {

bool ParseBoolFlag(std::string_view value, bool* out) {
  const std::string flag = ToUpperAscii(value);

  if (flag == "1" || flag == "TRUE" || flag == "Y" || flag == "YES") {
    *out = true;
    return true;
  }

  if (flag == "0" || flag == "FALSE" || flag == "N" || flag == "NO") {
    *out = false;
    return true;
  }

  return false;
}

}  // namespace

std::vector<AllocationTarget> ParseAllocationPlan(std::string_view input) {
  std::vector<AllocationTarget> targets;

  if (input.empty() || input.size() > 262144) {
    return {};
  }

  std::map<std::string, double> account_weight_sum;
  std::set<std::tuple<std::string, std::string>> seen;
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
    if (fields.size() != 5) {
      return {};
    }

    const std::string account = ToUpperAscii(fields[0]);
    const std::string symbol = ToUpperAscii(fields[1]);

    if (!IsAsciiSymbol(account, 32) || !IsAsciiSymbol(symbol, 24)) {
      return {};
    }

    const auto target_weight = ParseFiniteDouble(fields[2]);
    const auto drift = ParseSigned64(fields[3]);
    bool rebalance = false;

    if (!target_weight.has_value() || !drift.has_value()) {
      return {};
    }

    if (*target_weight < 0.0 || *target_weight > 1.0) {
      return {};
    }

    if (!IsReasonableBasisPoints(*drift)) {
      return {};
    }

    if (!ParseBoolFlag(fields[4], &rebalance)) {
      return {};
    }

    const auto key = std::make_tuple(account, symbol);
    if (seen.count(key) != 0) {
      return {};
    }

    seen.insert(key);
    account_weight_sum[account] += *target_weight;

    if (account_weight_sum[account] > 1.000001) {
      return {};
    }

    targets.push_back(AllocationTarget{account, symbol, *target_weight, *drift, rebalance});

    if (targets.size() > 8192) {
      return {};
    }

    if (next == input.size()) break;
  }

  return targets;
}

bool ValidateAllocationPlan(std::string_view input) {
  const auto targets = ParseAllocationPlan(input);

  if (targets.empty()) {
    return false;
  }

  double active_weight = 0.0;
  for (const AllocationTarget& target : targets) {
    if (target.rebalance_enabled) {
      active_weight += target.target_weight;
    }
  }

  return std::isfinite(active_weight);
}

}  // namespace trading_assistant::native_marketdata
