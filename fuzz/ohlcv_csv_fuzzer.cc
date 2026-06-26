#include <cstddef>
#include <cstdint>
#include <string_view>

#include "native/marketdata/market_data_parser.h"

extern "C" int LLVMFuzzerTestOneInput(const std::uint8_t* data, std::size_t size) {
  if (data == nullptr || size == 0 || size > 65536) {
    return 0;
  }

  const std::string_view input(reinterpret_cast<const char*>(data), size);

  trading_assistant::native_marketdata::ParserOptions options;
  options.allow_header = true;
  options.require_monotonic_timestamps = (size % 2) == 0;
  options.max_rows = 512;

  volatile bool valid =
      trading_assistant::native_marketdata::ValidateOhlcvCsv(input, options);

  (void)valid;
  return 0;
}
