#include "trade_lifecycle_validator.h"

#include <algorithm>
#include <cmath>
#include <iomanip>
#include <sstream>
#include <stdexcept>

namespace marketdata {
namespace {

std::string TrimLifecycleText(const std::string& value) {
  std::size_t begin = 0;
  while (begin < value.size() &&
         std::isspace(static_cast<unsigned char>(value[begin]))) {
    ++begin;
  }

  std::size_t end = value.size();
  while (end > begin &&
         std::isspace(static_cast<unsigned char>(value[end - 1]))) {
    --end;
  }

  return value.substr(begin, end - begin);
}

std::string UpperLifecycleText(const std::string& value) {
  std::string out = TrimLifecycleText(value);
  std::transform(out.begin(), out.end(), out.begin(), [](unsigned char c) {
    return static_cast<char>(std::toupper(c));
  });
  return out;
}

std::string LifecycleNumber(double value) {
  std::ostringstream out;
  out << std::fixed << std::setprecision(6) << value;
  return out.str();
}

bool IsLifecycleError(LifecycleIssueSeverity severity) {
  return severity == LifecycleIssueSeverity::kError ||
         severity == LifecycleIssueSeverity::kCritical;
}

bool IsPositiveFinite(double value) {
  return value > 0.0 && std::isfinite(value);
}

bool IsNonNegativeFinite(double value) {
  return value >= 0.0 && std::isfinite(value);
}

bool IsTerminalState(OrderLifecycleState state) {
  return state == OrderLifecycleState::kFilled ||
         state == OrderLifecycleState::kCanceled ||
         state == OrderLifecycleState::kRejected ||
         state == OrderLifecycleState::kExpired;
}

}  // namespace

bool OrderLifecycleSnapshot::Terminal() const {
  return IsTerminalState(state);
}

double OrderLifecycleSnapshot::RemainingQuantity() const {
  const double remaining = original_quantity - filled_quantity;
  return remaining < 0.0 ? 0.0 : remaining;
}

bool TradeLifecycleReport::Ok() const {
  for (const TradeLifecycleIssue& issue : issues) {
    if (IsLifecycleError(issue.severity)) {
      return false;
    }
  }
  return true;
}

std::uint64_t TradeLifecycleReport::ErrorCount() const {
  std::uint64_t total = 0;
  for (const TradeLifecycleIssue& issue : issues) {
    if (IsLifecycleError(issue.severity)) {
      ++total;
    }
  }
  return total;
}

std::uint64_t TradeLifecycleReport::WarningCount() const {
  std::uint64_t total = 0;
  for (const TradeLifecycleIssue& issue : issues) {
    if (issue.severity == LifecycleIssueSeverity::kWarning) {
      ++total;
    }
  }
  return total;
}

TradeLifecycleValidator::TradeLifecycleValidator()
    : quantity_tolerance_(0.000001),
      price_tolerance_(0.000001),
      require_monotonic_timestamps_(true),
      allow_terminal_corrections_(false) {}

void TradeLifecycleValidator::SetQuantityTolerance(double value) {
  if (!IsNonNegativeFinite(value)) {
    throw std::invalid_argument("quantity tolerance must be finite and non-negative");
  }
  quantity_tolerance_ = value;
}

void TradeLifecycleValidator::SetPriceTolerance(double value) {
  if (!IsNonNegativeFinite(value)) {
    throw std::invalid_argument("price tolerance must be finite and non-negative");
  }
  price_tolerance_ = value;
}

void TradeLifecycleValidator::SetRequireMonotonicTimestamps(bool value) {
  require_monotonic_timestamps_ = value;
}

void TradeLifecycleValidator::SetAllowTerminalCorrections(bool value) {
  allow_terminal_corrections_ = value;
}

TradeLifecycleReport TradeLifecycleValidator::Validate(
    const std::vector<TradeLifecycleEvent>& events) const {
  TradeLifecycleReport report;
  std::map<std::string, OrderLifecycleSnapshot> orders;
  std::set<std::string> seen_event_ids;
  std::set<std::string> seen_execution_ids;

  for (const TradeLifecycleEvent& event : events) {
    ++report.processed_events;
    ApplyEvent(event, &orders, &seen_event_ids, &seen_execution_ids, &report);
  }

  for (const auto& item : orders) {
    report.orders.push_back(item.second);

    switch (item.second.state) {
      case OrderLifecycleState::kAccepted:
      case OrderLifecycleState::kPartiallyFilled:
      case OrderLifecycleState::kReplaced:
      case OrderLifecycleState::kNew:
        ++report.accepted_orders;
        break;
      case OrderLifecycleState::kRejected:
        ++report.rejected_orders;
        break;
      case OrderLifecycleState::kFilled:
        ++report.filled_orders;
        break;
      case OrderLifecycleState::kCanceled:
        ++report.canceled_orders;
        break;
      case OrderLifecycleState::kExpired:
        ++report.expired_orders;
        break;
      case OrderLifecycleState::kUnknown:
        break;
    }
  }

  return report;
}

void TradeLifecycleValidator::ApplyEvent(
    const TradeLifecycleEvent& event,
    std::map<std::string, OrderLifecycleSnapshot>* orders,
    std::set<std::string>* seen_event_ids,
    std::set<std::string>* seen_execution_ids,
    TradeLifecycleReport* report) const {
  if (!ValidateCommonFields(event, report)) {
    return;
  }

  if (!event.event_id.empty()) {
    if (!seen_event_ids->insert(event.event_id).second) {
      AddIssue(
          LifecycleIssueType::kDuplicateEvent,
          LifecycleIssueSeverity::kError,
          event,
          "duplicate lifecycle event id",
          "unique event_id",
          event.event_id,
          report);
      return;
    }
  }

  OrderLifecycleSnapshot* snapshot = nullptr;

  const auto existing_it = orders->find(event.order_id);
  if (existing_it == orders->end()) {
    OrderLifecycleSnapshot created;
    created.order_id = event.order_id;
    created.account_id = event.account_id;
    created.symbol = event.symbol;
    created.venue = event.venue;
    snapshot = &orders->insert(std::make_pair(event.order_id, created)).first->second;
  } else {
    snapshot = &existing_it->second;
  }

  if (snapshot->Terminal() && !allow_terminal_corrections_) {
    AddIssue(
        LifecycleIssueType::kTerminalStateViolation,
        LifecycleIssueSeverity::kError,
        event,
        "event received after terminal order state",
        "no event after " + OrderStateName(snapshot->state),
        EventTypeName(event.event_type),
        report);
    return;
  }

  if (require_monotonic_timestamps_ &&
      snapshot->last_timestamp_ns != 0 &&
      event.timestamp_ns < snapshot->last_timestamp_ns) {
    AddIssue(
        LifecycleIssueType::kTimestampViolation,
        LifecycleIssueSeverity::kError,
        event,
        "event timestamp moved backwards",
        std::to_string(snapshot->last_timestamp_ns),
        std::to_string(event.timestamp_ns),
        report);
    return;
  }

  bool applied = false;

  switch (event.event_type) {
    case TradeLifecycleEventType::kNewOrder:
      applied = ValidateNewOrder(event, snapshot, report);
      break;
    case TradeLifecycleEventType::kAccepted:
      applied = ValidateAccepted(event, snapshot, report);
      break;
    case TradeLifecycleEventType::kRejected:
      applied = ValidateRejected(event, snapshot, report);
      break;
    case TradeLifecycleEventType::kFill:
      applied = ValidateFill(event, snapshot, seen_execution_ids, report);
      break;
    case TradeLifecycleEventType::kCanceled:
      applied = ValidateCanceled(event, snapshot, report);
      break;
    case TradeLifecycleEventType::kReplaced:
      applied = ValidateReplaced(event, snapshot, report);
      break;
    case TradeLifecycleEventType::kExpired:
      applied = ValidateExpired(event, snapshot, report);
      break;
    case TradeLifecycleEventType::kCancelRequest:
    case TradeLifecycleEventType::kReplaceRequest:
      applied = true;
      break;
    case TradeLifecycleEventType::kUnknown:
      AddIssue(
          LifecycleIssueType::kInvalidEvent,
          LifecycleIssueSeverity::kError,
          event,
          "unknown lifecycle event type",
          "known event type",
          "unknown",
          report);
      break;
  }

  if (applied) {
    if (snapshot->first_timestamp_ns == 0) {
      snapshot->first_timestamp_ns = event.timestamp_ns;
    }

    snapshot->last_timestamp_ns = event.timestamp_ns;

    if (!event.event_id.empty()) {
      snapshot->event_ids.push_back(event.event_id);
    }
  }
}

bool TradeLifecycleValidator::ValidateCommonFields(
    const TradeLifecycleEvent& event,
    TradeLifecycleReport* report) const {
  if (event.order_id.empty()) {
    AddIssue(
        LifecycleIssueType::kMissingRequiredField,
        LifecycleIssueSeverity::kError,
        event,
        "event is missing order_id",
        "non-empty order_id",
        "empty",
        report);
    return false;
  }

  if (event.timestamp_ns == 0) {
    AddIssue(
        LifecycleIssueType::kMissingRequiredField,
        LifecycleIssueSeverity::kError,
        event,
        "event is missing timestamp",
        "non-zero timestamp_ns",
        "0",
        report);
    return false;
  }

  if (event.event_type == TradeLifecycleEventType::kUnknown) {
    AddIssue(
        LifecycleIssueType::kInvalidEvent,
        LifecycleIssueSeverity::kError,
        event,
        "event type is unknown",
        "known event type",
        "unknown",
        report);
    return false;
  }

  return true;
}

bool TradeLifecycleValidator::ValidateNewOrder(
    const TradeLifecycleEvent& event,
    OrderLifecycleSnapshot* snapshot,
    TradeLifecycleReport* report) const {
  if (snapshot->state != OrderLifecycleState::kUnknown) {
    AddIssue(
        LifecycleIssueType::kInvalidTransition,
        LifecycleIssueSeverity::kError,
        event,
        "new order event received after order already exists",
        "unknown state",
        OrderStateName(snapshot->state),
        report);
    return false;
  }

  if (event.account_id.empty() || event.symbol.empty()) {
    AddIssue(
        LifecycleIssueType::kMissingRequiredField,
        LifecycleIssueSeverity::kError,
        event,
        "new order is missing account or symbol",
        "account_id and symbol",
        event.account_id + ":" + event.symbol,
        report);
    return false;
  }

  if (event.side == OrderSide::kUnknown || event.order_type == OrderType::kUnknown) {
    AddIssue(
        LifecycleIssueType::kInvalidEvent,
        LifecycleIssueSeverity::kError,
        event,
        "new order has invalid side or order type",
        "known side and type",
        OrderSideName(event.side) + ":" + OrderTypeName(event.order_type),
        report);
    return false;
  }

  if (!IsPositiveFinite(event.order_quantity)) {
    AddIssue(
        LifecycleIssueType::kQuantityViolation,
        LifecycleIssueSeverity::kError,
        event,
        "new order quantity must be positive",
        "positive finite quantity",
        LifecycleNumber(event.order_quantity),
        report);
    return false;
  }

  if ((event.order_type == OrderType::kLimit ||
       event.order_type == OrderType::kStopLimit) &&
      !IsPositiveFinite(event.limit_price)) {
    AddIssue(
        LifecycleIssueType::kPriceViolation,
        LifecycleIssueSeverity::kError,
        event,
        "limit order must include positive limit price",
        "positive finite price",
        LifecycleNumber(event.limit_price),
        report);
    return false;
  }

  snapshot->account_id = event.account_id;
  snapshot->symbol = event.symbol;
  snapshot->venue = event.venue;
  snapshot->side = event.side;
  snapshot->order_type = event.order_type;
  snapshot->state = OrderLifecycleState::kNew;
  snapshot->original_quantity = event.order_quantity;
  snapshot->leaves_quantity = event.order_quantity;
  snapshot->limit_price = event.limit_price;

  return true;
}

bool TradeLifecycleValidator::ValidateAccepted(
    const TradeLifecycleEvent& event,
    OrderLifecycleSnapshot* snapshot,
    TradeLifecycleReport* report) const {
  if (snapshot->state != OrderLifecycleState::kNew &&
      snapshot->state != OrderLifecycleState::kUnknown) {
    AddIssue(
        LifecycleIssueType::kInvalidTransition,
        LifecycleIssueSeverity::kError,
        event,
        "accepted event is only valid after new order",
        "new or unknown",
        OrderStateName(snapshot->state),
        report);
    return false;
  }

  if (snapshot->state == OrderLifecycleState::kUnknown) {
    snapshot->account_id = event.account_id;
    snapshot->symbol = event.symbol;
    snapshot->venue = event.venue;
    snapshot->side = event.side;
    snapshot->order_type = event.order_type;
    snapshot->original_quantity = event.order_quantity;
    snapshot->leaves_quantity = event.order_quantity;
    snapshot->limit_price = event.limit_price;
  }

  if (!IsPositiveFinite(snapshot->original_quantity)) {
    AddIssue(
        LifecycleIssueType::kQuantityViolation,
        LifecycleIssueSeverity::kError,
        event,
        "accepted order has no positive original quantity",
        "positive original quantity",
        LifecycleNumber(snapshot->original_quantity),
        report);
    return false;
  }

  snapshot->state = OrderLifecycleState::kAccepted;
  return true;
}

bool TradeLifecycleValidator::ValidateRejected(
    const TradeLifecycleEvent& event,
    OrderLifecycleSnapshot* snapshot,
    TradeLifecycleReport* report) const {
  if (snapshot->state != OrderLifecycleState::kNew &&
      snapshot->state != OrderLifecycleState::kUnknown) {
    AddIssue(
        LifecycleIssueType::kInvalidTransition,
        LifecycleIssueSeverity::kError,
        event,
        "rejected event is only valid before acceptance",
        "new or unknown",
        OrderStateName(snapshot->state),
        report);
    return false;
  }

  snapshot->state = OrderLifecycleState::kRejected;
  snapshot->leaves_quantity = 0.0;
  return true;
}

bool TradeLifecycleValidator::ValidateFill(
    const TradeLifecycleEvent& event,
    OrderLifecycleSnapshot* snapshot,
    std::set<std::string>* seen_execution_ids,
    TradeLifecycleReport* report) const {
  if (event.execution_id.empty()) {
    AddIssue(
        LifecycleIssueType::kMissingRequiredField,
        LifecycleIssueSeverity::kError,
        event,
        "fill event is missing execution_id",
        "non-empty execution_id",
        "empty",
        report);
    return false;
  }

  if (!seen_execution_ids->insert(event.execution_id).second) {
    AddIssue(
        LifecycleIssueType::kDuplicateExecution,
        LifecycleIssueSeverity::kError,
        event,
        "duplicate execution id",
        "unique execution_id",
        event.execution_id,
        report);
    return false;
  }

  if (snapshot->state != OrderLifecycleState::kAccepted &&
      snapshot->state != OrderLifecycleState::kPartiallyFilled &&
      snapshot->state != OrderLifecycleState::kReplaced) {
    AddIssue(
        LifecycleIssueType::kInvalidTransition,
        LifecycleIssueSeverity::kError,
        event,
        "fill is only valid for live accepted orders",
        "accepted, partially_filled, or replaced",
        OrderStateName(snapshot->state),
        report);
    return false;
  }

  if (!IsPositiveFinite(event.event_quantity)) {
    AddIssue(
        LifecycleIssueType::kQuantityViolation,
        LifecycleIssueSeverity::kError,
        event,
        "fill quantity must be positive",
        "positive fill quantity",
        LifecycleNumber(event.event_quantity),
        report);
    return false;
  }

  if (!IsPositiveFinite(event.execution_price)) {
    AddIssue(
        LifecycleIssueType::kPriceViolation,
        LifecycleIssueSeverity::kError,
        event,
        "fill execution price must be positive",
        "positive execution price",
        LifecycleNumber(event.execution_price),
        report);
    return false;
  }

  const double new_filled_quantity =
      snapshot->filled_quantity + event.event_quantity;

  if (new_filled_quantity - snapshot->original_quantity > quantity_tolerance_) {
    AddIssue(
        LifecycleIssueType::kQuantityViolation,
        LifecycleIssueSeverity::kCritical,
        event,
        "fill quantity exceeds original order quantity",
        LifecycleNumber(snapshot->original_quantity),
        LifecycleNumber(new_filled_quantity),
        report);
    return false;
  }

  snapshot->filled_quantity = new_filled_quantity;
  snapshot->leaves_quantity = snapshot->RemainingQuantity();
  snapshot->execution_ids.push_back(event.execution_id);

  if (snapshot->leaves_quantity <= quantity_tolerance_) {
    snapshot->state = OrderLifecycleState::kFilled;
    snapshot->leaves_quantity = 0.0;
  } else {
    snapshot->state = OrderLifecycleState::kPartiallyFilled;
  }

  return true;
}

bool TradeLifecycleValidator::ValidateCanceled(
    const TradeLifecycleEvent& event,
    OrderLifecycleSnapshot* snapshot,
    TradeLifecycleReport* report) const {
  if (snapshot->state != OrderLifecycleState::kAccepted &&
      snapshot->state != OrderLifecycleState::kPartiallyFilled &&
      snapshot->state != OrderLifecycleState::kReplaced) {
    AddIssue(
        LifecycleIssueType::kInvalidTransition,
        LifecycleIssueSeverity::kError,
        event,
        "cancel is only valid for live orders",
        "accepted, partially_filled, or replaced",
        OrderStateName(snapshot->state),
        report);
    return false;
  }

  snapshot->state = OrderLifecycleState::kCanceled;
  snapshot->leaves_quantity = 0.0;
  return true;
}

bool TradeLifecycleValidator::ValidateReplaced(
    const TradeLifecycleEvent& event,
    OrderLifecycleSnapshot* snapshot,
    TradeLifecycleReport* report) const {
  if (snapshot->state != OrderLifecycleState::kAccepted &&
      snapshot->state != OrderLifecycleState::kPartiallyFilled &&
      snapshot->state != OrderLifecycleState::kReplaced) {
    AddIssue(
        LifecycleIssueType::kInvalidTransition,
        LifecycleIssueSeverity::kError,
        event,
        "replace is only valid for live orders",
        "accepted, partially_filled, or replaced",
        OrderStateName(snapshot->state),
        report);
    return false;
  }

  if (event.order_quantity > 0.0) {
    if (event.order_quantity + quantity_tolerance_ < snapshot->filled_quantity) {
      AddIssue(
          LifecycleIssueType::kQuantityViolation,
          LifecycleIssueSeverity::kError,
          event,
          "replacement quantity cannot be below filled quantity",
          LifecycleNumber(snapshot->filled_quantity),
          LifecycleNumber(event.order_quantity),
          report);
      return false;
    }

    snapshot->original_quantity = event.order_quantity;
  }

  if (event.limit_price > 0.0) {
    snapshot->limit_price = event.limit_price;
  }

  snapshot->leaves_quantity = snapshot->RemainingQuantity();
  snapshot->state = OrderLifecycleState::kReplaced;
  return true;
}

bool TradeLifecycleValidator::ValidateExpired(
    const TradeLifecycleEvent& event,
    OrderLifecycleSnapshot* snapshot,
    TradeLifecycleReport* report) const {
  if (snapshot->state != OrderLifecycleState::kAccepted &&
      snapshot->state != OrderLifecycleState::kPartiallyFilled &&
      snapshot->state != OrderLifecycleState::kReplaced &&
      snapshot->state != OrderLifecycleState::kNew) {
    AddIssue(
        LifecycleIssueType::kInvalidTransition,
        LifecycleIssueSeverity::kError,
        event,
        "expire is only valid for open orders",
        "new, accepted, partially_filled, or replaced",
        OrderStateName(snapshot->state),
        report);
    return false;
  }

  snapshot->state = OrderLifecycleState::kExpired;
  snapshot->leaves_quantity = 0.0;
  return true;
}

void TradeLifecycleValidator::AddIssue(
    LifecycleIssueType type,
    LifecycleIssueSeverity severity,
    const TradeLifecycleEvent& event,
    const std::string& message,
    const std::string& expected,
    const std::string& actual,
    TradeLifecycleReport* report) const {
  report->issues.push_back(TradeLifecycleIssue{
      type,
      severity,
      event.order_id,
      event.event_id,
      message,
      expected,
      actual,
  });
}

std::string OrderSideName(OrderSide side) {
  switch (side) {
    case OrderSide::kBuy:
      return "buy";
    case OrderSide::kSell:
      return "sell";
    case OrderSide::kUnknown:
      return "unknown";
  }

  return "unknown";
}

std::string OrderTypeName(OrderType type) {
  switch (type) {
    case OrderType::kMarket:
      return "market";
    case OrderType::kLimit:
      return "limit";
    case OrderType::kStop:
      return "stop";
    case OrderType::kStopLimit:
      return "stop_limit";
    case OrderType::kUnknown:
      return "unknown";
  }

  return "unknown";
}

std::string OrderStateName(OrderLifecycleState state) {
  switch (state) {
    case OrderLifecycleState::kNew:
      return "new";
    case OrderLifecycleState::kAccepted:
      return "accepted";
    case OrderLifecycleState::kPartiallyFilled:
      return "partially_filled";
    case OrderLifecycleState::kFilled:
      return "filled";
    case OrderLifecycleState::kCanceled:
      return "canceled";
    case OrderLifecycleState::kRejected:
      return "rejected";
    case OrderLifecycleState::kExpired:
      return "expired";
    case OrderLifecycleState::kReplaced:
      return "replaced";
    case OrderLifecycleState::kUnknown:
      return "unknown";
  }

  return "unknown";
}

std::string EventTypeName(TradeLifecycleEventType type) {
  switch (type) {
    case TradeLifecycleEventType::kNewOrder:
      return "new_order";
    case TradeLifecycleEventType::kAccepted:
      return "accepted";
    case TradeLifecycleEventType::kRejected:
      return "rejected";
    case TradeLifecycleEventType::kFill:
      return "fill";
    case TradeLifecycleEventType::kCancelRequest:
      return "cancel_request";
    case TradeLifecycleEventType::kCanceled:
      return "canceled";
    case TradeLifecycleEventType::kReplaceRequest:
      return "replace_request";
    case TradeLifecycleEventType::kReplaced:
      return "replaced";
    case TradeLifecycleEventType::kExpired:
      return "expired";
    case TradeLifecycleEventType::kUnknown:
      return "unknown";
  }

  return "unknown";
}

std::string LifecycleSeverityName(LifecycleIssueSeverity severity) {
  switch (severity) {
    case LifecycleIssueSeverity::kInfo:
      return "info";
    case LifecycleIssueSeverity::kWarning:
      return "warning";
    case LifecycleIssueSeverity::kError:
      return "error";
    case LifecycleIssueSeverity::kCritical:
      return "critical";
  }

  return "unknown";
}

std::string LifecycleIssueTypeName(LifecycleIssueType type) {
  switch (type) {
    case LifecycleIssueType::kInvalidEvent:
      return "invalid_event";
    case LifecycleIssueType::kDuplicateEvent:
      return "duplicate_event";
    case LifecycleIssueType::kDuplicateExecution:
      return "duplicate_execution";
    case LifecycleIssueType::kUnknownOrder:
      return "unknown_order";
    case LifecycleIssueType::kInvalidTransition:
      return "invalid_transition";
    case LifecycleIssueType::kQuantityViolation:
      return "quantity_violation";
    case LifecycleIssueType::kPriceViolation:
      return "price_violation";
    case LifecycleIssueType::kTimestampViolation:
      return "timestamp_violation";
    case LifecycleIssueType::kTerminalStateViolation:
      return "terminal_state_violation";
    case LifecycleIssueType::kMissingRequiredField:
      return "missing_required_field";
  }

  return "unknown";
}

OrderSide ParseOrderSide(const std::string& value) {
  const std::string normalized = UpperLifecycleText(value);

  if (normalized == "BUY" || normalized == "B") {
    return OrderSide::kBuy;
  }

  if (normalized == "SELL" || normalized == "S") {
    return OrderSide::kSell;
  }

  return OrderSide::kUnknown;
}

OrderType ParseOrderType(const std::string& value) {
  const std::string normalized = UpperLifecycleText(value);

  if (normalized == "MARKET" || normalized == "MKT") {
    return OrderType::kMarket;
  }

  if (normalized == "LIMIT" || normalized == "LMT") {
    return OrderType::kLimit;
  }

  if (normalized == "STOP") {
    return OrderType::kStop;
  }

  if (normalized == "STOP_LIMIT" || normalized == "STOPLIMIT" || normalized == "STP_LMT") {
    return OrderType::kStopLimit;
  }

  return OrderType::kUnknown;
}

TradeLifecycleEventType ParseTradeLifecycleEventType(const std::string& value) {
  const std::string normalized = UpperLifecycleText(value);

  if (normalized == "NEW" || normalized == "NEW_ORDER") {
    return TradeLifecycleEventType::kNewOrder;
  }

  if (normalized == "ACCEPTED" || normalized == "ACK" || normalized == "ACCEPT") {
    return TradeLifecycleEventType::kAccepted;
  }

  if (normalized == "REJECTED" || normalized == "REJECT") {
    return TradeLifecycleEventType::kRejected;
  }

  if (normalized == "FILL" || normalized == "TRADE" || normalized == "EXECUTION") {
    return TradeLifecycleEventType::kFill;
  }

  if (normalized == "CANCEL_REQUEST" || normalized == "PENDING_CANCEL") {
    return TradeLifecycleEventType::kCancelRequest;
  }

  if (normalized == "CANCELED" || normalized == "CANCELLED") {
    return TradeLifecycleEventType::kCanceled;
  }

  if (normalized == "REPLACE_REQUEST" || normalized == "PENDING_REPLACE") {
    return TradeLifecycleEventType::kReplaceRequest;
  }

  if (normalized == "REPLACED" || normalized == "AMENDED") {
    return TradeLifecycleEventType::kReplaced;
  }

  if (normalized == "EXPIRED") {
    return TradeLifecycleEventType::kExpired;
  }

  return TradeLifecycleEventType::kUnknown;
}

}  // namespace marketdata
