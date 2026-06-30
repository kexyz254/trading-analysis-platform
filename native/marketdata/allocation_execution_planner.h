#pragma once

#include <cstdint>
#include <map>
#include <string>
#include <vector>

namespace marketdata {

enum class ExecutionIntent {
  kNone,
  kBuy,
  kSell,
  kHold,
  kReduceOnly,
};

enum class ExecutionUrgency {
  kPassive,
  kNormal,
  kUrgent,
  kDoNotTrade,
};

struct ExecutionVenueRule {
  std::string venue;
  bool enabled = true;
  bool allow_market_orders = true;
  bool allow_limit_orders = true;
  double max_participation_rate = 0.10;
  double max_child_notional = 250000.0;
  double min_child_quantity = 1.0;
  double lot_size = 1.0;
  double spread_bps_limit = 75.0;
};

struct AllocationExecutionInput {
  std::string portfolio_id;
  std::string account_id;
  std::string symbol;
  std::string preferred_venue;
  std::string currency;

  double current_quantity = 0.0;
  double target_quantity = 0.0;
  double last_price = 0.0;
  double average_daily_volume = 0.0;
  double spread_bps = 0.0;
  double available_cash = 0.0;
  double risk_reduction_score = 0.0;
  double minimum_trade_quantity = 0.0;
  double maximum_trade_quantity = 0.0;
  double lot_size = 1.0;

  bool restricted = false;
  bool reduce_only = false;
  bool cash_account = false;
};

struct ChildExecutionOrder {
  std::string portfolio_id;
  std::string account_id;
  std::string symbol;
  std::string venue;
  std::string reason;

  ExecutionIntent intent = ExecutionIntent::kNone;
  ExecutionUrgency urgency = ExecutionUrgency::kNormal;

  double quantity = 0.0;
  double estimated_notional = 0.0;
  double limit_price = 0.0;
  double participation_rate = 0.0;

  bool valid = false;
};

struct ExecutionPlanningIssue {
  std::string symbol;
  std::string venue;
  std::string message;
  std::string detail;
  bool fatal = false;
};

struct ExecutionPlan {
  std::vector<ChildExecutionOrder> orders;
  std::vector<ExecutionPlanningIssue> issues;

  double gross_buy_notional = 0.0;
  double gross_sell_notional = 0.0;
  double skipped_notional = 0.0;

  bool ok() const;
};

class AllocationExecutionPlanner {
 public:
  AllocationExecutionPlanner();

  void SetDefaultVenue(const std::string& venue);
  void SetCashReserveRatio(double ratio);
  void SetPriceBandBps(double bps);
  void SetVenueRule(const ExecutionVenueRule& rule);

  ExecutionPlan BuildPlan(
      const std::vector<AllocationExecutionInput>& rows) const;

 private:
  std::string default_venue_;
  double cash_reserve_ratio_;
  double price_band_bps_;
  std::map<std::string, ExecutionVenueRule> venue_rules_;

  const ExecutionVenueRule* FindVenueRule(const std::string& venue) const;
  ChildExecutionOrder BuildOrder(const AllocationExecutionInput& row,
                                 const ExecutionVenueRule& venue_rule,
                                 ExecutionPlanningIssue* issue) const;

  ExecutionUrgency ChooseUrgency(const AllocationExecutionInput& row,
                                 double trade_quantity) const;

  double RoundToLot(double quantity, double lot_size) const;
  double ClampTradeQuantity(const AllocationExecutionInput& row,
                            const ExecutionVenueRule& venue_rule,
                            double desired_quantity) const;
};

std::string ExecutionIntentName(ExecutionIntent intent);
std::string ExecutionUrgencyName(ExecutionUrgency urgency);

}  // namespace marketdata
