#include <cstddef>
#include <cstdint>
#include <string_view>

#include "native/marketdata/market_data_router.h"

extern "C" int LLVMFuzzerTestOneInput(const std::uint8_t* data, std::size_t size) {
  if (data == nullptr || size == 0 || size > 131072) {
    return 0;
  }

  const std::string_view input(reinterpret_cast<const char*>(data), size);

  volatile bool valid =
      trading_assistant::native_marketdata::ValidateMarketDataRoutes(input);

  (void)valid;
  return 0;
}
