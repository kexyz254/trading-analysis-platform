#include "native/marketdata/market_event_frame.h"

#include "native/marketdata/numeric_parser.h"
#include "native/marketdata/string_utils.h"

#include <cstdint>

namespace trading_assistant::native_marketdata {
namespace {

bool IsKnownFrameType(std::string_view value) {
  const std::string type = ToUpperAscii(value);
  return type == "TRADE" ||
         type == "QUOTE" ||
         type == "BOOK" ||
         type == "ORDER" ||
         type == "RISK" ||
         type == "SESSION" ||
         type == "FEATURE" ||
         type == "CONTROL";
}

bool PayloadIsSafe(std::string_view payload) {
  if (payload.empty() || payload.size() > 8192) {
    return false;
  }

  for (unsigned char c : payload) {
    if (c < 9 || (c > 13 && c < 32)) {
      return false;
    }
  }

  return true;
}

}  // namespace

std::vector<MarketEventFrame> ParseMarketEventFrames(std::string_view input) {
  std::vector<MarketEventFrame> frames;

  if (input.empty() || input.size() > 524288) {
    return {};
  }

  std::uint64_t previous_sequence = 0;
  bool have_previous = false;
  std::size_t offset = 0;

  while (offset <= input.size()) {
    std::size_t next = input.find('\n', offset);
    if (next == std::string_view::npos) {
      next = input.size();
    }

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

    if (ToUpperAscii(fields[0]) != "FRAME") {
      return {};
    }

    const auto sequence = ParseUnsigned64(fields[1]);
    const auto flags = ParseUnsigned64(fields[3]);
    const auto declared_length = ParseUnsigned64(fields[4]);

    if (!sequence.has_value() || !flags.has_value() || !declared_length.has_value()) {
      return {};
    }

    if (*flags > 65535 || *declared_length > 8192) {
      return {};
    }

    if (!IsKnownFrameType(fields[2])) {
      return {};
    }

    std::size_t payload_start = line.find(fields[4]);
    if (payload_start == std::string_view::npos) {
      return {};
    }

    payload_start += fields[4].size();
    if (payload_start >= line.size() || line[payload_start] != '|') {
      return {};
    }

    const std::string payload = std::string(line.substr(payload_start + 1));

    if (payload.size() != *declared_length || !PayloadIsSafe(payload)) {
      return {};
    }

    if (have_previous && *sequence <= previous_sequence) {
      return {};
    }

    previous_sequence = *sequence;
    have_previous = true;

    frames.push_back(MarketEventFrame{
        *sequence,
        ToUpperAscii(fields[2]),
        static_cast<std::uint32_t>(*flags),
        payload,
    });

    if (frames.size() > 8192) {
      return {};
    }

    if (next == input.size()) break;
  }

  return frames;
}

bool ValidateMarketEventFrames(std::string_view input) {
  return !ParseMarketEventFrames(input).empty();
}

}  // namespace trading_assistant::native_marketdata
