#include "native/marketdata/numeric_parser.h"

#include "native/marketdata/string_utils.h"

#include <cerrno>
#include <charconv>
#include <cmath>
#include <cstdlib>
#include <limits>
#include <string>

namespace trading_assistant::native_marketdata {

std::optional<std::uint64_t> ParseUnsigned64(std::string_view value) {
  const std::string trimmed = TrimAscii(value);
  if (trimmed.empty()) {
    return std::nullopt;
  }

  std::uint64_t out = 0;
  const char* begin = trimmed.data();
  const char* end = trimmed.data() + trimmed.size();
  const auto result = std::from_chars(begin, end, out);

  if (result.ec != std::errc{} || result.ptr != end) {
    return std::nullopt;
  }

  return out;
}

std::optional<std::int64_t> ParseSigned64(std::string_view value) {
  const std::string trimmed = TrimAscii(value);
  if (trimmed.empty()) {
    return std::nullopt;
  }

  std::int64_t out = 0;
  const char* begin = trimmed.data();
  const char* end = trimmed.data() + trimmed.size();
  const auto result = std::from_chars(begin, end, out);

  if (result.ec != std::errc{} || result.ptr != end) {
    return std::nullopt;
  }

  return out;
}

std::optional<double> ParseFiniteDouble(std::string_view value) {
  const std::string trimmed = TrimAscii(value);
  if (trimmed.empty()) {
    return std::nullopt;
  }

  char* end = nullptr;
  errno = 0;
  const double parsed = std::strtod(trimmed.c_str(), &end);

  if (errno == ERANGE || end == trimmed.c_str() || *end != '\0' || !std::isfinite(parsed)) {
    return std::nullopt;
  }

  return parsed;
}

bool IsValidPrice(double value) {
  return std::isfinite(value) && value >= 0.0 && value <= 1000000000.0;
}

bool IsValidQuantity(std::uint64_t value) {
  return value > 0 && value <= 1000000000000ull;
}

bool IsReasonableBasisPoints(std::int64_t value) {
  return value >= -100000 && value <= 100000;
}

std::uint64_t ClampUnsigned(std::uint64_t value, std::uint64_t low, std::uint64_t high) {
  if (value < low) return low;
  if (value > high) return high;
  return value;
}

double ClampDouble(double value, double low, double high) {
  if (!std::isfinite(value)) return low;
  if (value < low) return low;
  if (value > high) return high;
  return value;
}

}  // namespace trading_assistant::native_marketdata
