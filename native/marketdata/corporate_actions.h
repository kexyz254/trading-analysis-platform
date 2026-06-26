#ifndef TRADING_ASSISTANT_NATIVE_CORPORATE_ACTIONS_H_
#define TRADING_ASSISTANT_NATIVE_CORPORATE_ACTIONS_H_

#include <cstdint>
#include <string>
#include <string_view>
#include <vector>

namespace trading_assistant::native_marketdata {

enum class CorporateActionType {
  kSplit,
  kDividend,
  kSymbolChange,
  kDelisting,
};

struct CorporateAction {
  CorporateActionType type = CorporateActionType::kSplit;
  std::string symbol;
  std::string effective_date;
  double ratio_numerator = 1.0;
  double ratio_denominator = 1.0;
  double cash_amount = 0.0;
  std::string new_symbol;
};

std::vector<CorporateAction> ParseCorporateActions(std::string_view input);
bool ValidateCorporateActions(std::string_view input);

}  // namespace trading_assistant::native_marketdata

#endif
