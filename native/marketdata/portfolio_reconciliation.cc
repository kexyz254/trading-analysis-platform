#include "portfolio_reconciliation.h"

#include <algorithm>
#include <cmath>
#include <iomanip>
#include <sstream>
#include <stdexcept>
#include <unordered_set>

namespace marketdata {
namespace {

std::string Trim(const std::string& value) {
  std::size_t begin = 0;
  while (begin < value.size() &&
         std::isspace(static_cast<unsigned char>(value[begin]))) {
    ++begin;
  }

  std::size_t end = value.size();
  while (end > begin &&
         std::isspace(static_cast<unsigned char>(value[end - 1]))) {
    --end;
  }

  return value.substr(begin, end - begin);
}

std::string Upper(const std::string& value) {
  std::string out = value;
  std::transform(out.begin(), out.end(), out.begin(), [](unsigned char c) {
    return static_cast<char>(std::toupper(c));
  });
  return out;
}

std::string NumberText(double value) {
  std::ostringstream out;
  out << std::fixed << std::setprecision(6) << value;
  return out.str();
}

bool IsErrorSeverity(ReconcileSeverity severity) {
  return severity == ReconcileSeverity::kError ||
         severity == ReconcileSeverity::kCritical;
}

}  // namespace

bool PositionKey::operator<(const PositionKey& other) const {
  if (account_id != other.account_id) return account_id < other.account_id;
  if (symbol != other.symbol) return symbol < other.symbol;
  return venue < other.venue;
}

std::string PositionKey::ToString() const {
  if (!venue.empty()) {
    return account_id + ":" + symbol + ":" + venue;
  }
  return account_id + ":" + symbol;
}

PositionKey PositionRecord::Key() const {
  return PositionKey{
      NormalizeAccountId(account_id),
      NormalizeSymbol(symbol),
      NormalizeVenue(venue),
  };
}

double PositionRecord::MarketValue() const {
  return quantity * market_price;
}

std::string CashBalanceRecord::Key() const {
  return NormalizeAccountId(account_id) + ":" + NormalizeCurrency(currency);
}

double ExecutionFillRecord::SignedQuantity() const {
  const std::string normalized_side = Upper(Trim(side));

  if (normalized_side == "BUY" ||
      normalized_side == "B" ||
      normalized_side == "LONG") {
    return quantity;
  }

  if (normalized_side == "SELL" ||
      normalized_side == "S" ||
      normalized_side == "SHORT") {
    return -quantity;
  }

  throw std::invalid_argument("unsupported execution side: " + side);
}

double ExecutionFillRecord::CashEffect() const {
  const std::string normalized_side = Upper(Trim(side));
  const double notional = quantity * price;

  if (normalized_side == "BUY" ||
      normalized_side == "B" ||
      normalized_side == "LONG") {
    return -notional - fees;
  }

  if (normalized_side == "SELL" ||
      normalized_side == "S" ||
      normalized_side == "SHORT") {
    return notional - fees;
  }

  throw std::invalid_argument("unsupported execution side: " + side);
}

bool ReconcileSummary::Ok() const {
  for (const ReconcileBreak& item : breaks) {
    if (IsErrorSeverity(item.severity)) {
      return false;
    }
  }
  return true;
}

std::uint64_t ReconcileSummary::ErrorCount() const {
  std::uint64_t total = 0;
  for (const ReconcileBreak& item : breaks) {
    if (IsErrorSeverity(item.severity)) {
      ++total;
    }
  }
  return total;
}

std::uint64_t ReconcileSummary::WarningCount() const {
  std::uint64_t total = 0;
  for (const ReconcileBreak& item : breaks) {
    if (item.severity == ReconcileSeverity::kWarning) {
      ++total;
    }
  }
  return total;
}

PortfolioReconciler::PortfolioReconciler()
    : quantity_tolerance_(0.000001),
      cash_tolerance_(0.01),
      price_tolerance_(0.0001) {}

void PortfolioReconciler::SetQuantityTolerance(double value) {
  if (value < 0.0 || !std::isfinite(value)) {
    throw std::invalid_argument("quantity tolerance must be finite and non-negative");
  }
  quantity_tolerance_ = value;
}

void PortfolioReconciler::SetCashTolerance(double value) {
  if (value < 0.0 || !std::isfinite(value)) {
    throw std::invalid_argument("cash tolerance must be finite and non-negative");
  }
  cash_tolerance_ = value;
}

void PortfolioReconciler::SetPriceTolerance(double value) {
  if (value < 0.0 || !std::isfinite(value)) {
    throw std::invalid_argument("price tolerance must be finite and non-negative");
  }
  price_tolerance_ = value;
}

ReconcileSummary PortfolioReconciler::ReconcilePositions(
    const std::vector<PositionRecord>& expected,
    const std::vector<PositionRecord>& broker) const {
  ReconcileSummary summary;

  const std::map<PositionKey, PositionRecord> expected_index =
      IndexPositions(expected, "expected", &summary);
  const std::map<PositionKey, PositionRecord> broker_index =
      IndexPositions(broker, "broker", &summary);

  std::map<PositionKey, bool> all_keys;
  for (const auto& item : expected_index) {
    all_keys[item.first] = true;
  }
  for (const auto& item : broker_index) {
    all_keys[item.first] = true;
  }

  for (const auto& key_item : all_keys) {
    const PositionKey& key = key_item.first;
    const auto expected_it = expected_index.find(key);
    const auto broker_it = broker_index.find(key);
    ++summary.checked_positions;

    if (expected_it == expected_index.end() && broker_it != broker_index.end()) {
      summary.breaks.push_back(ReconcileBreak{
          ReconcileBreakType::kExtraPosition,
          ReconcileSeverity::kError,
          key.ToString(),
          "missing",
          NumberText(broker_it->second.quantity),
          NumberText(broker_it->second.quantity),
          "broker has a position that is absent from expected books",
      });
      continue;
    }

    if (broker_it == broker_index.end() && expected_it != expected_index.end()) {
      summary.breaks.push_back(ReconcileBreak{
          ReconcileBreakType::kMissingPosition,
          ReconcileSeverity::kError,
          key.ToString(),
          NumberText(expected_it->second.quantity),
          "missing",
          NumberText(expected_it->second.quantity),
          "expected position is absent from broker books",
      });
      continue;
    }

    const PositionRecord& expected_position = expected_it->second;
    const PositionRecord& broker_position = broker_it->second;

    const double quantity_diff =
        broker_position.quantity - expected_position.quantity;

    if (std::fabs(quantity_diff) > quantity_tolerance_) {
      summary.breaks.push_back(ReconcileBreak{
          ReconcileBreakType::kQuantityMismatch,
          ReconcileSeverity::kError,
          key.ToString(),
          NumberText(expected_position.quantity),
          NumberText(broker_position.quantity),
          NumberText(quantity_diff),
          "position quantity mismatch exceeds tolerance",
      });
    }

    const double price_diff =
        broker_position.market_price - expected_position.market_price;

    if (expected_position.market_price != 0.0 &&
        std::fabs(price_diff) > price_tolerance_) {
      summary.breaks.push_back(ReconcileBreak{
          ReconcileBreakType::kPriceMismatch,
          ReconcileSeverity::kWarning,
          key.ToString(),
          NumberText(expected_position.market_price),
          NumberText(broker_position.market_price),
          NumberText(price_diff),
          "market price mismatch exceeds tolerance",
      });
    }
  }

  return summary;
}

ReconcileSummary PortfolioReconciler::ReconcileCash(
    const std::vector<CashBalanceRecord>& expected,
    const std::vector<CashBalanceRecord>& broker) const {
  ReconcileSummary summary;

  const std::map<std::string, CashBalanceRecord> expected_index =
      IndexCash(expected, "expected", &summary);
  const std::map<std::string, CashBalanceRecord> broker_index =
      IndexCash(broker, "broker", &summary);

  std::map<std::string, bool> all_keys;
  for (const auto& item : expected_index) {
    all_keys[item.first] = true;
  }
  for (const auto& item : broker_index) {
    all_keys[item.first] = true;
  }

  for (const auto& key_item : all_keys) {
    const std::string& key = key_item.first;
    const auto expected_it = expected_index.find(key);
    const auto broker_it = broker_index.find(key);
    ++summary.checked_cash_balances;

    const double expected_amount =
        expected_it == expected_index.end() ? 0.0 : expected_it->second.amount;
    const double broker_amount =
        broker_it == broker_index.end() ? 0.0 : broker_it->second.amount;
    const double diff = broker_amount - expected_amount;

    if (std::fabs(diff) > cash_tolerance_) {
      const ReconcileSeverity severity =
          std::fabs(diff) > 10000.0
              ? ReconcileSeverity::kCritical
              : ReconcileSeverity::kError;

      summary.breaks.push_back(ReconcileBreak{
          ReconcileBreakType::kCashMismatch,
          severity,
          key,
          NumberText(expected_amount),
          NumberText(broker_amount),
          NumberText(diff),
          "cash balance mismatch exceeds tolerance",
      });
    }
  }

  return summary;
}

std::vector<PositionRecord> PortfolioReconciler::ExpectedPositionsFromFills(
    const std::vector<PositionRecord>& opening_positions,
    const std::vector<ExecutionFillRecord>& fills) const {
  std::map<PositionKey, PositionRecord> index;

  for (const PositionRecord& position : opening_positions) {
    index[position.Key()] = position;
  }

  for (const ExecutionFillRecord& fill : fills) {
    const PositionKey key{
        NormalizeAccountId(fill.account_id),
        NormalizeSymbol(fill.symbol),
        NormalizeVenue(fill.venue),
    };

    auto it = index.find(key);
    if (it == index.end()) {
      PositionRecord created;
      created.account_id = NormalizeAccountId(fill.account_id);
      created.symbol = NormalizeSymbol(fill.symbol);
      created.venue = NormalizeVenue(fill.venue);
      created.currency = NormalizeCurrency(fill.currency);
      created.quantity = 0.0;
      created.market_price = fill.price;
      it = index.insert(std::make_pair(key, created)).first;
    }

    it->second.quantity += fill.SignedQuantity();
    it->second.market_price = fill.price;
  }

  std::vector<PositionRecord> output;
  output.reserve(index.size());
  for (const auto& item : index) {
    output.push_back(item.second);
  }

  return output;
}

std::vector<CashBalanceRecord> PortfolioReconciler::CashFromFills(
    const std::vector<CashBalanceRecord>& opening_cash,
    const std::vector<ExecutionFillRecord>& fills) const {
  std::map<std::string, CashBalanceRecord> index;

  for (const CashBalanceRecord& cash : opening_cash) {
    index[cash.Key()] = cash;
  }

  for (const ExecutionFillRecord& fill : fills) {
    CashBalanceRecord cash;
    cash.account_id = NormalizeAccountId(fill.account_id);
    cash.currency = NormalizeCurrency(fill.currency);
    cash.amount = 0.0;

    const std::string key = cash.Key();

    auto it = index.find(key);
    if (it == index.end()) {
      it = index.insert(std::make_pair(key, cash)).first;
    }

    it->second.amount += fill.CashEffect();
  }

  std::vector<CashBalanceRecord> output;
  output.reserve(index.size());
  for (const auto& item : index) {
    output.push_back(item.second);
  }

  return output;
}

ReconcileSummary PortfolioReconciler::MergeSummaries(
    const std::vector<ReconcileSummary>& summaries) const {
  ReconcileSummary merged;

  for (const ReconcileSummary& summary : summaries) {
    merged.breaks.insert(
        merged.breaks.end(), summary.breaks.begin(), summary.breaks.end());
    merged.checked_positions += summary.checked_positions;
    merged.checked_cash_balances += summary.checked_cash_balances;
    merged.duplicate_records += summary.duplicate_records;
    merged.invalid_records += summary.invalid_records;
  }

  return merged;
}

std::map<PositionKey, PositionRecord> PortfolioReconciler::IndexPositions(
    const std::vector<PositionRecord>& positions,
    const std::string& source,
    ReconcileSummary* summary) const {
  std::map<PositionKey, PositionRecord> index;

  for (const PositionRecord& position : positions) {
    const PositionKey key = position.Key();

    if (key.account_id.empty() || key.symbol.empty()) {
      ++summary->invalid_records;
      summary->breaks.push_back(ReconcileBreak{
          ReconcileBreakType::kInvalidRecord,
          ReconcileSeverity::kError,
          source,
          "valid position key",
          key.ToString(),
          "",
          "position has missing account or symbol",
      });
      continue;
    }

    if (!std::isfinite(position.quantity) ||
        !std::isfinite(position.market_price)) {
      ++summary->invalid_records;
      summary->breaks.push_back(ReconcileBreak{
          ReconcileBreakType::kInvalidRecord,
          ReconcileSeverity::kError,
          key.ToString(),
          "finite quantity and price",
          "non-finite numeric value",
          "",
          "position contains non-finite numeric fields",
      });
      continue;
    }

    if (index.find(key) != index.end()) {
      ++summary->duplicate_records;
      summary->breaks.push_back(ReconcileBreak{
          ReconcileBreakType::kDuplicateRecord,
          ReconcileSeverity::kError,
          key.ToString(),
          "unique position",
          "duplicate position",
          "",
          "duplicate position record",
      });
      continue;
    }

    index[key] = position;
  }

  return index;
}

std::map<std::string, CashBalanceRecord> PortfolioReconciler::IndexCash(
    const std::vector<CashBalanceRecord>& balances,
    const std::string& source,
    ReconcileSummary* summary) const {
  std::map<std::string, CashBalanceRecord> index;

  for (const CashBalanceRecord& balance : balances) {
    const std::string key = balance.Key();

    if (NormalizeAccountId(balance.account_id).empty() ||
        NormalizeCurrency(balance.currency).empty()) {
      ++summary->invalid_records;
      summary->breaks.push_back(ReconcileBreak{
          ReconcileBreakType::kInvalidRecord,
          ReconcileSeverity::kError,
          source,
          "valid cash key",
          key,
          "",
          "cash balance has missing account or currency",
      });
      continue;
    }

    if (!std::isfinite(balance.amount)) {
      ++summary->invalid_records;
      summary->breaks.push_back(ReconcileBreak{
          ReconcileBreakType::kInvalidRecord,
          ReconcileSeverity::kError,
          key,
          "finite amount",
          "non-finite amount",
          "",
          "cash balance contains non-finite amount",
      });
      continue;
    }

    if (index.find(key) != index.end()) {
      ++summary->duplicate_records;
      summary->breaks.push_back(ReconcileBreak{
          ReconcileBreakType::kDuplicateRecord,
          ReconcileSeverity::kError,
          key,
          "unique cash balance",
          "duplicate cash balance",
          "",
          "duplicate cash balance record",
      });
      continue;
    }

    index[key] = balance;
  }

  return index;
}

std::string NormalizeAccountId(const std::string& value) {
  return Upper(Trim(value));
}

std::string NormalizeSymbol(const std::string& value) {
  std::string out = Upper(Trim(value));
  std::replace(out.begin(), out.end(), '/', '-');
  return out;
}

std::string NormalizeVenue(const std::string& value) {
  return Upper(Trim(value));
}

std::string NormalizeCurrency(const std::string& value) {
  const std::string normalized = Upper(Trim(value));
  return normalized.empty() ? "USD" : normalized;
}

std::string SeverityName(ReconcileSeverity severity) {
  switch (severity) {
    case ReconcileSeverity::kInfo:
      return "info";
    case ReconcileSeverity::kWarning:
      return "warning";
    case ReconcileSeverity::kError:
      return "error";
    case ReconcileSeverity::kCritical:
      return "critical";
  }

  return "unknown";
}

std::string BreakTypeName(ReconcileBreakType type) {
  switch (type) {
    case ReconcileBreakType::kMissingPosition:
      return "missing_position";
    case ReconcileBreakType::kExtraPosition:
      return "extra_position";
    case ReconcileBreakType::kQuantityMismatch:
      return "quantity_mismatch";
    case ReconcileBreakType::kCashMismatch:
      return "cash_mismatch";
    case ReconcileBreakType::kPriceMismatch:
      return "price_mismatch";
    case ReconcileBreakType::kDuplicateRecord:
      return "duplicate_record";
    case ReconcileBreakType::kInvalidRecord:
      return "invalid_record";
  }

  return "unknown";
}

}  // namespace marketdata
