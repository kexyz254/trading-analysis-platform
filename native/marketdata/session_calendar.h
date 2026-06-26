#ifndef TRADING_ASSISTANT_NATIVE_SESSION_CALENDAR_H_
#define TRADING_ASSISTANT_NATIVE_SESSION_CALENDAR_H_

#include <cstdint>
#include <string>
#include <string_view>
#include <vector>

namespace trading_assistant::native_marketdata {

struct TradingSession {
  std::string venue;
  std::string date;
  std::uint32_t open_minute = 0;
  std::uint32_t close_minute = 0;
};

std::vector<TradingSession> ParseTradingSessions(std::string_view input);
bool ValidateTradingSessions(std::string_view input);

}  // namespace trading_assistant::native_marketdata

#endif
