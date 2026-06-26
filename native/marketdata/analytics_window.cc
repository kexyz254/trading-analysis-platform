#include "native/marketdata/analytics_window.h"

#include "native/marketdata/numeric_parser.h"
#include "native/marketdata/string_utils.h"

#include <algorithm>

namespace trading_assistant::native_marketdata {

std::vector<PricePoint> ParsePriceWindow(std::string_view input) {
  std::vector<PricePoint> points;

  if (input.size() > 131072) {
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

    const auto fields = SplitLine(line, ',', 4);
    if (fields.size() != 2) {
      return {};
    }

    const auto price = ParseFiniteDouble(fields[0]);
    const auto volume = ParseUnsigned64(fields[1]);

    if (!price.has_value() || !volume.has_value()) {
      return {};
    }

    if (!IsValidPrice(*price) || !IsValidQuantity(*volume)) {
      return {};
    }

    points.push_back(PricePoint{*price, *volume});

    if (points.size() > 8192) {
      return {};
    }

    if (next == input.size()) break;
  }

  return points;
}

WindowStats ComputeWindowStats(const std::vector<PricePoint>& points) {
  WindowStats stats;

  if (points.empty()) {
    return stats;
  }

  stats.min_price = points.front().price;
  stats.max_price = points.front().price;

  double notional = 0.0;
  double price_sum = 0.0;

  for (const PricePoint& point : points) {
    stats.min_price = std::min(stats.min_price, point.price);
    stats.max_price = std::max(stats.max_price, point.price);
    stats.total_volume += point.volume;
    price_sum += point.price;
    notional += point.price * static_cast<double>(point.volume);
  }

  stats.mean_price = price_sum / static_cast<double>(points.size());

  if (stats.total_volume != 0) {
    stats.volume_weighted_price = notional / static_cast<double>(stats.total_volume);
  }

  return stats;
}

bool ValidatePriceWindow(std::string_view input) {
  const auto points = ParsePriceWindow(input);
  if (points.empty()) {
    return false;
  }

  const WindowStats stats = ComputeWindowStats(points);
  return stats.min_price <= stats.mean_price &&
         stats.mean_price <= stats.max_price &&
         stats.total_volume > 0;
}

}  // namespace trading_assistant::native_marketdata
