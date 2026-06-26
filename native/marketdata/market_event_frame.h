#ifndef TRADING_ASSISTANT_NATIVE_MARKET_EVENT_FRAME_H_
#define TRADING_ASSISTANT_NATIVE_MARKET_EVENT_FRAME_H_

#include <cstdint>
#include <string>
#include <string_view>
#include <vector>

namespace trading_assistant::native_marketdata {

struct MarketEventFrame {
  std::uint64_t sequence = 0;
  std::string event_type;
  std::uint32_t flags = 0;
  std::string payload;
};

std::vector<MarketEventFrame> ParseMarketEventFrames(std::string_view input);
bool ValidateMarketEventFrames(std::string_view input);

}  // namespace trading_assistant::native_marketdata

#endif
