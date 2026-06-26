#include "native/marketdata/volatility_surface.h"

#include "native/marketdata/numeric_parser.h"
#include "native/marketdata/string_utils.h"

#include <cctype>
#include <map>
#include <set>
#include <tuple>

namespace trading_assistant::native_marketdata {
namespace {

bool IsDateLike(std::string_view value) {
  if (value.size() != 10) return false;

  for (std::size_t i = 0; i < value.size(); ++i) {
    if (i == 4 || i == 7) {
      if (value[i] != '-') return false;
    } else if (!std::isdigit(static_cast<unsigned char>(value[i]))) {
      return false;
    }
  }

  return true;
}

bool IsReasonableVolatility(double value) {
  return value >= 0.0001 && value <= 10.0;
}

}  // namespace

std::vector<VolatilityPoint> ParseVolatilitySurface(std::string_view input) {
  std::vector<VolatilityPoint> points;

  if (input.empty() || input.size() > 524288) {
    return {};
  }

  std::set<std::tuple<std::string, std::string, double>> seen;
  std::map<std::tuple<std::string, std::string>, double> last_strike;
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

    const auto fields = SplitLine(line, ',', 8);
    if (fields.size() != 5) {
      return {};
    }

    const std::string symbol = ToUpperAscii(fields[0]);
    const std::string expiry = fields[1];

    if (!IsAsciiSymbol(symbol, 24) || !IsDateLike(expiry)) {
      return {};
    }

    const auto strike = ParseFiniteDouble(fields[2]);
    const auto call_iv = ParseFiniteDouble(fields[3]);
    const auto put_iv = ParseFiniteDouble(fields[4]);

    if (!strike.has_value() || !call_iv.has_value() || !put_iv.has_value()) {
      return {};
    }

    if (*strike <= 0.0 || *strike > 10000000.0) {
      return {};
    }

    if (!IsReasonableVolatility(*call_iv) || !IsReasonableVolatility(*put_iv)) {
      return {};
    }

    const auto curve_key = std::make_tuple(symbol, expiry);
    const auto point_key = std::make_tuple(symbol, expiry, *strike);

    if (seen.count(point_key) != 0) {
      return {};
    }

    if (last_strike.count(curve_key) != 0 && *strike <= last_strike[curve_key]) {
      return {};
    }

    seen.insert(point_key);
    last_strike[curve_key] = *strike;

    points.push_back(VolatilityPoint{symbol, expiry, *strike, *call_iv, *put_iv});

    if (points.size() > 16384) {
      return {};
    }

    if (next == input.size()) break;
  }

  return points;
}

bool ValidateVolatilitySurface(std::string_view input) {
  return !ParseVolatilitySurface(input).empty();
}

}  // namespace trading_assistant::native_marketdata
