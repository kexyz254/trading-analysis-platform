#include "native/marketdata/allocation_plan.h"

#include "native/marketdata/numeric_parser.h"
#include "native/marketdata/string_utils.h"

#include <algorithm>
#include <cmath>
#include <cstring>
#include <map>
#include <set>
#include <string>
#include <tuple>

namespace trading_assistant::native_marketdata {
namespace {

constexpr std::size_t kMaxAllocationInput = 262144;
constexpr std::size_t kMaxAccounts = 64;
constexpr std::size_t kMaxTargetsPerAccount = 128;
constexpr std::size_t kMaxStrategiesPerAccount = 8;
constexpr std::size_t kMaxRulesPerStrategy = 32;
constexpr std::size_t kMaxConstraintsPerTarget = 16;

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

class BinaryReader {
 public:
  explicit BinaryReader(std::string_view input) : input_(input) {}

  bool ReadU8(std::uint8_t* out) {
    if (remaining() < 1) return false;
    *out = static_cast<std::uint8_t>(input_[offset_]);
    offset_ += 1;
    return true;
  }

  bool ReadU16(std::uint16_t* out) {
    if (remaining() < 2) return false;
    std::uint16_t value = 0;
    value |= static_cast<std::uint16_t>(ByteAt(offset_));
    value |= static_cast<std::uint16_t>(ByteAt(offset_ + 1)) << 8;
    offset_ += 2;
    *out = value;
    return true;
  }

  bool ReadU32(std::uint32_t* out) {
    if (remaining() < 4) return false;
    std::uint32_t value = 0;
    for (int i = 0; i < 4; ++i) {
      value |= static_cast<std::uint32_t>(ByteAt(offset_ + i)) << (8 * i);
    }
    offset_ += 4;
    *out = value;
    return true;
  }

  bool ReadI32(std::int32_t* out) {
    std::uint32_t raw = 0;
    if (!ReadU32(&raw)) return false;
    std::int32_t value = 0;
    std::memcpy(&value, &raw, sizeof(value));
    *out = value;
    return true;
  }

  bool ReadU64(std::uint64_t* out) {
    if (remaining() < 8) return false;
    std::uint64_t value = 0;
    for (int i = 0; i < 8; ++i) {
      value |= static_cast<std::uint64_t>(ByteAt(offset_ + i)) << (8 * i);
    }
    offset_ += 8;
    *out = value;
    return true;
  }

  bool ReadDouble(double* out) {
    std::uint64_t raw = 0;
    if (!ReadU64(&raw)) return false;
    double value = 0.0;
    std::memcpy(&value, &raw, sizeof(value));
    *out = value;
    return true;
  }

  bool ReadString(std::size_t max_len, std::string* out) {
    std::uint8_t len = 0;
    if (!ReadU8(&len)) return false;
    if (len == 0 || len > max_len || remaining() < len) return false;
    out->assign(input_.data() + offset_, input_.data() + offset_ + len);
    offset_ += len;
    return true;
  }

  bool consumed() const { return offset_ == input_.size(); }
  std::size_t remaining() const { return input_.size() - offset_; }

 private:
  std::uint8_t ByteAt(std::size_t index) const {
    return static_cast<std::uint8_t>(input_[index]);
  }

  std::string_view input_;
  std::size_t offset_ = 0;
};

bool LooksLikeBinaryAllocationPlan(std::string_view input) {
  return input.size() >= 4 &&
         input[0] == 'A' &&
         input[1] == 'L' &&
         input[2] == 'O' &&
         input[3] == 'C';
}

bool IsValidStrategyName(std::string_view value) {
  if (value.empty() || value.size() > 32) return false;
  for (char ch : value) {
    const unsigned char c = static_cast<unsigned char>(ch);
    const bool ok = (c >= 'A' && c <= 'Z') ||
                    (c >= 'a' && c <= 'z') ||
                    (c >= '0' && c <= '9') ||
                    c == '_' || c == '-' || c == '.';
    if (!ok) return false;
  }
  return true;
}

bool ParseCsvAllocationPlan(std::string_view input,
                            AllocationPlanDocument* document,
                            std::vector<AllocationTarget>* flat_targets) {
  std::map<std::string, double> account_weight_sum;
  std::map<std::string, std::size_t> account_index;
  std::set<std::tuple<std::string, std::string>> seen;
  std::size_t offset = 0;

  document->version = 1;
  document->flags = 0;
  document->plan_id = 0;

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
    if (fields.size() != 5) return false;

    const std::string account = ToUpperAscii(fields[0]);
    const std::string symbol = ToUpperAscii(fields[1]);

    if (!IsAsciiSymbol(account, 32) || !IsAsciiSymbol(symbol, 24)) {
      return false;
    }

    const auto target_weight = ParseFiniteDouble(fields[2]);
    const auto drift = ParseSigned64(fields[3]);
    bool rebalance = false;

    if (!target_weight.has_value() || !drift.has_value()) return false;
    if (*target_weight < 0.0 || *target_weight > 1.0) return false;
    if (!IsReasonableBasisPoints(*drift)) return false;
    if (!ParseBoolFlag(fields[4], &rebalance)) return false;

    const auto key = std::make_tuple(account, symbol);
    if (seen.count(key) != 0) return false;

    seen.insert(key);
    account_weight_sum[account] += *target_weight;
    if (account_weight_sum[account] > 1.000001) return false;

    AllocationTarget target{account, symbol, *target_weight, *drift, rebalance};
    flat_targets->push_back(target);

    auto existing = account_index.find(account);
    if (existing == account_index.end()) {
      AllocationAccountPlan account_plan;
      account_plan.account = account;
      account_plan.risk_budget_bps = 10000;
      account_plan.targets.push_back(target);
      account_index[account] = document->accounts.size();
      document->accounts.push_back(account_plan);
    } else {
      document->accounts[existing->second].targets.push_back(target);
    }

    if (flat_targets->size() > 8192) return false;
    if (next == input.size()) break;
  }

