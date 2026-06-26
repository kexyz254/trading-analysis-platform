#include "native/marketdata/market_data_parser.h"

#include <algorithm>
#include <cerrno>
#include <charconv>
#include <cctype>
#include <cmath>
#include <cstdlib>
#include <string>
#include <utility>

namespace trading_assistant::native_marketdata {
namespace {

constexpr std::size_t kMaxInputLength = 1024 * 1024;
constexpr std::size_t kMaxFieldLength = 512;
constexpr std::size_t kMaxSymbolLength = 24;

struct Status {
  bool ok = true;
  std::string message;

  static Status Ok() {
    return Status{};
  }

  static Status Error(std::string text) {
    return Status{false, std::move(text)};
  }
};

std::string Trim(std::string_view value) {
  std::size_t begin = 0;

  while (begin < value.size() &&
         (value[begin] == ' ' || value[begin] == '\t' || value[begin] == '\r')) {
    ++begin;
  }

  std::size_t end = value.size();

  while (end > begin &&
         (value[end - 1] == ' ' ||
          value[end - 1] == '\t' ||
          value[end - 1] == '\r')) {
    --end;
  }

  return std::string(value.substr(begin, end - begin));
}

std::string UpperAscii(std::string value) {
  std::transform(value.begin(), value.end(), value.begin(),
                 [](unsigned char c) {
                   return static_cast<char>(std::toupper(c));
                 });

  return value;
}

bool LooksLikeHeader(const std::vector<std::string>& fields) {
  if (fields.empty()) {
    return false;
  }

  const std::string first = UpperAscii(fields[0]);
  return first == "TIMESTAMP" || first == "DATE" || first == "TIME";
}

Status SplitDelimitedLine(std::string_view line,
                          char delimiter,
                          std::vector<std::string>* fields) {
  fields->clear();

  std::string current;
  current.reserve(std::min<std::size_t>(line.size(), kMaxFieldLength));

  bool quoted = false;

  for (char c : line) {
    if (c == '"') {
      quoted = !quoted;
      continue;
    }

    if (!quoted && c == delimiter) {
      fields->push_back(Trim(current));
      current.clear();
      continue;
    }

    if (current.size() >= kMaxFieldLength) {
      return Status::Error("field exceeds maximum supported length");
    }

    current.push_back(c);
  }

  if (quoted) {
    return Status::Error("unterminated quoted field");
  }

  fields->push_back(Trim(current));
  return Status::Ok();
}

bool IsValidSymbol(std::string_view symbol) {
  if (symbol.empty() || symbol.size() > kMaxSymbolLength) {
    return false;
  }

  for (unsigned char c : symbol) {
    if (!(std::isalnum(c) || c == '.' || c == '-' || c == '_')) {
      return false;
    }
  }

  return true;
}

Status ParseDoubleStrict(std::string_view value,
                         const char* field_name,
                         double* out) {
  const std::string trimmed = Trim(value);

  if (trimmed.empty()) {
    return Status::Error(std::string(field_name) + " is empty");
  }

  char* end = nullptr;
  errno = 0;

  const double parsed = std::strtod(trimmed.c_str(), &end);

  if (errno == ERANGE ||
      end == trimmed.c_str() ||
      *end != '\0' ||
      !std::isfinite(parsed)) {
    return Status::Error(std::string(field_name) + " is not a finite number");
  }

  *out = parsed;
  return Status::Ok();
}

Status ParseUInt64Strict(std::string_view value,
                         const char* field_name,
                         std::uint64_t* out) {
  const std::string trimmed = Trim(value);

  if (trimmed.empty()) {
    return Status::Error(std::string(field_name) + " is empty");
  }

  std::uint64_t parsed = 0;
  const char* begin = trimmed.data();
  const char* end = trimmed.data() + trimmed.size();

  const auto result = std::from_chars(begin, end, parsed);

  if (result.ec != std::errc{} || result.ptr != end) {
    return Status::Error(std::string(field_name) + " is not an unsigned integer");
  }

  *out = parsed;
  return Status::Ok();
}

Status ValidateBar(const OhlcvBar& bar) {
  if (bar.timestamp.empty()) {
    return Status::Error("timestamp is empty");
  }

  if (bar.open < 0.0 ||
      bar.high < 0.0 ||
      bar.low < 0.0 ||
      bar.close < 0.0) {
    return Status::Error("price fields must be non-negative");
  }

  if (bar.low > bar.high) {
    return Status::Error("low cannot exceed high");
  }

  if (bar.open < bar.low ||
      bar.open > bar.high ||
      bar.close < bar.low ||
      bar.close > bar.high) {
    return Status::Error("open and close must be inside the high-low range");
  }

  return Status::Ok();
}

Status ParseSide(std::string_view value, OrderSide* out) {
  const std::string side = UpperAscii(Trim(value));

  if (side == "BUY" || side == "B") {
    *out = OrderSide::kBuy;
    return Status::Ok();
  }

  if (side == "SELL" || side == "S") {
    *out = OrderSide::kSell;
    return Status::Ok();
  }

  return Status::Error("unknown order side");
}

Status ParseKind(std::string_view value, OrderKind* out) {
  const std::string kind = UpperAscii(Trim(value));

  if (kind == "MARKET" || kind == "MKT") {
    *out = OrderKind::kMarket;
    return Status::Ok();
  }

  if (kind == "LIMIT" || kind == "LMT") {
    *out = OrderKind::kLimit;
    return Status::Ok();
  }

  if (kind == "STOP" || kind == "STP") {
    *out = OrderKind::kStop;
    return Status::Ok();
  }

  return Status::Error("unknown order kind");
}

Status ParseOhlcvCsvImpl(std::string_view input,
                         ParserOptions options,
                         std::vector<OhlcvBar>* bars) {
  bars->clear();

  if (input.size() > kMaxInputLength) {
    return Status::Error("CSV input is too large");
  }

  bool first_record = true;
  std::string previous_timestamp;
  std::vector<std::string> fields;

  std::size_t offset = 0;

  while (offset <= input.size()) {
    std::size_t next = input.find('\n', offset);

    if (next == std::string_view::npos) {
      next = input.size();
    }

    const std::string_view line = input.substr(offset, next - offset);
    offset = next + 1;

    if (Trim(line).empty()) {
      if (next == input.size()) {
        break;
      }

      continue;
    }

    Status status = SplitDelimitedLine(line, ',', &fields);

    if (!status.ok) {
      return status;
    }

    if (first_record && options.allow_header && LooksLikeHeader(fields)) {
      first_record = false;

      if (next == input.size()) {
        break;
      }

      continue;
    }

    first_record = false;

    if (fields.size() != 6) {
      return Status::Error("OHLCV rows must have exactly six fields");
    }

    OhlcvBar bar;
    bar.timestamp = fields[0];

    status = ParseDoubleStrict(fields[1], "open", &bar.open);
    if (!status.ok) return status;

    status = ParseDoubleStrict(fields[2], "high", &bar.high);
    if (!status.ok) return status;

    status = ParseDoubleStrict(fields[3], "low", &bar.low);
    if (!status.ok) return status;

    status = ParseDoubleStrict(fields[4], "close", &bar.close);
    if (!status.ok) return status;

    status = ParseUInt64Strict(fields[5], "volume", &bar.volume);
    if (!status.ok) return status;

    status = ValidateBar(bar);
    if (!status.ok) return status;

    if (options.require_monotonic_timestamps &&
        !previous_timestamp.empty() &&
        bar.timestamp <= previous_timestamp) {
      return Status::Error("timestamps must be strictly increasing");
    }

    previous_timestamp = bar.timestamp;

    bars->push_back(std::move(bar));

    if (bars->size() > options.max_rows) {
      return Status::Error("too many OHLCV rows");
    }

    if (next == input.size()) {
      break;
    }
  }

  return Status::Ok();
}

Status ParseOrderMessageImpl(std::string_view input, OrderMessage* order) {
  if (input.size() > 4096) {
    return Status::Error("order message is too large");
  }

  std::vector<std::string> fields;
  Status status = SplitDelimitedLine(input, '|', &fields);

  if (!status.ok) {
    return status;
  }

  if (fields.size() != 6) {
    return Status::Error("order message must have six pipe-delimited fields");
  }

  status = ParseUInt64Strict(fields[0], "sequence", &order->sequence);
  if (!status.ok) return status;

  order->symbol = Trim(fields[1]);

  if (!IsValidSymbol(order->symbol)) {
    return Status::Error("invalid symbol");
  }

  status = ParseSide(fields[2], &order->side);
  if (!status.ok) return status;

  status = ParseKind(fields[3], &order->kind);
  if (!status.ok) return status;

  status = ParseDoubleStrict(fields[4], "price", &order->price);
  if (!status.ok) return status;

  status = ParseUInt64Strict(fields[5], "quantity", &order->quantity);
  if (!status.ok) return status;

  if (order->quantity == 0) {
    return Status::Error("quantity must be positive");
  }

  if (order->price < 0.0) {
    return Status::Error("price must be non-negative");
  }

  if (order->kind != OrderKind::kMarket && order->price == 0.0) {
    return Status::Error("limit and stop orders require a positive price");
  }

  return Status::Ok();
}

Status ParseOrderStreamImpl(std::string_view input,
                            std::size_t max_messages,
                            std::vector<OrderMessage>* orders) {
  orders->clear();

  if (input.size() > kMaxInputLength) {
    return Status::Error("order stream is too large");
  }

  std::uint64_t previous_sequence = 0;
  bool have_sequence = false;

  std::size_t offset = 0;

  while (offset <= input.size()) {
    std::size_t next = input.find('\n', offset);

    if (next == std::string_view::npos) {
      next = input.size();
    }

    const std::string_view line = input.substr(offset, next - offset);
    offset = next + 1;

    if (Trim(line).empty()) {
      if (next == input.size()) {
        break;
      }

      continue;
    }

    OrderMessage order;
    Status status = ParseOrderMessageImpl(line, &order);

    if (!status.ok) {
      return status;
    }

    if (have_sequence && order.sequence <= previous_sequence) {
      return Status::Error("order sequence must be increasing");
    }

    previous_sequence = order.sequence;
    have_sequence = true;

    orders->push_back(std::move(order));

    if (orders->size() > max_messages) {
      return Status::Error("too many order messages");
    }

    if (next == input.size()) {
      break;
    }
  }

  return Status::Ok();
}

}  // namespace

std::vector<OhlcvBar> ParseOhlcvCsv(std::string_view input,
                                    ParserOptions options) {
  std::vector<OhlcvBar> bars;
  const Status status = ParseOhlcvCsvImpl(input, options, &bars);

  if (!status.ok) {
    throw ParseError(status.message);
  }

  return bars;
}

bool ValidateOhlcvCsv(std::string_view input, ParserOptions options) {
  std::vector<OhlcvBar> bars;
  return ParseOhlcvCsvImpl(input, options, &bars).ok;
}

OrderMessage ParseOrderMessage(std::string_view input) {
  OrderMessage order;
  const Status status = ParseOrderMessageImpl(input, &order);

  if (!status.ok) {
    throw ParseError(status.message);
  }

  return order;
}

std::vector<OrderMessage> ParseOrderStream(std::string_view input,
                                           std::size_t max_messages) {
  std::vector<OrderMessage> orders;
  const Status status = ParseOrderStreamImpl(input, max_messages, &orders);

  if (!status.ok) {
    throw ParseError(status.message);
  }

  return orders;
}

bool ValidateOrderStream(std::string_view input, std::size_t max_messages) {
  std::vector<OrderMessage> orders;
  return ParseOrderStreamImpl(input, max_messages, &orders).ok;
}

}  // namespace trading_assistant::native_marketdata
