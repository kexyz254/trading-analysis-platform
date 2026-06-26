#ifndef TRADING_ASSISTANT_NATIVE_BINARY_FEED_DECODER_H_
#define TRADING_ASSISTANT_NATIVE_BINARY_FEED_DECODER_H_

#include <cstdint>
#include <string>
#include <string_view>
#include <vector>

namespace trading_assistant::native_marketdata {

enum class FeedMessageType {
  kTrade = 1,
  kQuote = 2,
  kHalt = 3,
  kHeartbeat = 4,
};

struct FeedMessage {
  FeedMessageType type = FeedMessageType::kHeartbeat;
  std::string symbol;
  std::uint64_t sequence = 0;
  double price = 0.0;
  std::uint64_t quantity = 0;
};

std::vector<FeedMessage> DecodeBinaryFeedOrEmpty(std::string_view input);
bool ValidateBinaryFeed(std::string_view input);

}  // namespace trading_assistant::native_marketdata

#endif