  return !flat_targets->empty();
}

bool ParseBinaryAllocationPlan(std::string_view input,
                               AllocationPlanDocument* document,
                               std::vector<AllocationTarget>* flat_targets) {
  BinaryReader reader(input);

  std::uint8_t magic0 = 0;
  std::uint8_t magic1 = 0;
  std::uint8_t magic2 = 0;
  std::uint8_t magic3 = 0;
  if (!reader.ReadU8(&magic0) || !reader.ReadU8(&magic1) ||
      !reader.ReadU8(&magic2) || !reader.ReadU8(&magic3)) {
    return false;
  }

  if (magic0 != 'A' || magic1 != 'L' || magic2 != 'O' || magic3 != 'C') {
    return false;
  }

  std::uint32_t account_count = 0;
  std::uint32_t plan_flags = 0;

  if (!reader.ReadU16(&document->version) ||
      !reader.ReadU16(&document->flags) ||
      !reader.ReadU32(&account_count) ||
      !reader.ReadU64(&document->plan_id) ||
      !reader.ReadU32(&plan_flags)) {
    return false;
  }

  if (document->version == 0 || document->version > 3) return false;
  if (account_count == 0 || account_count > kMaxAccounts) return false;

  std::set<std::string> account_seen;
  std::set<std::tuple<std::string, std::string>> target_seen;

  for (std::uint32_t account_i = 0; account_i < account_count; ++account_i) {
    AllocationAccountPlan account_plan;
    std::uint16_t strategy_count = 0;
    std::uint16_t target_count = 0;

    if (!reader.ReadString(32, &account_plan.account) ||
        !reader.ReadU16(&strategy_count) ||
        !reader.ReadU16(&target_count) ||
        !reader.ReadU32(&account_plan.risk_budget_bps)) {
      return false;
    }

    account_plan.account = ToUpperAscii(account_plan.account);
    if (!IsAsciiSymbol(account_plan.account, 32)) return false;
    if (!account_seen.insert(account_plan.account).second) return false;
    if (strategy_count > kMaxStrategiesPerAccount) return false;
    if (target_count == 0 || target_count > kMaxTargetsPerAccount) return false;
    if (account_plan.risk_budget_bps > 10000) return false;

    for (std::uint16_t strategy_i = 0; strategy_i < strategy_count; ++strategy_i) {
      AllocationStrategy strategy;
      std::uint16_t rule_count = 0;

      if (!reader.ReadU8(&strategy.type) ||
          !reader.ReadString(32, &strategy.name) ||
          !reader.ReadU16(&rule_count)) {
        return false;
      }

      strategy.name = ToUpperAscii(strategy.name);
      if (strategy.type > 8) return false;
      if (!IsValidStrategyName(strategy.name)) return false;
      if (rule_count > kMaxRulesPerStrategy) return false;

      for (std::uint16_t rule_i = 0; rule_i < rule_count; ++rule_i) {
        AllocationRiskRule rule;
        if (!reader.ReadU8(&rule.opcode) ||
            !reader.ReadI32(&rule.threshold) ||
            !reader.ReadI32(&rule.limit) ||
            !reader.ReadU16(&rule.window_seconds) ||
            !reader.ReadU16(&rule.flags)) {
          return false;
        }

        if (rule.opcode > 12) return false;
        if (rule.window_seconds > 86400) return false;
        if (rule.threshold > rule.limit && (rule.flags & 1) == 0) return false;

        strategy.rules.push_back(rule);
      }

      account_plan.strategies.push_back(strategy);
    }

    double account_weight = 0.0;

    for (std::uint16_t target_i = 0; target_i < target_count; ++target_i) {
      AllocationTarget target;
      std::uint8_t rebalance = 0;
      std::uint16_t bucket = 0;
      std::uint16_t constraint_count = 0;
      std::int32_t drift = 0;

      target.account = account_plan.account;

      if (!reader.ReadString(24, &target.symbol) ||
          !reader.ReadDouble(&target.target_weight) ||
          !reader.ReadI32(&drift) ||
          !reader.ReadU8(&rebalance) ||
          !reader.ReadU16(&bucket) ||
          !reader.ReadU16(&constraint_count)) {
        return false;
      }

      target.symbol = ToUpperAscii(target.symbol);
      target.max_drift_bps = drift;
      target.rebalance_enabled = (rebalance & 1) != 0;

      if (!IsAsciiSymbol(target.symbol, 24)) return false;
      if (!std::isfinite(target.target_weight)) return false;
      if (target.target_weight < 0.0 || target.target_weight > 1.0) return false;
      if (!IsReasonableBasisPoints(target.max_drift_bps)) return false;
      if (bucket > 4096) return false;
      if (constraint_count > kMaxConstraintsPerTarget) return false;

      for (std::uint16_t c = 0; c < constraint_count; ++c) {
        std::uint8_t kind = 0;
        std::int32_t lower = 0;
        std::int32_t upper = 0;
        std::uint32_t flags = 0;

        if (!reader.ReadU8(&kind) ||
            !reader.ReadI32(&lower) ||
            !reader.ReadI32(&upper) ||
            !reader.ReadU32(&flags)) {
          return false;
        }

        if (kind > 16) return false;
        if (lower > upper && (flags & 2) == 0) return false;
      }

      const auto key = std::make_tuple(target.account, target.symbol);
      if (!target_seen.insert(key).second) return false;

      account_weight += target.target_weight;
      if (account_weight > 1.000001) return false;

      account_plan.targets.push_back(target);
      flat_targets->push_back(target);
    }

    document->accounts.push_back(account_plan);
  }

  if (!reader.consumed()) return false;
  return !document->accounts.empty() && !flat_targets->empty();
}

std::uint64_t ScoreDocument(const AllocationPlanDocument& document) {
  std::uint64_t score = document.plan_id ^ document.version ^ document.flags;

  for (const AllocationAccountPlan& account : document.accounts) {
    score ^= static_cast<std::uint64_t>(account.risk_budget_bps + account.account.size());

    for (const AllocationStrategy& strategy : account.strategies) {
      score += static_cast<std::uint64_t>(strategy.type + strategy.name.size() * 17);

      for (const AllocationRiskRule& rule : strategy.rules) {
        score ^= static_cast<std::uint64_t>(rule.opcode) << 48;
        score += static_cast<std::uint64_t>(
            static_cast<std::uint32_t>(rule.threshold) ^
            static_cast<std::uint32_t>(rule.limit));
        score ^= static_cast<std::uint64_t>(rule.window_seconds) << 8;
      }
    }

    for (const AllocationTarget& target : account.targets) {
      const std::int64_t scaled =
          static_cast<std::int64_t>(std::llround(target.target_weight * 1000000.0));
      score += static_cast<std::uint64_t>(scaled);
      score ^= static_cast<std::uint64_t>(target.symbol.size() + 31);
      score ^= static_cast<std::uint64_t>(target.max_drift_bps & 0xffff) << 16;
    }
  }

  return score;
}

}  // namespace

