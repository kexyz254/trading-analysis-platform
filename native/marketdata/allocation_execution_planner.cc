#include "allocation_execution_planner.h"

#include <algorithm>
#include <cmath>
#include <limits>
#include <stdexcept>

namespace marketdata {
namespace {

bool IsFinite(double value) {
  return std::isfinite(value);
}

double Abs(double value) {
  return value < 0.0 ? -value : value;
}

double Sign(double value) {
  if (value > 0.0) return 1.0;
  if (value < 0.0) return -1.0;
  return 0.0;
}

std::string NonEmptyOr(const std::string& value, const std::string& fallback) {
  return value.empty() ? fallback : value;
}

}  // namespace

bool ExecutionPlan::ok() const {
  for (const ExecutionPlanningIssue& issue : issues) {
    if (issue.fatal) {
      return false;
    }
  }
  return true;
}

AllocationExecutionPlanner::AllocationExecutionPlanner()
    : default_venue_("AUTO"),
      cash_reserve_ratio_(0.02),
      price_band_bps_(20.0) {
  ExecutionVenueRule auto_rule;
  auto_rule.venue = "AUTO";
  venue_rules_[auto_rule.venue] = auto_rule;
}

void AllocationExecutionPlanner::SetDefaultVenue(const std::string& venue) {
  if (venue.empty()) {
    throw std::invalid_argument("default venue cannot be empty");
  }
  default_venue_ = venue;
  if (venue_rules_.find(venue) == venue_rules_.end()) {
    ExecutionVenueRule rule;
    rule.venue = venue;
    venue_rules_[venue] = rule;
  }
}

void AllocationExecutionPlanner::SetCashReserveRatio(double ratio) {
  if (!IsFinite(ratio) || ratio < 0.0 || ratio > 0.95) {
    throw std::invalid_argument("cash reserve ratio must be within [0, 0.95]");
  }
  cash_reserve_ratio_ = ratio;
}

void AllocationExecutionPlanner::SetPriceBandBps(double bps) {
  if (!IsFinite(bps) || bps < 0.0 || bps > 5000.0) {
    throw std::invalid_argument("price band must be finite and reasonable");
  }
  price_band_bps_ = bps;
}

void AllocationExecutionPlanner::SetVenueRule(const ExecutionVenueRule& rule) {
  if (rule.venue.empty()) {
    throw std::invalid_argument("venue rule requires a venue");
  }
  if (rule.lot_size <= 0.0 || !IsFinite(rule.lot_size)) {
    throw std::invalid_argument("venue rule lot size must be positive");
  }
  if (rule.max_participation_rate < 0.0 ||
      rule.max_participation_rate > 1.0 ||
      !IsFinite(rule.max_participation_rate)) {
    throw std::invalid_argument("participation rate must be within [0, 1]");
  }
  venue_rules_[rule.venue] = rule;
}

ExecutionPlan AllocationExecutionPlanner::BuildPlan(
    const std::vector<AllocationExecutionInput>& rows) const {
  ExecutionPlan plan;

  for (const AllocationExecutionInput& row : rows) {
    ExecutionPlanningIssue issue;
    const std::string venue = NonEmptyOr(row.preferred_venue, default_venue_);
    const ExecutionVenueRule* rule = FindVenueRule(venue);

    if (rule == nullptr) {
      issue.symbol = row.symbol;
      issue.venue = venue;
      issue.message = "no venue rule configured";
      issue.detail = "planner cannot route without a venue rule";
      issue.fatal = true;
      plan.skipped_notional += Abs(row.target_quantity - row.current_quantity) *
                               std::max(0.0, row.last_price);
      plan.issues.push_back(issue);
      continue;
    }

    ChildExecutionOrder order = BuildOrder(row, *rule, &issue);
    if (!issue.message.empty()) {
      plan.issues.push_back(issue);
    }

    if (!order.valid) {
      plan.skipped_notional += Abs(row.target_quantity - row.current_quantity) *
                               std::max(0.0, row.last_price);
      continue;
    }

    if (order.intent == ExecutionIntent::kBuy) {
      plan.gross_buy_notional += order.estimated_notional;
    } else if (order.intent == ExecutionIntent::kSell ||
               order.intent == ExecutionIntent::kReduceOnly) {
      plan.gross_sell_notional += order.estimated_notional;
    }

    plan.orders.push_back(order);
  }

  return plan;
}

const ExecutionVenueRule* AllocationExecutionPlanner::FindVenueRule(
    const std::string& venue) const {
  const auto exact = venue_rules_.find(venue);
  if (exact != venue_rules_.end()) {
    return &exact->second;
  }

  const auto fallback = venue_rules_.find(default_venue_);
  if (fallback != venue_rules_.end()) {
    return &fallback->second;
  }

  return nullptr;
}

ChildExecutionOrder AllocationExecutionPlanner::BuildOrder(
    const AllocationExecutionInput& row,
    const ExecutionVenueRule& venue_rule,
    ExecutionPlanningIssue* issue) const {
  ChildExecutionOrder order;
  order.portfolio_id = row.portfolio_id;
  order.account_id = row.account_id;
  order.symbol = row.symbol;
  order.venue = venue_rule.venue;

  issue->symbol = row.symbol;
  issue->venue = venue_rule.venue;

  if (!venue_rule.enabled) {
    issue->message = "venue is disabled";
    issue->detail = "routing rule blocked this venue";
    issue->fatal = false;
    return order;
  }

  if (row.symbol.empty() || row.account_id.empty()) {
    issue->message = "missing allocation identity";
    issue->detail = "symbol and account id are required";
    issue->fatal = true;
    return order;
  }

  if (!IsFinite(row.current_quantity) ||
      !IsFinite(row.target_quantity) ||
      !IsFinite(row.last_price) ||
      row.last_price <= 0.0) {
    issue->message = "invalid execution numeric fields";
    issue->detail = "quantity and price fields must be finite";
    issue->fatal = true;
    return order;
  }

  if (row.restricted) {
    issue->message = "symbol is restricted";
    issue->detail = "planner will not trade restricted symbols";
    issue->fatal = false;
    return order;
  }

  const double desired = row.target_quantity - row.current_quantity;
  const double abs_desired = Abs(desired);
  if (abs_desired <= 0.0) {
    order.intent = ExecutionIntent::kHold;
    order.reason = "already at target quantity";
    return order;
  }

  if (row.reduce_only && desired > 0.0) {
    issue->message = "reduce-only row requested buy";
    issue->detail = "buy side blocked for reduce-only allocation";
    issue->fatal = false;
    return order;
  }

  if (row.spread_bps > venue_rule.spread_bps_limit) {
    issue->message = "spread exceeds venue routing limit";
    issue->detail = "planner skipped order to avoid poor execution quality";
    issue->fatal = false;
    return order;
  }

  double quantity = ClampTradeQuantity(row, venue_rule, desired);
  quantity = RoundToLot(quantity, std::max(row.lot_size, venue_rule.lot_size));

  if (Abs(quantity) <= 0.0) {
    issue->message = "trade rounded below minimum lot";
    issue->detail = "desired trade did not survive lot-size rounding";
    issue->fatal = false;
    return order;
  }

  if (row.cash_account && quantity > 0.0) {
    const double usable_cash = row.available_cash * (1.0 - cash_reserve_ratio_);
    if (quantity * row.last_price > usable_cash) {
      quantity = RoundToLot(usable_cash / row.last_price,
                            std::max(row.lot_size, venue_rule.lot_size));
      if (quantity <= 0.0) {
        issue->message = "insufficient cash after reserve";
        issue->detail = "cash account cannot fund requested buy order";
        issue->fatal = false;
        return order;
      }
    }
  }

  const double price_band = row.last_price * price_band_bps_ / 10000.0;

  order.intent = quantity > 0.0 ? ExecutionIntent::kBuy : ExecutionIntent::kSell;
  if (row.reduce_only && quantity < 0.0) {
    order.intent = ExecutionIntent::kReduceOnly;
  }
  order.urgency = ChooseUrgency(row, quantity);
  order.quantity = Abs(quantity);
  order.estimated_notional = order.quantity * row.last_price;
  order.limit_price = quantity > 0.0 ? row.last_price + price_band
                                     : row.last_price - price_band;
  order.participation_rate =
      row.average_daily_volume > 0.0 ? order.quantity / row.average_daily_volume
                                     : 0.0;
  order.reason = "rebalance toward allocation target";
  order.valid = true;

  return order;
}

ExecutionUrgency AllocationExecutionPlanner::ChooseUrgency(
    const AllocationExecutionInput& row,
    double trade_quantity) const {
  if (row.restricted) {
    return ExecutionUrgency::kDoNotTrade;
  }

  const double abs_trade = Abs(trade_quantity);
  if (abs_trade <= row.minimum_trade_quantity && row.minimum_trade_quantity > 0.0) {
    return ExecutionUrgency::kPassive;
  }

  if (row.risk_reduction_score >= 0.80 && trade_quantity < 0.0) {
    return ExecutionUrgency::kUrgent;
  }

  if (row.spread_bps > 40.0) {
    return ExecutionUrgency::kPassive;
  }

  return ExecutionUrgency::kNormal;
}

double AllocationExecutionPlanner::RoundToLot(double quantity,
                                              double lot_size) const {
  if (lot_size <= 0.0 || !IsFinite(lot_size)) {
    return quantity;
  }

  const double direction = Sign(quantity);
  const double lots = std::floor(Abs(quantity) / lot_size);
  return direction * lots * lot_size;
}

double AllocationExecutionPlanner::ClampTradeQuantity(
    const AllocationExecutionInput& row,
    const ExecutionVenueRule& venue_rule,
    double desired_quantity) const {
  double capped = desired_quantity;
  const double direction = Sign(desired_quantity);

  if (row.maximum_trade_quantity > 0.0) {
    capped = direction * std::min(Abs(capped), row.maximum_trade_quantity);
  }

  if (venue_rule.max_child_notional > 0.0 && row.last_price > 0.0) {
    const double max_child_qty = venue_rule.max_child_notional / row.last_price;
    capped = direction * std::min(Abs(capped), max_child_qty);
  }

  if (row.average_daily_volume > 0.0 && venue_rule.max_participation_rate > 0.0) {
    const double max_participation_qty =
        row.average_daily_volume * venue_rule.max_participation_rate;
    capped = direction * std::min(Abs(capped), max_participation_qty);
  }

  const double min_quantity =
      std::max(row.minimum_trade_quantity, venue_rule.min_child_quantity);
  if (min_quantity > 0.0 && Abs(capped) < min_quantity) {
    return 0.0;
  }

  return capped;
}

std::string ExecutionIntentName(ExecutionIntent intent) {
  switch (intent) {
    case ExecutionIntent::kNone:
      return "none";
    case ExecutionIntent::kBuy:
      return "buy";
    case ExecutionIntent::kSell:
      return "sell";
    case ExecutionIntent::kHold:
      return "hold";
    case ExecutionIntent::kReduceOnly:
      return "reduce_only";
  }
  return "unknown";
}

std::string ExecutionUrgencyName(ExecutionUrgency urgency) {
  switch (urgency) {
    case ExecutionUrgency::kPassive:
      return "passive";
    case ExecutionUrgency::kNormal:
      return "normal";
    case ExecutionUrgency::kUrgent:
      return "urgent";
    case ExecutionUrgency::kDoNotTrade:
      return "do_not_trade";
  }
  return "unknown";
}

}  // namespace marketdata
