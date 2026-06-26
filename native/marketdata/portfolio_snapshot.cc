#include "native/marketdata/portfolio_snapshot.h"

#include "native/marketdata/numeric_parser.h"
#include "native/marketdata/string_utils.h"

#include <cmath>
#include <set>
#include <tuple>
#include <utility>

namespace trading_assistant::native_marketdata {
namespace {

bool IsValidAccount(std::string_view account) {
  return IsAsciiSymbol(account, 32);
}

bool PositionHasReasonableExposure(const PortfolioPosition& position) {
  const double notional = static_cast<double>(position.quantity) * position.last_price;
  return std::isfinite(notional) && std::fabs(notional) <= 1000000000000.0;
}

}  // namespace

PortfolioSnapshot ParsePortfolioSnapshotOrEmpty(std::string_view input) {
  PortfolioSnapshot snapshot;

  if (input.empty() || input.size() > 262144) {
    return {};
  }

  std::set<std::tuple<std::string, std::string>> seen;
  std::size_t offset = 0;

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

    const auto fields = SplitLine(line, ',', 8);
    if (fields.size() != 5) {
      return {};
    }

    if (!IsValidAccount(fields[0]) || !IsAsciiSymbol(fields[1], 24)) {
      return {};
    }

    const auto quantity = ParseSigned64(fields[2]);
    const auto average_price = ParseFiniteDouble(fields[3]);
    const auto last_price = ParseFiniteDouble(fields[4]);

    if (!quantity.has_value() || !average_price.has_value() || !last_price.has_value()) {
      return {};
    }

    if (!IsValidPrice(*average_price) || !IsValidPrice(*last_price)) {
      return {};
    }

    PortfolioPosition position{
        fields[0],
        ToUpperAscii(fields[1]),
        *quantity,
        *average_price,
        *last_price,
    };

    if (!PositionHasReasonableExposure(position)) {
      return {};
    }

    const auto key = std::make_tuple(position.account, position.symbol);
    if (seen.count(key) != 0) {
      return {};
    }

    seen.insert(key);

    const double exposure = static_cast<double>(position.quantity) * position.last_price;
    snapshot.net_exposure += exposure;
    snapshot.gross_exposure += std::fabs(exposure);
    snapshot.positions.push_back(std::move(position));

    if (snapshot.positions.size() > 8192) {
      return {};
    }

    if (next == input.size()) break;
  }

  return snapshot;
}

bool ValidatePortfolioSnapshot(std::string_view input) {
  const PortfolioSnapshot snapshot = ParsePortfolioSnapshotOrEmpty(input);
  return !snapshot.positions.empty() &&
         std::isfinite(snapshot.gross_exposure) &&
         std::isfinite(snapshot.net_exposure) &&
         snapshot.gross_exposure >= std::fabs(snapshot.net_exposure);
}

}  // namespace trading_assistant::native_marketdata
