#include <cstddef>
#include <cstdint>
#include <string_view>

#include "native/marketdata/market_data_parser.h"

extern "C" int LLVMFuzzerTestOneInput(const std::uint8_t* data, std::size_t size) {
  if (data == nullptr || size == 0 || size > 65536) {
    return 0;
  }

  const std::string_view input(reinterpret_cast<const char*>(data), size);

  volatile bool valid =
      trading_assistant::native_marketdata::ValidateOrderStream(input, 512);

  (void)valid;
  return 0;
}
