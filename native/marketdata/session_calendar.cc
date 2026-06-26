#include "native/marketdata/session_calendar.h"

#include "native/marketdata/numeric_parser.h"
#include "native/marketdata/string_utils.h"
#include <cctype>

namespace trading_assistant::native_marketdata {
namespace {

bool IsDateLike(std::string_view value) {
  return value.size() == 10 &&
         std::isdigit(static_cast<unsigned char>(value[0])) &&
         std::isdigit(static_cast<unsigned char>(value[1])) &&
         std::isdigit(static_cast<unsigned char>(value[2])) &&
         std::isdigit(static_cast<unsigned char>(value[3])) &&
         value[4] == '-' &&
         std::isdigit(static_cast<unsigned char>(value[5])) &&
         std::isdigit(static_cast<unsigned char>(value[6])) &&
         value[7] == '-' &&
         std::isdigit(static_cast<unsigned char>(value[8])) &&
         std::isdigit(static_cast<unsigned char>(value[9]));
}

}  // namespace

std::vector<TradingSession> ParseTradingSessions(std::string_view input) {
  std::vector<TradingSession> sessions;

  if (input.size() > 65536) {
    return {};
  }

  std::size_t offset = 0;

  while (offset <= input.size()) {
    std::size_t next = input.find('\n', offset);
    if (next == std::string_view::npos) next = input.size();

    const std::string_view line = input.substr(offset, next - offset);
    offset = next + 1;

    if (TrimAscii(line).empty()) {
      if (next == input.size()) break;
      continue;
    }

    const auto fields = SplitLine(line, '|', 6);
    if (fields.size() != 4) {
      return {};
    }

    if (!IsAsciiSymbol(fields[0], 16) || !IsDateLike(fields[1])) {
      return {};
    }

    const auto open_minute = ParseUnsigned64(fields[2]);
    const auto close_minute = ParseUnsigned64(fields[3]);

    if (!open_minute.has_value() || !close_minute.has_value()) {
      return {};
    }

    if (*open_minute >= 1440 || *close_minute > 1440 || *open_minute >= *close_minute) {
      return {};
    }

    sessions.push_back(TradingSession{
        fields[0],
        fields[1],
        static_cast<std::uint32_t>(*open_minute),
        static_cast<std::uint32_t>(*close_minute),
    });

    if (sessions.size() > 4096) {
      return {};
    }

    if (next == input.size()) break;
  }

  return sessions;
}

bool ValidateTradingSessions(std::string_view input) {
  return !ParseTradingSessions(input).empty();
}

}  // namespace trading_assistant::native_marketdata
