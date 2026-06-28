#pragma once

#include <cstdint>
#include <map>
#include <set>
#include <string>
#include <vector>

namespace marketdata {

enum class OrderSide {
  kUnknown,
  kBuy,
  kSell,
};

enum class OrderType {
  kUnknown,
  kMarket,
  kLimit,
  kStop,
  kStopLimit,
};

enum class OrderLifecycleState {
  kUnknown,
  kNew,
  kAccepted,
  kPartiallyFilled,
  kFilled,
  kCanceled,
  kRejected,
  kExpired,
  kReplaced,
};

enum class TradeLifecycleEventType {
  kUnknown,
  kNewOrder,
  kAccepted,
  kRejected,
  kFill,
  kCancelRequest,
  kCanceled,
  kReplaceRequest,
  kReplaced,
  kExpired,
};

enum class LifecycleIssueSeverity {
  kInfo,
  kWarning,
  kError,
  kCritical,
};

enum class LifecycleIssueType {
  kInvalidEvent,
  kDuplicateEvent,
  kDuplicateExecution,
  kUnknownOrder,
  kInvalidTransition,
  kQuantityViolation,
  kPriceViolation,
  kTimestampViolation,
  kTerminalStateViolation,
  kMissingRequiredField,
};

struct TradeLifecycleEvent {
  std::string event_id;
  std::string order_id;
  std::string parent_order_id;
  std::string execution_id;
  std::string account_id;
  std::string symbol;
  std::string venue;
  std::string text;

  TradeLifecycleEventType event_type = TradeLifecycleEventType::kUnknown;
  OrderSide side = OrderSide::kUnknown;
  OrderType order_type = OrderType::kUnknown;

  std::uint64_t timestamp_ns = 0;
  double order_quantity = 0.0;
  double event_quantity = 0.0;
  double limit_price = 0.0;
  double execution_price = 0.0;
  double leaves_quantity = 0.0;
  double cumulative_quantity = 0.0;
};

struct TradeLifecycleIssue {
  LifecycleIssueType type = LifecycleIssueType::kInvalidEvent;
  LifecycleIssueSeverity severity = LifecycleIssueSeverity::kError;
  std::string order_id;
  std::string event_id;
  std::string message;
  std::string expected;
  std::string actual;
};

struct OrderLifecycleSnapshot {
  std::string order_id;
  std::string account_id;
  std::string symbol;
  std::string venue;

  OrderSide side = OrderSide::kUnknown;
  OrderType order_type = OrderType::kUnknown;
  OrderLifecycleState state = OrderLifecycleState::kUnknown;

  std::uint64_t first_timestamp_ns = 0;
  std::uint64_t last_timestamp_ns = 0;

  double original_quantity = 0.0;
  double filled_quantity = 0.0;
  double leaves_quantity = 0.0;
  double limit_price = 0.0;

  std::vector<std::string> event_ids;
  std::vector<std::string> execution_ids;

  bool Terminal() const;
  double RemainingQuantity() const;
};

struct TradeLifecycleReport {
  std::vector<TradeLifecycleIssue> issues;
  std::vector<OrderLifecycleSnapshot> orders;

  std::uint64_t processed_events = 0;
  std::uint64_t accepted_orders = 0;
  std::uint64_t rejected_orders = 0;
  std::uint64_t filled_orders = 0;
  std::uint64_t canceled_orders = 0;
  std::uint64_t expired_orders = 0;

  bool Ok() const;
  std::uint64_t ErrorCount() const;
  std::uint64_t WarningCount() const;
};

class TradeLifecycleValidator {
 public:
  TradeLifecycleValidator();

  void SetQuantityTolerance(double value);
  void SetPriceTolerance(double value);
  void SetRequireMonotonicTimestamps(bool value);
  void SetAllowTerminalCorrections(bool value);

  TradeLifecycleReport Validate(
      const std::vector<TradeLifecycleEvent>& events) const;

 private:
  double quantity_tolerance_;
  double price_tolerance_;
  bool require_monotonic_timestamps_;
  bool allow_terminal_corrections_;

  void ApplyEvent(
      const TradeLifecycleEvent& event,
      std::map<std::string, OrderLifecycleSnapshot>* orders,
      std::set<std::string>* seen_event_ids,
      std::set<std::string>* seen_execution_ids,
      TradeLifecycleReport* report) const;

  bool ValidateCommonFields(
      const TradeLifecycleEvent& event,
      TradeLifecycleReport* report) const;

  bool ValidateNewOrder(
      const TradeLifecycleEvent& event,
      OrderLifecycleSnapshot* snapshot,
      TradeLifecycleReport* report) const;

  bool ValidateAccepted(
      const TradeLifecycleEvent& event,
      OrderLifecycleSnapshot* snapshot,
      TradeLifecycleReport* report) const;

  bool ValidateRejected(
      const TradeLifecycleEvent& event,
      OrderLifecycleSnapshot* snapshot,
      TradeLifecycleReport* report) const;

  bool ValidateFill(
      const TradeLifecycleEvent& event,
      OrderLifecycleSnapshot* snapshot,
      std::set<std::string>* seen_execution_ids,
      TradeLifecycleReport* report) const;

  bool ValidateCanceled(
      const TradeLifecycleEvent& event,
      OrderLifecycleSnapshot* snapshot,
      TradeLifecycleReport* report) const;

  bool ValidateReplaced(
      const TradeLifecycleEvent& event,
      OrderLifecycleSnapshot* snapshot,
      TradeLifecycleReport* report) const;

  bool ValidateExpired(
      const TradeLifecycleEvent& event,
      OrderLifecycleSnapshot* snapshot,
      TradeLifecycleReport* report) const;

  void AddIssue(
      LifecycleIssueType type,
      LifecycleIssueSeverity severity,
      const TradeLifecycleEvent& event,
      const std::string& message,
      const std::string& expected,
      const std::string& actual,
      TradeLifecycleReport* report) const;
};

std::string OrderSideName(OrderSide side);
std::string OrderTypeName(OrderType type);
std::string OrderStateName(OrderLifecycleState state);
std::string EventTypeName(TradeLifecycleEventType type);
std::string LifecycleSeverityName(LifecycleIssueSeverity severity);
std::string LifecycleIssueTypeName(LifecycleIssueType type);

OrderSide ParseOrderSide(const std::string& value);
OrderType ParseOrderType(const std::string& value);
TradeLifecycleEventType ParseTradeLifecycleEventType(const std::string& value);

}  // namespace marketdata
