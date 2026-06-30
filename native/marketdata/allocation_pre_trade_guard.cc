#include "allocation_pre_trade_guard.h"

#include <cmath>
#include <stdexcept>

namespace marketdata {
namespace {

bool Finite(double value) {
  return std::isfinite(value);
}

double Abs(double value) {
  return value < 0.0 ? -value : value;
}

double Notional(const PreTradeGuardInput& order) {
  if (!Finite(order.signed_quantity) || !Finite(order.reference_price)) {
    return 0.0;
  }
  return Abs(order.signed_quantity) * order.reference_price;
}

}  // namespace

bool PreTradeGuardReport::ok() const {
  return blocked == 0;
}

AllocationPreTradeGuard::AllocationPreTradeGuard()
    : allow_manual_override_(false) {}

void AllocationPreTradeGuard::SetRules(const PreTradeGuardRule& rules) {
  if (!Finite(rules.max_order_notional) ||
      !Finite(rules.max_participation_rate) ||
      !Finite(rules.max_spread_bps) ||
      !Finite(rules.max_slippage_bps) ||
      !Finite(rules.min_cash_after_trade)) {
    throw std::invalid_argument("pre-trade guard rule contains non-finite value");
  }

  if (rules.max_order_notional < 0.0 ||
      rules.max_participation_rate < 0.0 ||
      rules.max_participation_rate > 1.0 ||
      rules.max_spread_bps < 0.0 ||
      rules.max_slippage_bps < 0.0) {
    throw std::invalid_argument("pre-trade guard rule is outside supported bounds");
  }

  rules_ = rules;
}

void AllocationPreTradeGuard::SetAllowManualOverride(bool value) {
  allow_manual_override_ = value;
}

PreTradeGuardReport AllocationPreTradeGuard::Evaluate(
    const std::vector<PreTradeGuardInput>& orders) const {
  PreTradeGuardReport report;

  for (const PreTradeGuardInput& order : orders) {
    EvaluateOne(order, &report);
  }

  return report;
}

void AllocationPreTradeGuard::EvaluateOne(
    const PreTradeGuardInput& order,
    PreTradeGuardReport* report) const {
  const double notional = Notional(order);
  bool blocked = false;
  bool warned = false;

  if (order.plan_id.empty() || order.account_id.empty() ||
      order.symbol.empty() || order.venue.empty()) {
    AddFinding(report, PreTradeDecision::kBlock, order,
               "pre-trade order is missing required identity fields",
               0.0, 0.0);
    blocked = true;
  }

  if (rules_.require_positive_price &&
      (!Finite(order.reference_price) || order.reference_price <= 0.0)) {
    AddFinding(report, PreTradeDecision::kBlock, order,
               "pre-trade order has invalid reference price",
               order.reference_price, 0.0);
    blocked = true;
  }

  if (!Finite(order.signed_quantity) || order.signed_quantity == 0.0) {
    AddFinding(report, PreTradeDecision::kBlock, order,
               "pre-trade order has invalid signed quantity",
               order.signed_quantity, 0.0);
    blocked = true;
  }

  if (rules_.block_restricted_symbols &&
      order.restricted_symbol &&
      !(allow_manual_override_ && order.manual_override)) {
    AddFinding(report, PreTradeDecision::kBlock, order,
               "restricted symbol blocked by pre-trade guard",
               1.0, 0.0);
    blocked = true;
  }

  if (rules_.block_halted_symbols &&
      order.halted &&
      !(allow_manual_override_ && order.manual_override)) {
    AddFinding(report, PreTradeDecision::kBlock, order,
               "halted symbol blocked by pre-trade guard",
               1.0, 0.0);
    blocked = true;
  }

  if (order.reduce_only && order.signed_quantity > 0.0) {
    AddFinding(report, PreTradeDecision::kBlock, order,
               "reduce-only allocation attempted to increase exposure",
               order.signed_quantity, 0.0);
    blocked = true;
  }

  if (notional > rules_.max_order_notional &&
      !(allow_manual_override_ && order.manual_override)) {
    AddFinding(report, PreTradeDecision::kBlock, order,
               "order notional exceeds pre-trade hard limit",
               notional, rules_.max_order_notional);
    blocked = true;
  }

  if (Finite(order.average_daily_volume) &&
      order.average_daily_volume > 0.0) {
    const double participation = Abs(order.signed_quantity) /
                                 order.average_daily_volume;
    if (participation > rules_.max_participation_rate) {
      AddFinding(report, PreTradeDecision::kWarn, order,
                 "order participation exceeds configured limit",
                 participation, rules_.max_participation_rate);
      warned = true;
    }
  }

  if (Finite(order.spread_bps) &&
      order.spread_bps > rules_.max_spread_bps) {
    AddFinding(report, PreTradeDecision::kWarn, order,
               "spread is wider than pre-trade threshold",
               order.spread_bps, rules_.max_spread_bps);
    warned = true;
  }

  if (Finite(order.estimated_slippage_bps) &&
      order.estimated_slippage_bps > rules_.max_slippage_bps) {
    AddFinding(report, PreTradeDecision::kWarn, order,
               "estimated slippage exceeds pre-trade threshold",
               order.estimated_slippage_bps, rules_.max_slippage_bps);
    warned = true;
  }

  if (order.signed_quantity > 0.0 && Finite(order.available_cash)) {
    const double cash_after_trade = order.available_cash - notional;
    if (cash_after_trade < rules_.min_cash_after_trade) {
      AddFinding(report, PreTradeDecision::kBlock, order,
                 "cash after buy would fall below minimum buffer",
                 cash_after_trade, rules_.min_cash_after_trade);
      blocked = true;
    }
  }

  if (blocked) {
    report->blocked_notional += notional;
    return;
  }

  if (warned) {
    report->allowed_notional += notional;
    return;
  }

  AddFinding(report, PreTradeDecision::kAllow, order,
             "pre-trade checks passed",
             notional, rules_.max_order_notional);
  report->allowed_notional += notional;
}

void AllocationPreTradeGuard::AddFinding(
    PreTradeGuardReport* report,
    PreTradeDecision decision,
    const PreTradeGuardInput& order,
    const std::string& message,
    double observed,
    double limit) const {
  PreTradeGuardFinding finding;
  finding.decision = decision;
  finding.plan_id = order.plan_id;
  finding.symbol = order.symbol;
  finding.venue = order.venue;
  finding.message = message;
  finding.observed = observed;
  finding.limit = limit;

  switch (decision) {
    case PreTradeDecision::kAllow:
      ++report->allowed;
      break;
    case PreTradeDecision::kWarn:
      ++report->warned;
      break;
    case PreTradeDecision::kBlock:
      ++report->blocked;
      break;
  }

  report->findings.push_back(finding);
}

std::string PreTradeDecisionName(PreTradeDecision decision) {
  switch (decision) {
    case PreTradeDecision::kAllow:
      return "allow";
    case PreTradeDecision::kWarn:
      return "warn";
    case PreTradeDecision::kBlock:
      return "block";
  }

  return "unknown";
}

}  // namespace marketdata
