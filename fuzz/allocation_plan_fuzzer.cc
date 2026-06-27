#include <cstddef>
#include <cstdint>
#include <string_view>
#include <vector>

#include "native/marketdata/allocation_plan.h"

extern "C" int LLVMFuzzerTestOneInput(const std::uint8_t* data, std::size_t size) {
  if (data == nullptr || size == 0 || size > 262144) {
    return 0;
  }

  const std::string_view input(reinterpret_cast<const char*>(data), size);

  const auto document =
      trading_assistant::native_marketdata::ParseAllocationPlanDocument(input);
  const auto targets =
      trading_assistant::native_marketdata::ParseAllocationPlan(input);

  volatile bool valid =
      trading_assistant::native_marketdata::ValidateAllocationPlan(input);

  volatile std::size_t observed = document.accounts.size() ^ targets.size();
  (void)observed;
  (void)valid;
  return 0;
}
