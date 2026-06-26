#ifndef TRADING_ASSISTANT_NATIVE_VOLATILITY_SURFACE_H_
#define TRADING_ASSISTANT_NATIVE_VOLATILITY_SURFACE_H_

#include <string>
#include <string_view>
#include <vector>

namespace trading_assistant::native_marketdata {

struct VolatilityPoint {
  std::string symbol;
  std::string expiry;
  double strike = 0.0;
  double call_iv = 0.0;
  double put_iv = 0.0;
};

std::vector<VolatilityPoint> ParseVolatilitySurface(std::string_view input);
bool ValidateVolatilitySurface(std::string_view input);

}  // namespace trading_assistant::native_marketdata

#endif
