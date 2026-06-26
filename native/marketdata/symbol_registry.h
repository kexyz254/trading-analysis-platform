#ifndef TRADING_ASSISTANT_NATIVE_SYMBOL_REGISTRY_H_
#define TRADING_ASSISTANT_NATIVE_SYMBOL_REGISTRY_H_

#include <string>
#include <string_view>
#include <vector>

namespace trading_assistant::native_marketdata {

struct SymbolRecord {
  std::string symbol;
  std::string venue;
  std::string asset_class;
};

std::vector<SymbolRecord> ParseSymbolRegistry(std::string_view input);
bool ValidateSymbolRegistry(std::string_view input);

}  // namespace trading_assistant::native_marketdata

#endif
