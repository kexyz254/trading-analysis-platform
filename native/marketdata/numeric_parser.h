#ifndef TRADING_ASSISTANT_NATIVE_NUMERIC_PARSER_H_
#define TRADING_ASSISTANT_NATIVE_NUMERIC_PARSER_H_

#include <cstdint>
#include <optional>
#include <string_view>

namespace trading_assistant::native_marketdata {

std::optional<std::uint64_t> ParseUnsigned64(std::string_view value);
std::optional<std::int64_t> ParseSigned64(std::string_view value);
std::optional<double> ParseFiniteDouble(std::string_view value);
bool IsValidPrice(double value);
bool IsValidQuantity(std::uint64_t value);
bool IsReasonableBasisPoints(std::int64_t value);
std::uint64_t ClampUnsigned(std::uint64_t value, std::uint64_t low, std::uint64_t high);
double ClampDouble(double value, double low, double high);

}  // namespace trading_assistant::native_marketdata

#endif
