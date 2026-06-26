#include "native/marketdata/corporate_actions.h"

#include "native/marketdata/numeric_parser.h"
#include "native/marketdata/string_utils.h"
#include <cctype>

namespace trading_assistant::native_marketdata {
namespace {

bool IsIsoDate(std::string_view value) {
  if (value.size() != 10) return false;
  for (std::size_t i = 0; i < value.size(); ++i) {
    if (i == 4 || i == 7) {
      if (value[i] != '-') return false;
    } else if (!std::isdigit(static_cast<unsigned char>(value[i]))) {
      return false;
    }
  }
  return true;
}

bool ParseActionType(std::string_view value, CorporateActionType* out) {
  const std::string type = ToUpperAscii(value);
  if (type == "SPLIT") {
    *out = CorporateActionType::kSplit;
    return true;
  }
  if (type == "DIVIDEND") {
    *out = CorporateActionType::kDividend;
    return true;
  }
  if (type == "SYMBOL_CHANGE") {
    *out = CorporateActionType::kSymbolChange;
    return true;
  }
  if (type == "DELISTING") {
    *out = CorporateActionType::kDelisting;
    return true;
  }
  return false;
}

bool ValidateAction(const CorporateAction& action) {
  if (!IsAsciiSymbol(action.symbol, 24) || !IsIsoDate(action.effective_date)) {
    return false;
  }

  switch (action.type) {
    case CorporateActionType::kSplit:
      return action.ratio_numerator > 0.0 &&
             action.ratio_denominator > 0.0 &&
             action.cash_amount == 0.0 &&
             action.new_symbol.empty();

    case CorporateActionType::kDividend:
      return action.cash_amount >= 0.0 &&
             action.cash_amount <= 1000000.0 &&
             action.new_symbol.empty();

    case CorporateActionType::kSymbolChange:
      return IsAsciiSymbol(action.new_symbol, 24) &&
             action.new_symbol != action.symbol;

    case CorporateActionType::kDelisting:
      return action.new_symbol.empty();

    default:
      return false;
  }
}

}  // namespace

std::vector<CorporateAction> ParseCorporateActions(std::string_view input) {
  std::vector<CorporateAction> actions;

  if (input.empty() || input.size() > 131072) {
    return {};
  }

  std::size_t offset = 0;

  while (offset <= input.size()) {
    std::size_t next = input.find('\n', offset);
    if (next == std::string_view::npos) next = input.size();

    const std::string_view line = input.substr(offset, next - offset);
    offset = next + 1;

    if (TrimAscii(line).empty()) {
      if (next == input.size()) break;
      continue;
    }

    const auto fields = SplitLine(line, '|', 8);
    if (fields.size() != 7) {
      return {};
    }

    CorporateAction action;
    if (!ParseActionType(fields[0], &action.type)) {
      return {};
    }

    action.symbol = ToUpperAscii(fields[1]);
    action.effective_date = fields[2];

    const auto numerator = ParseFiniteDouble(fields[3]);
    const auto denominator = ParseFiniteDouble(fields[4]);
    const auto cash = ParseFiniteDouble(fields[5]);

    if (!numerator.has_value() || !denominator.has_value() || !cash.has_value()) {
      return {};
    }

    action.ratio_numerator = *numerator;
    action.ratio_denominator = *denominator;
    action.cash_amount = *cash;
    action.new_symbol = ToUpperAscii(fields[6]);

    if (!ValidateAction(action)) {
      return {};
    }

    actions.push_back(std::move(action));

    if (actions.size() > 4096) {
      return {};
    }

    if (next == input.size()) break;
  }

  return actions;
}

bool ValidateCorporateActions(std::string_view input) {
  return !ParseCorporateActions(input).empty();
}

}  // namespace trading_assistant::native_marketdata
