#include <cstddef>
#include <cstdint>
#include <string_view>

#include "native/marketdata/market_event_frame.h"

extern "C" int LLVMFuzzerTestOneInput(const std::uint8_t* data, std::size_t size) {
  if (data == nullptr || size == 0 || size > 524288) {
    return 0;
  }

  const std::string_view input(reinterpret_cast<const char*>(data), size);

  volatile bool valid =
      trading_assistant::native_marketdata::ValidateMarketEventFrames(input);

  (void)valid;
  return 0;
}
