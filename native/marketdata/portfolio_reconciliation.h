#pragma once

#include <cstdint>
#include <map>
#include <string>
#include <vector>

namespace marketdata {

enum class ReconcileSeverity {
  kInfo,
  kWarning,
  kError,
  kCritical,
};

enum class ReconcileBreakType {
  kMissingPosition,
  kExtraPosition,
  kQuantityMismatch,
  kCashMismatch,
  kPriceMismatch,
  kDuplicateRecord,
  kInvalidRecord,
};

struct PositionKey {
  std::string account_id;
  std::string symbol;
  std::string venue;

  bool operator<(const PositionKey& other) const;
  std::string ToString() const;
};

struct PositionRecord {
  std::string account_id;
  std::string symbol;
  std::string venue;
  std::string currency;
  double quantity = 0.0;
  double market_price = 0.0;

  PositionKey Key() const;
  double MarketValue() const;
};

struct CashBalanceRecord {
  std::string account_id;
  std::string currency;
  double amount = 0.0;

  std::string Key() const;
};

struct ExecutionFillRecord {
  std::string execution_id;
  std::string account_id;
  std::string symbol;
  std::string side;
  std::string venue;
  std::string currency;
  double quantity = 0.0;
  double price = 0.0;
  double fees = 0.0;

  double SignedQuantity() const;
  double CashEffect() const;
};

struct ReconcileBreak {
  ReconcileBreakType type = ReconcileBreakType::kInvalidRecord;
  ReconcileSeverity severity = ReconcileSeverity::kError;
  std::string key;
  std::string expected;
  std::string actual;
  std::string difference;
  std::string message;
};

struct ReconcileSummary {
  std::vector<ReconcileBreak> breaks;
  std::uint64_t checked_positions = 0;
  std::uint64_t checked_cash_balances = 0;
  std::uint64_t duplicate_records = 0;
  std::uint64_t invalid_records = 0;

  bool Ok() const;
  std::uint64_t ErrorCount() const;
  std::uint64_t WarningCount() const;
};

class PortfolioReconciler {
 public:
  PortfolioReconciler();

  void SetQuantityTolerance(double value);
  void SetCashTolerance(double value);
  void SetPriceTolerance(double value);

  ReconcileSummary ReconcilePositions(
      const std::vector<PositionRecord>& expected,
      const std::vector<PositionRecord>& broker) const;

  ReconcileSummary ReconcileCash(
      const std::vector<CashBalanceRecord>& expected,
      const std::vector<CashBalanceRecord>& broker) const;

  std::vector<PositionRecord> ExpectedPositionsFromFills(
      const std::vector<PositionRecord>& opening_positions,
      const std::vector<ExecutionFillRecord>& fills) const;

  std::vector<CashBalanceRecord> CashFromFills(
      const std::vector<CashBalanceRecord>& opening_cash,
      const std::vector<ExecutionFillRecord>& fills) const;

  ReconcileSummary MergeSummaries(
      const std::vector<ReconcileSummary>& summaries) const;

 private:
  double quantity_tolerance_;
  double cash_tolerance_;
  double price_tolerance_;

  std::map<PositionKey, PositionRecord> IndexPositions(
      const std::vector<PositionRecord>& positions,
      const std::string& source,
      ReconcileSummary* summary) const;

  std::map<std::string, CashBalanceRecord> IndexCash(
      const std::vector<CashBalanceRecord>& balances,
      const std::string& source,
      ReconcileSummary* summary) const;
};

std::string NormalizeAccountId(const std::string& value);
std::string NormalizeSymbol(const std::string& value);
std::string NormalizeVenue(const std::string& value);
std::string NormalizeCurrency(const std::string& value);

std::string SeverityName(ReconcileSeverity severity);
std::string BreakTypeName(ReconcileBreakType type);

}  // namespace marketdata
