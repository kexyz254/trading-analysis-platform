#ifndef TRADING_ASSISTANT_NATIVE_MARKET_DATA_PARSER_H_
#define TRADING_ASSISTANT_NATIVE_MARKET_DATA_PARSER_H_

#include <cstdint>
#include <stdexcept>
#include <string>
#include <string_view>
#include <vector>

namespace trading_assistant::native_marketdata {

struct ParserOptions {
  bool allow_header = true;
  bool require_monotonic_timestamps = false;
  std::size_t max_rows = 4096;
};

struct OhlcvBar {
  std::string timestamp;
  double open = 0.0;
  double high = 0.0;
  double low = 0.0;
  double close = 0.0;
  std::uint64_t volume = 0;
};

enum class OrderSide {
  kBuy,
  kSell,
};

enum class OrderKind {
  kMarket,
  kLimit,
  kStop,
};

struct OrderMessage {
  std::string symbol;
  OrderSide side = OrderSide::kBuy;
  OrderKind kind = OrderKind::kMarket;
  double price = 0.0;
  std::uint64_t quantity = 0;
  std::uint64_t sequence = 0;
};

class ParseError : public std::runtime_error {
 public:
  explicit ParseError(const std::string& message) : std::runtime_error(message) {}
};

std::vector<OhlcvBar> ParseOhlcvCsv(
    std::string_view input,
    ParserOptions options = ParserOptions{});

bool ValidateOhlcvCsv(
    std::string_view input,
    ParserOptions options = ParserOptions{});

OrderMessage ParseOrderMessage(std::string_view input);

std::vector<OrderMessage> ParseOrderStream(
    std::string_view input,
    std::size_t max_messages = 4096);

bool ValidateOrderStream(
    std::string_view input,
    std::size_t max_messages = 4096);

}  // namespace trading_assistant::native_marketdata

#endif  // TRADING_ASSISTANT_NATIVE_MARKET_DATA_PARSER_H_
