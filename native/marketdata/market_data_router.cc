#include "native/marketdata/market_data_router.h"

#include "native/marketdata/numeric_parser.h"
#include "native/marketdata/string_utils.h"

#include <set>
#include <tuple>

namespace trading_assistant::native_marketdata {
namespace {

bool IsKnownChannel(std::string_view value) {
  const std::string channel = ToUpperAscii(value);
  return channel == "TRADES" ||
         channel == "QUOTES" ||
         channel == "BOOK" ||
         channel == "OHLCV" ||
         channel == "REFERENCE" ||
         channel == "RISK" ||
         channel == "FEATURES";
}

}  // namespace

std::vector<MarketDataRoute> ParseMarketDataRoutes(std::string_view input) {
  std::vector<MarketDataRoute> routes;

  if (input.empty() || input.size() > 131072) {
    return {};
  }

  std::set<std::string> route_names;
  std::set<std::tuple<std::string, std::string, std::string>> route_keys;
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

    const auto fields = SplitLine(line, '|', 8);
    if (fields.size() != 5) {
      return {};
    }

    const std::string route_name = ToUpperAscii(fields[0]);
    const std::string venue = ToUpperAscii(fields[1]);
    const std::string prefix = ToUpperAscii(fields[2]);
    const std::string channel = ToUpperAscii(fields[3]);

    if (!IsAsciiSymbol(route_name, 32) ||
        !IsAsciiSymbol(venue, 16) ||
        !IsAsciiSymbol(prefix, 24) ||
        !IsKnownChannel(channel)) {
      return {};
    }

    const auto max_lag = ParseUnsigned64(fields[4]);
    if (!max_lag.has_value() || *max_lag > 3600000) {
      return {};
    }

    const auto key = std::make_tuple(venue, prefix, channel);
    if (route_names.count(route_name) != 0 || route_keys.count(key) != 0) {
      return {};
    }

    route_names.insert(route_name);
    route_keys.insert(key);

    routes.push_back(MarketDataRoute{route_name, venue, prefix, channel, *max_lag});

    if (routes.size() > 4096) {
      return {};
    }

    if (next == input.size()) break;
  }

  return routes;
}

bool ValidateMarketDataRoutes(std::string_view input) {
  return !ParseMarketDataRoutes(input).empty();
}

}  // namespace trading_assistant::native_marketdata
