#include "native/marketdata/replay_event_log.h"

#include "native/marketdata/numeric_parser.h"
#include "native/marketdata/string_utils.h"

namespace trading_assistant::native_marketdata {
namespace {

bool IsKnownEventType(std::string_view value) {
  const std::string type = ToUpperAscii(value);
  return type == "TRADE" ||
         type == "QUOTE" ||
         type == "ORDER" ||
         type == "BOOK" ||
         type == "HALT" ||
         type == "SESSION" ||
         type == "RISK";
}

bool PayloadLooksSafe(std::string_view payload) {
  if (payload.empty() || payload.size() > 4096) {
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

std::vector<ReplayEvent> ParseReplayEventLog(std::string_view input) {
  std::vector<ReplayEvent> events;

  if (input.empty() || input.size() > 524288) {
    return {};
  }

  std::uint64_t previous_timestamp = 0;
  std::uint64_t previous_sequence = 0;
  bool have_previous = false;

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

    const auto fields = SplitLine(line, '|', 5);
    if (fields.size() != 4) {
      return {};
    }

    const auto timestamp = ParseUnsigned64(fields[0]);
    const auto sequence = ParseUnsigned64(fields[1]);

    if (!timestamp.has_value() || !sequence.has_value()) {
      return {};
    }

    if (!IsKnownEventType(fields[2]) || !PayloadLooksSafe(fields[3])) {
      return {};
    }

    if (have_previous) {
      if (*timestamp < previous_timestamp) {
        return {};
      }
      if (*timestamp == previous_timestamp && *sequence <= previous_sequence) {
        return {};
      }
    }

    previous_timestamp = *timestamp;
    previous_sequence = *sequence;
    have_previous = true;

    events.push_back(ReplayEvent{*timestamp, *sequence, ToUpperAscii(fields[2]), fields[3]});

    if (events.size() > 16384) {
      return {};
    }

    if (next == input.size()) break;
  }

  return events;
}

bool ValidateReplayEventLog(std::string_view input) {
  return !ParseReplayEventLog(input).empty();
}

}  // namespace trading_assistant::native_marketdata
