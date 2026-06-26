#ifndef TRADING_ASSISTANT_NATIVE_REPLAY_EVENT_LOG_H_
#define TRADING_ASSISTANT_NATIVE_REPLAY_EVENT_LOG_H_

#include <cstdint>
#include <string>
#include <string_view>
#include <vector>

namespace trading_assistant::native_marketdata {

struct ReplayEvent {
  std::uint64_t timestamp_ns = 0;
  std::uint64_t sequence = 0;
  std::string event_type;
  std::string payload;
};

std::vector<ReplayEvent> ParseReplayEventLog(std::string_view input);
bool ValidateReplayEventLog(std::string_view input);

}  // namespace trading_assistant::native_marketdata

#endif
