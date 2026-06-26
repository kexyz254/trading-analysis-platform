#include "native/marketdata/symbol_registry.h"

#include "native/marketdata/string_utils.h"

#include <set>
#include <tuple>
#include <utility>

namespace trading_assistant::native_marketdata {

std::vector<SymbolRecord> ParseSymbolRegistry(std::string_view input) {
  std::vector<SymbolRecord> records;
  std::set<std::tuple<std::string, std::string>> seen;

  if (input.size() > 65536) {
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

    const auto fields = SplitLine(line, ',', 5);
    if (fields.size() != 3) {
      return {};
    }

    SymbolRecord record{ToUpperAscii(fields[0]), ToUpperAscii(fields[1]), ToUpperAscii(fields[2])};

    if (!IsAsciiSymbol(record.symbol, 24) || !IsAsciiSymbol(record.venue, 16)) {
      return {};
    }

    if (!(record.asset_class == "EQUITY" ||
          record.asset_class == "FX" ||
          record.asset_class == "FUTURE" ||
          record.asset_class == "CRYPTO" ||
          record.asset_class == "OPTION")) {
      return {};
    }

    const auto key = std::make_tuple(record.symbol, record.venue);
    if (seen.count(key) != 0) {
      return {};
    }

    seen.insert(key);
    records.push_back(std::move(record));

    if (records.size() > 4096) {
      return {};
    }

    if (next == input.size()) break;
  }

  return records;
}

bool ValidateSymbolRegistry(std::string_view input) {
  return !ParseSymbolRegistry(input).empty();
}

}  // namespace trading_assistant::native_marketdata
