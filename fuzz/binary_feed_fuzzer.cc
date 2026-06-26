#include <cstddef>
#include <cstdint>
#include <string_view>

#include "native/marketdata/binary_feed_decoder.h"

extern "C" int LLVMFuzzerTestOneInput(const std::uint8_t* data, std::size_t size) {
  if (data == nullptr || size == 0 || size > 262144) {
    return 0;
  }

  const std::string_view input(reinterpret_cast<const char*>(data), size);

  volatile bool valid =
      trading_assistant::native_marketdata::ValidateBinaryFeed(input);

  (void)valid;
  return 0;
}