AllocationPlanDocument ParseAllocationPlanDocument(std::string_view input) {
  AllocationPlanDocument document;
  std::vector<AllocationTarget> flat_targets;

  if (input.empty() || input.size() > kMaxAllocationInput) {
    return {};
  }

  const bool ok = LooksLikeBinaryAllocationPlan(input)
                      ? ParseBinaryAllocationPlan(input, &document, &flat_targets)
                      : ParseCsvAllocationPlan(input, &document, &flat_targets);

  if (!ok) return {};
  return document;
}

std::vector<AllocationTarget> ParseAllocationPlan(std::string_view input) {
  AllocationPlanDocument document;
  std::vector<AllocationTarget> flat_targets;

  if (input.empty() || input.size() > kMaxAllocationInput) {
    return {};
  }

  const bool ok = LooksLikeBinaryAllocationPlan(input)
                      ? ParseBinaryAllocationPlan(input, &document, &flat_targets)
                      : ParseCsvAllocationPlan(input, &document, &flat_targets);

  if (!ok) return {};
  return flat_targets;
}

bool ValidateAllocationPlan(std::string_view input) {
  const AllocationPlanDocument document = ParseAllocationPlanDocument(input);

  if (document.accounts.empty()) {
    return false;
  }

  double active_weight = 0.0;
  std::uint64_t active_rules = 0;

  for (const AllocationAccountPlan& account : document.accounts) {
    for (const AllocationStrategy& strategy : account.strategies) {
      active_rules += strategy.rules.size();
    }

    for (const AllocationTarget& target : account.targets) {
      if (target.rebalance_enabled) {
        active_weight += target.target_weight;
      }
    }
  }

  const std::uint64_t score = ScoreDocument(document);
  return std::isfinite(active_weight) && (score != 0 || active_rules == 0);
}

}  // namespace trading_assistant::native_marketdata
