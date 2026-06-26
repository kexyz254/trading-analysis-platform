#include "native/marketdata/order_book_snapshot.h"

#include "native/marketdata/numeric_parser.h"
#include "native/marketdata/string_utils.h"

#include <algorithm>

namespace trading_assistant::native_marketdata {
namespace {

bool AppendLevel(std::vector<BookLevel>* levels, std::string_view price_text, std::string_view quantity_text) {
  const auto price = ParseFiniteDouble(price_text);
  const auto quantity = ParseUnsigned64(quantity_text);

  if (!price.has_value() || !quantity.has_value()) {
    return false;
  }

  if (!IsValidPrice(*price) || !IsValidQuantity(*quantity)) {
    return false;
  }

  levels->push_back(BookLevel{*price, *quantity});
  return true;
}

bool BidsAreSorted(const std::vector<BookLevel>& bids) {
  for (std::size_t i = 1; i < bids.size(); ++i) {
    if (bids[i].price >= bids[i - 1].price) {
      return false;
    }
  }
  return true;
}

bool AsksAreSorted(const std::vector<BookLevel>& asks) {
  for (std::size_t i = 1; i < asks.size(); ++i) {
    if (asks[i].price <= asks[i - 1].price) {
      return false;
    }
  }
  return true;
}

bool SpreadIsValid(const std::vector<BookLevel>& bids, const std::vector<BookLevel>& asks) {
  if (bids.empty() || asks.empty()) {
    return true;
  }
  return bids.front().price < asks.front().price;
}

}  // namespace

OrderBookSnapshot ParseOrderBookSnapshotOrEmpty(std::string_view input) {
  OrderBookSnapshot snapshot;

  if (input.size() > 131072) {
    return snapshot;
  }

  std::size_t offset = 0;
  bool saw_header = false;

  while (offset <= input.size()) {
    std::size_t next = input.find('\n', offset);
    if (next == std::string_view::npos) {
      next = input.size();
    }

    const std::string_view line = input.substr(offset, next - offset);
    offset = next + 1;

    if (TrimAscii(line).empty()) {
      if (next == input.size()) break;
      continue;
    }

    const auto fields = SplitLine(line, '|', 8);
    if (fields.empty()) {
      continue;
    }

    const std::string tag = ToUpperAscii(fields[0]);

    if (tag == "BOOK") {
      if (fields.size() != 3) {
        snapshot.symbol.clear();
        return snapshot;
      }

      if (!IsAsciiSymbol(fields[1], 24)) {
        snapshot.symbol.clear();
        return snapshot;
      }

      const auto sequence = ParseUnsigned64(fields[2]);
      if (!sequence.has_value()) {
        snapshot.symbol.clear();
        return snapshot;
      }

      snapshot.symbol = fields[1];
      snapshot.sequence = *sequence;
      saw_header = true;
      continue;
    }

    if (!saw_header) {
      snapshot.symbol.clear();
      return snapshot;
    }

    if (tag == "BID") {
      if (fields.size() != 3 || !AppendLevel(&snapshot.bids, fields[1], fields[2])) {
        snapshot.symbol.clear();
        return snapshot;
      }
    } else if (tag == "ASK") {
      if (fields.size() != 3 || !AppendLevel(&snapshot.asks, fields[1], fields[2])) {
        snapshot.symbol.clear();
        return snapshot;
      }
    } else {
      snapshot.symbol.clear();
      return snapshot;
    }

    if (snapshot.bids.size() + snapshot.asks.size() > 512) {
      snapshot.symbol.clear();
      return snapshot;
    }

    if (next == input.size()) break;
  }

  return snapshot;
}

bool ValidateOrderBookSnapshot(std::string_view input) {
  const OrderBookSnapshot snapshot = ParseOrderBookSnapshotOrEmpty(input);

  if (snapshot.symbol.empty()) {
    return false;
  }

  if (snapshot.bids.empty() && snapshot.asks.empty()) {
    return false;
  }

  if (!BidsAreSorted(snapshot.bids)) {
    return false;
  }

  if (!AsksAreSorted(snapshot.asks)) {
    return false;
  }

  return SpreadIsValid(snapshot.bids, snapshot.asks);
}

}  // namespace trading_assistant::native_marketdata
