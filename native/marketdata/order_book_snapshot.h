#ifndef TRADING_ASSISTANT_NATIVE_ORDER_BOOK_SNAPSHOT_H_
#define TRADING_ASSISTANT_NATIVE_ORDER_BOOK_SNAPSHOT_H_

#include <cstdint>
#include <string>
#include <string_view>
#include <vector>

namespace trading_assistant::native_marketdata {

struct BookLevel {
  double price = 0.0;
  std::uint64_t quantity = 0;
};

struct OrderBookSnapshot {
  std::string symbol;
  std::uint64_t sequence = 0;
  std::vector<BookLevel> bids;
  std::vector<BookLevel> asks;
};

bool ValidateOrderBookSnapshot(std::string_view input);
OrderBookSnapshot ParseOrderBookSnapshotOrEmpty(std::string_view input);

}  // namespace trading_assistant::native_marketdata

#endif
