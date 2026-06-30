#pragma once

#include <cstdint>
#include <string>
#include <vector>

namespace marketdata {

enum class PreTradeDecision {
  kAllow,
  kWarn,
  kBlock,
};

struct PreTradeGuardRule {
  double max_order_notional = 500000.0;
  double max_participation_rate = 0.15;
  double max_spread_bps = 100.0;
  double max_slippage_bps = 250.0;
  double min_cash_after_trade = 0.0;
  bool block_restricted_symbols = true;
  bool block_halted_symbols = true;
  bool require_positive_price = true;
};

struct PreTradeGuardInput {
  std::string plan_id;
  std::string account_id;
  std::string symbol;
  std::string venue;
  std::string currency;

  double signed_quantity = 0.0;
  double reference_price = 0.0;
  double estimated_slippage_bps = 0.0;
  double spread_bps = 0.0;
  double average_daily_volume = 0.0;
  double available_cash = 0.0;

  bool restricted_symbol = false;
  bool halted = false;
  bool reduce_only = false;
  bool manual_override = false;
};

struct PreTradeGuardFinding {
  PreTradeDecision decision = PreTradeDecision::kAllow;
  std::string plan_id;
  std::string symbol;
  std::string venue;
  std::string message;
  double observed = 0.0;
  double limit = 0.0;
};

struct PreTradeGuardReport {
  std::vector<PreTradeGuardFinding> findings;
  std::uint64_t allowed = 0;
  std::uint64_t warned = 0;
  std::uint64_t blocked = 0;
  double allowed_notional = 0.0;
  double blocked_notional = 0.0;

  bool ok() const;
};

class AllocationPreTradeGuard {
 public:
  AllocationPreTradeGuard();

  void SetRules(const PreTradeGuardRule& rules);
  void SetAllowManualOverride(bool value);

  PreTradeGuardReport Evaluate(
      const std::vector<PreTradeGuardInput>& orders) const;

 private:
  PreTradeGuardRule rules_;
  bool allow_manual_override_;

  void EvaluateOne(const PreTradeGuardInput& order,
                   PreTradeGuardReport* report) const;

  void AddFinding(PreTradeGuardReport* report,
                  PreTradeDecision decision,
                  const PreTradeGuardInput& order,
                  const std::string& message,
                  double observed,
                  double limit) const;
};

std::string PreTradeDecisionName(PreTradeDecision decision);

}  // namespace marketdata
