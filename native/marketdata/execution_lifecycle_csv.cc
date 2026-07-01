#include "native/marketdata/execution_lifecycle_csv.h"

#include <algorithm>
#include <charconv>
#include <cctype>
#include <limits>
#include <set>
#include <system_error>

namespace marketdata {
namespace {

std::string TrimAscii(const std::string& value) {
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

std::string UpperAscii(const std::string& value) {
  std::string out;
  out.reserve(value.size());

  for (char c : value) {
    out.push_back(static_cast<char>(
        std::toupper(static_cast<unsigned char>(c))));
  }

  return out;
}

bool IsDigit(char c) {
  return c >= '0' && c <= '9';
}

std::vector<std::string> SplitLines(const std::string& input) {
  std::vector<std::string> lines;
  std::string current;

  for (char c : input) {
    if (c == '\n') {
      if (!current.empty() && current.back() == '\r') {
        current.pop_back();
      }
      lines.push_back(current);
      current.clear();
    } else {
      current.push_back(c);
    }
  }

  if (!current.empty()) {
    if (!current.empty() && current.back() == '\r') {
      current.pop_back();
    }
    lines.push_back(current);
  }

  return lines;
}


}  // namespace

bool ExecutionLifecycleDocument::ok() const {
  return !events.empty() && errors.empty();
}

std::size_t ExecutionLifecycleDocument::FilledEventCount() const {
  std::size_t count = 0;

  for (const ExecutionLifecycleEvent& event : events) {
    if (event.event_type == ExecutionEventType::kFill ||
        event.event_type == ExecutionEventType::kPartialFill) {
      ++count;
    }
  }

  return count;
}

bool ExecutionLifecycleDocument::HasTerminalEvent(
    const std::string& order_id) const {
  for (const ExecutionLifecycleEvent& event : events) {
    if (event.order_id != order_id) {
      continue;
    }

    if (event.event_type == ExecutionEventType::kFill ||
        event.event_type == ExecutionEventType::kCancel ||
        event.event_type == ExecutionEventType::kReject ||
        event.event_type == ExecutionEventType::kExpire) {
      return true;
    }
  }

  return false;
}

ExecutionLifecycleCsvParser::ExecutionLifecycleCsvParser() = default;

ExecutionLifecycleCsvParser::ExecutionLifecycleCsvParser(
    const ExecutionLifecycleParserOptions& options)
    : options_(options) {}

ExecutionLifecycleDocument ExecutionLifecycleCsvParser::Parse(
    const std::string& csv) const {
  ExecutionLifecycleDocument document;
  const std::vector<std::string> lines = SplitLines(csv);

  if (lines.empty()) {
    AddError(&document, 0, "document", "input is empty");
    return document;
  }

  bool header_ok = false;
  const std::vector<std::string> header = SplitCsvRecord(lines[0], &header_ok);
  if (!header_ok) {
    AddError(&document, 1, "header", "header is not valid CSV");
    return document;
  }

  const ColumnMap columns = BuildColumnMap(header, &document);
  if (!document.errors.empty()) {
    return document;
  }

  std::map<std::string, OrderState> states;
  std::set<std::string> seen_event_keys;

  for (std::size_t i = 1; i < lines.size(); ++i) {
    const std::size_t line_number = i + 1;
    const std::string& line = lines[i];

    if (TrimAscii(line).empty()) {
      continue;
    }

    if (document.events.size() >= options_.max_rows) {
      AddError(&document, line_number, "document", "row limit exceeded");
      break;
    }

    bool row_ok = false;
    const std::vector<std::string> fields = SplitCsvRecord(line, &row_ok);
    if (!row_ok) {
      AddError(&document, line_number, "row", "row is not valid CSV");
      continue;
    }

    ExecutionLifecycleEvent event;
    const std::size_t errors_before = document.errors.size();

    if (!ParseEventRow(fields, columns, line_number, &event, &document)) {
      continue;
    }

    ValidateEvent(event, &document);

    const std::string event_key =
        event.order_id + "|" + event.event_time + "|" +
        EventTypeName(event.event_type);

    if (seen_event_keys.find(event_key) != seen_event_keys.end()) {
      AddError(&document, line_number, "event", "duplicate lifecycle event");
    } else {
      seen_event_keys.insert(event_key);
    }

    if (options_.enforce_state_transitions) {
      const OrderState previous = states[event.order_id];

      if (!ValidateTransition(previous, event.event_type)) {
        AddError(&document,
                 line_number,
                 "event_type",
                 "invalid order lifecycle transition");
      } else {
        states[event.order_id] = AdvanceState(previous, event.event_type);
      }
    }

    if (document.errors.size() == errors_before) {
      document.events.push_back(event);
    }
  }

  if (document.events.empty() && document.errors.empty()) {
    AddError(&document, 0, "document", "no execution events parsed");
  }

  return document;
}

std::vector<std::string> ExecutionLifecycleCsvParser::SplitCsvRecord(
    const std::string& line,
    bool* ok) const {
  std::vector<std::string> fields;
  std::string current;
  bool quoted = false;

  if (ok != nullptr) {
    *ok = false;
  }

  for (std::size_t i = 0; i < line.size(); ++i) {
    const char c = line[i];

    if (c == '"') {
      if (quoted && i + 1 < line.size() && line[i + 1] == '"') {
        current.push_back('"');
        ++i;
      } else {
        quoted = !quoted;
      }
      continue;
    }

    if (c == ',' && !quoted) {
      if (current.size() > options_.max_field_bytes) {
        return {};
      }
      fields.push_back(current);
      current.clear();
      continue;
    }

    current.push_back(c);

    if (current.size() > options_.max_field_bytes) {
      return {};
    }
  }

  if (quoted) {
    return {};
  }

  fields.push_back(current);

  if (ok != nullptr) {
    *ok = true;
  }

  return fields;
}

std::string ExecutionLifecycleCsvParser::NormalizeToken(
    const std::string& value) const {
  return UpperAscii(TrimAscii(value));
}

std::string ExecutionLifecycleCsvParser::EventTypeName(
    ExecutionEventType type) const {
  switch (type) {
    case ExecutionEventType::kNew:
      return "NEW";
    case ExecutionEventType::kAck:
      return "ACK";
    case ExecutionEventType::kRoute:
      return "ROUTE";
    case ExecutionEventType::kReplace:
      return "REPLACE";
    case ExecutionEventType::kPartialFill:
      return "PARTIAL_FILL";
    case ExecutionEventType::kFill:
      return "FILL";
    case ExecutionEventType::kCancel:
      return "CANCEL";
    case ExecutionEventType::kReject:
      return "REJECT";
    case ExecutionEventType::kExpire:
      return "EXPIRE";
    case ExecutionEventType::kUnknown:
      return "UNKNOWN";
  }

  return "UNKNOWN";
}

bool ExecutionLifecycleCsvParser::ColumnMap::Has(
    const std::string& name) const {
  return index.find(name) != index.end();
}

std::size_t ExecutionLifecycleCsvParser::ColumnMap::At(
    const std::string& name) const {
  const auto it = index.find(name);
  if (it == index.end()) {
    return std::numeric_limits<std::size_t>::max();
  }

  return it->second;
}

ExecutionLifecycleCsvParser::ColumnMap
ExecutionLifecycleCsvParser::BuildColumnMap(
    const std::vector<std::string>& header,
    ExecutionLifecycleDocument* document) const {
  ColumnMap columns;

  for (std::size_t i = 0; i < header.size(); ++i) {
    const std::string name = NormalizeToken(header[i]);
    if (name.empty()) {
      AddError(document, 1, "header", "empty column name");
      continue;
    }

    if (columns.index.find(name) != columns.index.end()) {
      AddError(document, 1, name, "duplicate column name");
      continue;
    }

    columns.index[name] = i;
  }

  const char* required[] = {
      "ORDER_ID",
      "CLIENT_ORDER_ID",
      "SYMBOL",
      "SIDE",
      "ORDER_TYPE",
      "TIME_IN_FORCE",
      "EVENT_TYPE",
      "EVENT_TIME",
      "VENUE",
      "ROUTE",
      "QUANTITY",
      "PRICE",
      "FILLED_QUANTITY",
      "REMAINING_QUANTITY",
      "AVERAGE_PRICE",
      "COMMISSION",
      "LIQUIDITY_FLAG",
      "REJECT_CODE",
      "NOTES",
  };

  for (const char* name : required) {
    if (!columns.Has(name)) {
      AddError(document, 1, name, "required column missing");
    }
  }

  return columns;
}

bool ExecutionLifecycleCsvParser::ParseEventRow(
    const std::vector<std::string>& fields,
    const ColumnMap& columns,
    std::size_t line_number,
    ExecutionLifecycleEvent* event,
    ExecutionLifecycleDocument* document) const {
  if (event == nullptr) {
    return false;
  }

  if (fields.size() < columns.index.size()) {
    AddError(document, line_number, "row", "row has too few columns");
    return false;
  }

  event->line = line_number;
  event->order_id = TrimAscii(Field(fields, columns, "ORDER_ID"));
  event->parent_order_id = TrimAscii(Field(fields, columns, "PARENT_ORDER_ID"));
  event->client_order_id = TrimAscii(Field(fields, columns, "CLIENT_ORDER_ID"));
  event->symbol = NormalizeToken(Field(fields, columns, "SYMBOL"));
  event->venue = NormalizeToken(Field(fields, columns, "VENUE"));
  event->route = NormalizeToken(Field(fields, columns, "ROUTE"));
  event->event_time = TrimAscii(Field(fields, columns, "EVENT_TIME"));
  event->reject_code = TrimAscii(Field(fields, columns, "REJECT_CODE"));
  event->notes = Field(fields, columns, "NOTES");

  event->side = ParseSide(Field(fields, columns, "SIDE"));
  event->order_type = ParseOrderType(Field(fields, columns, "ORDER_TYPE"));
  event->time_in_force = ParseTimeInForce(Field(fields, columns, "TIME_IN_FORCE"));
  event->event_type = ParseEventType(Field(fields, columns, "EVENT_TYPE"));
  event->liquidity_flag =
      ParseLiquidityFlag(Field(fields, columns, "LIQUIDITY_FLAG"));

  if (!ParseUnsigned(Field(fields, columns, "QUANTITY"), &event->quantity)) {
    AddError(document, line_number, "quantity", "quantity is invalid");
  }

  if (!ParseUnsigned(Field(fields, columns, "FILLED_QUANTITY"),
                     &event->filled_quantity)) {
    AddError(document, line_number, "filled_quantity", "filled quantity invalid");
  }

  if (!ParseUnsigned(Field(fields, columns, "REMAINING_QUANTITY"),
                     &event->remaining_quantity)) {
    AddError(document,
             line_number,
             "remaining_quantity",
             "remaining quantity invalid");
  }

  const std::string price = TrimAscii(Field(fields, columns, "PRICE"));
  if (!price.empty()) {
    event->has_price = ParseDecimal(price, &event->price);
    if (!event->has_price) {
      AddError(document, line_number, "price", "price is invalid");
    }
  }

  const std::string average_price =
      TrimAscii(Field(fields, columns, "AVERAGE_PRICE"));
  if (!average_price.empty()) {
    event->has_average_price =
        ParseDecimal(average_price, &event->average_price);
    if (!event->has_average_price) {
      AddError(document, line_number, "average_price", "average price invalid");
    }
  }

  const std::string commission = TrimAscii(Field(fields, columns, "COMMISSION"));
  if (!commission.empty()) {
    event->has_commission = ParseDecimal(commission, &event->commission);
    if (!event->has_commission) {
      AddError(document, line_number, "commission", "commission invalid");
    }
  }

  return true;
}

bool ExecutionLifecycleCsvParser::ValidateEvent(
    const ExecutionLifecycleEvent& event,
    ExecutionLifecycleDocument* document) const {
  bool ok = true;

  auto fail = [&](const std::string& field, const std::string& message) {
    AddError(document, event.line, field, message);
    ok = false;
  };

  if (event.order_id.empty()) {
    fail("order_id", "order id is required");
  }

  if (event.client_order_id.empty()) {
    fail("client_order_id", "client order id is required");
  }

  if (event.symbol.empty()) {
    fail("symbol", "symbol is required");
  }

  if (event.side == ExecutionSide::kUnknown) {
    fail("side", "side is unknown");
  }

  if (event.order_type == ExecutionOrderType::kUnknown) {
    fail("order_type", "order type is unknown");
  }

  if (event.time_in_force == ExecutionTimeInForce::kUnknown) {
    fail("time_in_force", "time in force is unknown");
  }

  if (event.event_type == ExecutionEventType::kUnknown) {
    fail("event_type", "event type is unknown");
  }

  if (!LooksLikeTimestamp(event.event_time)) {
    fail("event_time", "event time is not ISO-like");
  }

  if (event.quantity == 0) {
    fail("quantity", "quantity must be positive");
  }

  if (event.filled_quantity > event.quantity) {
    fail("filled_quantity", "filled quantity exceeds order quantity");
  }

  if (event.remaining_quantity > event.quantity) {
    fail("remaining_quantity", "remaining quantity exceeds order quantity");
  }

  if (event.filled_quantity <= event.quantity &&
      event.remaining_quantity > event.quantity - event.filled_quantity) {
    fail("quantity", "filled plus remaining exceeds quantity");
  }

  if ((event.order_type == ExecutionOrderType::kLimit ||
       event.order_type == ExecutionOrderType::kStopLimit) &&
      !event.has_price) {
    fail("price", "limit orders require a price");
  }

  if ((event.event_type == ExecutionEventType::kFill ||
       event.event_type == ExecutionEventType::kPartialFill) &&
      event.filled_quantity == 0) {
    fail("filled_quantity", "fill events require positive filled quantity");
  }

  if (event.filled_quantity > 0 && !event.has_average_price) {
    fail("average_price", "filled orders require average price");
  }

  if (event.event_type == ExecutionEventType::kReject &&
      event.reject_code.empty()) {
    fail("reject_code", "reject events require reject code");
  }

  return ok;
}

bool ExecutionLifecycleCsvParser::ValidateTransition(
    OrderState previous,
    ExecutionEventType next) const {

  switch (previous) {
    case OrderState::kNone:
      return next == ExecutionEventType::kNew ||
             next == ExecutionEventType::kRoute ||
             next == ExecutionEventType::kAck ||
             next == ExecutionEventType::kReject;
    case OrderState::kPending:
      return next == ExecutionEventType::kAck ||
             next == ExecutionEventType::kRoute ||
             next == ExecutionEventType::kReject ||
             next == ExecutionEventType::kCancel;
    case OrderState::kLive:
      return next == ExecutionEventType::kReplace ||
             next == ExecutionEventType::kPartialFill ||
             next == ExecutionEventType::kFill ||
             next == ExecutionEventType::kCancel ||
             next == ExecutionEventType::kReject ||
             next == ExecutionEventType::kExpire;
    case OrderState::kPartiallyFilled:
      return next == ExecutionEventType::kPartialFill ||
             next == ExecutionEventType::kFill ||
             next == ExecutionEventType::kCancel ||
             next == ExecutionEventType::kExpire;
    case OrderState::kFilled:
    case OrderState::kCancelled:
    case OrderState::kRejected:
    case OrderState::kExpired:
      return false;
  }

  return false;
}

ExecutionLifecycleCsvParser::OrderState
ExecutionLifecycleCsvParser::AdvanceState(
    OrderState previous,
    ExecutionEventType next) const {
  switch (next) {
    case ExecutionEventType::kNew:
      return OrderState::kPending;
    case ExecutionEventType::kAck:
    case ExecutionEventType::kRoute:
    case ExecutionEventType::kReplace:
      return OrderState::kLive;
    case ExecutionEventType::kPartialFill:
      return OrderState::kPartiallyFilled;
    case ExecutionEventType::kFill:
      return OrderState::kFilled;
    case ExecutionEventType::kCancel:
      return OrderState::kCancelled;
    case ExecutionEventType::kReject:
      return OrderState::kRejected;
    case ExecutionEventType::kExpire:
      return OrderState::kExpired;
    case ExecutionEventType::kUnknown:
      return previous;
  }

  return previous;
}

void ExecutionLifecycleCsvParser::AddError(
    ExecutionLifecycleDocument* document,
    std::size_t line,
    const std::string& field,
    const std::string& message) const {
  if (document == nullptr) {
    return;
  }

  ExecutionLifecycleError error;
  error.line = line;
  error.field = field;
  error.message = message;
  document->errors.push_back(error);
}

std::string ExecutionLifecycleCsvParser::Field(
    const std::vector<std::string>& fields,
    const ColumnMap& columns,
    const std::string& name) const {
  if (!columns.Has(name)) {
    return "";
  }

  const std::size_t index = columns.At(name);
  if (index >= fields.size()) {
    return "";
  }

  return fields[index];
}

bool ExecutionLifecycleCsvParser::ParseUnsigned(
    const std::string& value,
    std::uint64_t* out) const {
  if (out == nullptr) {
    return false;
  }

  const std::string trimmed = TrimAscii(value);
  if (trimmed.empty()) {
    return false;
  }

  std::uint64_t parsed = 0;
  const char* begin = trimmed.data();
  const char* end = trimmed.data() + trimmed.size();
  const auto result = std::from_chars(begin, end, parsed);

  if (result.ec != std::errc() || result.ptr != end) {
    return false;
  }

  *out = parsed;
  return true;
}

bool ExecutionLifecycleCsvParser::ParseDecimal(
    const std::string& value,
    double* out) const {
  if (out == nullptr) {
    return false;
  }

  const std::string trimmed = TrimAscii(value);
  if (trimmed.empty()) {
    return false;
  }

  std::size_t pos = 0;
  bool negative = false;

  if (trimmed[pos] == '+' || trimmed[pos] == '-') {
    negative = trimmed[pos] == '-';
    ++pos;
  }

  bool saw_digit = false;
  bool saw_dot = false;
  long double parsed = 0.0L;
  long double scale = 0.1L;

  for (; pos < trimmed.size(); ++pos) {
    const char c = trimmed[pos];

    if (c == '.') {
      if (saw_dot) {
        return false;
      }
      saw_dot = true;
      continue;
    }

    if (!IsDigit(c)) {
      return false;
    }

    saw_digit = true;
    const int digit = c - '0';

    if (saw_dot) {
      parsed += static_cast<long double>(digit) * scale;
      scale /= 10.0L;
    } else {
      parsed = parsed * 10.0L + static_cast<long double>(digit);
    }
  }

  if (!saw_digit) {
    return false;
  }

  *out = static_cast<double>(negative ? -parsed : parsed);
  return true;
}

bool ExecutionLifecycleCsvParser::LooksLikeTimestamp(
    const std::string& value) const {
  const std::string t = TrimAscii(value);

  if (t.size() < 10) {
    return false;
  }

  return IsDigit(t[0]) && IsDigit(t[1]) && IsDigit(t[2]) && IsDigit(t[3]) &&
         t[4] == '-' &&
         IsDigit(t[5]) && IsDigit(t[6]) &&
         t[7] == '-' &&
         IsDigit(t[8]) && IsDigit(t[9]);
}

ExecutionSide ExecutionLifecycleCsvParser::ParseSide(
    const std::string& value) const {
  const std::string token = NormalizeToken(value);

  if (token == "BUY") return ExecutionSide::kBuy;
  if (token == "SELL") return ExecutionSide::kSell;
  if (token == "SHORT") return ExecutionSide::kShort;
  if (token == "COVER") return ExecutionSide::kCover;
  return ExecutionSide::kUnknown;
}

ExecutionOrderType ExecutionLifecycleCsvParser::ParseOrderType(
    const std::string& value) const {
  const std::string token = NormalizeToken(value);

  if (token == "MARKET") return ExecutionOrderType::kMarket;
  if (token == "LIMIT") return ExecutionOrderType::kLimit;
  if (token == "STOP") return ExecutionOrderType::kStop;
  if (token == "STOP_LIMIT" || token == "STOP LIMIT") {
    return ExecutionOrderType::kStopLimit;
  }
  if (token == "PEGGED") return ExecutionOrderType::kPegged;
  return ExecutionOrderType::kUnknown;
}

ExecutionTimeInForce ExecutionLifecycleCsvParser::ParseTimeInForce(
    const std::string& value) const {
  const std::string token = NormalizeToken(value);

  if (token == "DAY") return ExecutionTimeInForce::kDay;
  if (token == "GTC") return ExecutionTimeInForce::kGtc;
  if (token == "IOC") return ExecutionTimeInForce::kIoc;
  if (token == "FOK") return ExecutionTimeInForce::kFok;
  if (token == "AT_OPEN" || token == "OPG") return ExecutionTimeInForce::kAtOpen;
  if (token == "AT_CLOSE" || token == "CLS") {
    return ExecutionTimeInForce::kAtClose;
  }
  return ExecutionTimeInForce::kUnknown;
}

ExecutionEventType ExecutionLifecycleCsvParser::ParseEventType(
    const std::string& value) const {
  const std::string token = NormalizeToken(value);

  if (token == "NEW") return ExecutionEventType::kNew;
  if (token == "ACK") return ExecutionEventType::kAck;
  if (token == "ROUTE") return ExecutionEventType::kRoute;
  if (token == "REPLACE") return ExecutionEventType::kReplace;
  if (token == "PARTIAL_FILL" || token == "PARTIAL FILL") {
    return ExecutionEventType::kPartialFill;
  }
  if (token == "FILL") return ExecutionEventType::kFill;
  if (token == "CANCEL" || token == "CANCELLED") {
    return ExecutionEventType::kCancel;
  }
  if (token == "REJECT" || token == "REJECTED") {
    return ExecutionEventType::kReject;
  }
  if (token == "EXPIRE" || token == "EXPIRED") {
    return ExecutionEventType::kExpire;
  }
  return ExecutionEventType::kUnknown;
}

ExecutionLiquidityFlag ExecutionLifecycleCsvParser::ParseLiquidityFlag(
    const std::string& value) const {
  const std::string token = NormalizeToken(value);

  if (token.empty()) return ExecutionLiquidityFlag::kUnknown;
  if (token == "A" || token == "ADD") return ExecutionLiquidityFlag::kAdd;
  if (token == "R" || token == "REMOVE") {
    return ExecutionLiquidityFlag::kRemove;
  }
  if (token == "AUCTION" || token == "O" || token == "C") {
    return ExecutionLiquidityFlag::kAuction;
  }
  return ExecutionLiquidityFlag::kUnknown;
}

bool ParseExecutionLifecycleCsv(
    const std::string& csv,
    ExecutionLifecycleDocument* document) {
  if (document == nullptr) {
    return false;
  }

  ExecutionLifecycleCsvParser parser;
  *document = parser.Parse(csv);
  return document->ok();
}

}  // namespace marketdata


