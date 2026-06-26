#ifndef TRADING_ASSISTANT_NATIVE_MARKET_DATA_ROUTER_H_
#define TRADING_ASSISTANT_NATIVE_MARKET_DATA_ROUTER_H_

#include <cstdint>
#include <string>
#include <string_view>
#include <vector>

namespace trading_assistant::native_marketdata {

struct MarketDataRoute {
  std::string route_name;
  std::string venue;
  std::string symbol_prefix;
  std::string channel;
  std::uint64_t max_lag_ms = 0;
};

std::vector<MarketDataRoute> ParseMarketDataRoutes(std::string_view input);
bool ValidateMarketDataRoutes(std::string_view input);

}  // namespace trading_assistant::native_marketdata

#endif
