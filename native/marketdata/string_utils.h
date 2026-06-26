#ifndef TRADING_ASSISTANT_NATIVE_STRING_UTILS_H_
#define TRADING_ASSISTANT_NATIVE_STRING_UTILS_H_

#include <cstddef>
#include <cstdint>
#include <string>
#include <string_view>
#include <vector>

namespace trading_assistant::native_marketdata {

std::string TrimAscii(std::string_view value);
std::string ToUpperAscii(std::string_view value);
std::string ToLowerAscii(std::string_view value);
bool StartsWith(std::string_view value, std::string_view prefix);
bool EndsWith(std::string_view value, std::string_view suffix);
bool IsAsciiSymbol(std::string_view value, std::size_t max_length);
bool IsAsciiInteger(std::string_view value);
bool IsAsciiDecimal(std::string_view value);
std::vector<std::string> SplitLine(std::string_view line, char delimiter, std::size_t max_fields);
std::uint64_t StableHash64(std::string_view value);

}  // namespace trading_assistant::native_marketdata

#endif
