#ifndef TRADING_ASSISTANT_NATIVE_ANALYTICS_WINDOW_H_
#define TRADING_ASSISTANT_NATIVE_ANALYTICS_WINDOW_H_

#include <cstdint>
#include <string_view>
#include <vector>

namespace trading_assistant::native_marketdata {

struct PricePoint {
  double price = 0.0;
  std::uint64_t volume = 0;
};

struct WindowStats {
  double min_price = 0.0;
  double max_price = 0.0;
  double mean_price = 0.0;
  double volume_weighted_price = 0.0;
  std::uint64_t total_volume = 0;
};

std::vector<PricePoint> ParsePriceWindow(std::string_view input);
WindowStats ComputeWindowStats(const std::vector<PricePoint>& points);
bool ValidatePriceWindow(std::string_view input);

}  // namespace trading_assistant::native_marketdata

#endif
