#ifndef TRADING_ASSISTANT_NATIVE_PORTFOLIO_SNAPSHOT_H_
#define TRADING_ASSISTANT_NATIVE_PORTFOLIO_SNAPSHOT_H_

#include <cstdint>
#include <string>
#include <string_view>
#include <vector>

namespace trading_assistant::native_marketdata {

struct PortfolioPosition {
  std::string account;
  std::string symbol;
  std::int64_t quantity = 0;
  double average_price = 0.0;
  double last_price = 0.0;
};

struct PortfolioSnapshot {
  std::vector<PortfolioPosition> positions;
  double gross_exposure = 0.0;
  double net_exposure = 0.0;
};

PortfolioSnapshot ParsePortfolioSnapshotOrEmpty(std::string_view input);
bool ValidatePortfolioSnapshot(std::string_view input);

}  // namespace trading_assistant::native_marketdata

#endif
