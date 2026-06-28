#pragma once

#include <cstdint>
#include <map>
#include <string>
#include <vector>

namespace marketdata {

enum class AllocationIssueSeverity {
  kInfo,
  kWarning,
  kError,
  kCritical,
};

enum class AllocationIssueType {
  kInvalidRow,
  kDuplicateTarget,
  kWeightSumMismatch,
  kTargetWeightOutOfRange,
  kQuantityOutOfRange,
  kLotSizeViolation,
  kRestrictedSymbol,
  kLiquidityLimitExceeded,
  kRiskLimitExceeded,
  kDriftExceeded,
  kTradeTooSmall,
  kTradeTooLarge,
  kCashShortfall,
};

struct AllocationTargetKey {
  std::string portfolio_id;
  std::string account_id;
  std::string symbol;
  std::string strategy;
  std::string venue;

  bool operator<(const AllocationTargetKey& other) const;
  std::string ToString() const;
};

struct AllocationTargetRow {
  std::string portfolio_id;
  std::string account_id;
  std::string symbol;
  std::string strategy;
  std::string venue;
  std::string currency;

  double target_weight = 0.0;
  double current_weight = 0.0;
  double target_quantity = 0.0;
  double current_quantity = 0.0;
  double price = 0.0;
  double min_trade_quantity = 0.0;
  double max_trade_quantity = 0.0;
  double max_position_quantity = 0.0;
  double lot_size = 1.0;
  double liquidity_score = 1.0;
  double risk_score = 0.0;
  double available_cash = 0.0;

  bool restricted = false;

  AllocationTargetKey Key() const;
  double Drift() const;
  double TradeQuantity() const;
  double TradeNotional() const;
  double TargetNotional() const;
};

struct AllocationDiagnosticIssue {
  AllocationIssueType type = AllocationIssueType::kInvalidRow;
  AllocationIssueSeverity severity = AllocationIssueSeverity::kError;
  std::string key;
  std::string message;
  std::string expected;
  std::string actual;
};

struct PortfolioAllocationSummary {
  std::string portfolio_id;
  std::uint64_t target_count = 0;
  double total_target_weight = 0.0;
  double total_current_weight = 0.0;
  double gross_trade_notional = 0.0;
  double net_trade_notional = 0.0;
  double max_abs_drift = 0.0;
  double weighted_risk_score = 0.0;
};

struct AllocationDiagnosticReport {
  std::vector<AllocationDiagnosticIssue> issues;
  std::vector<PortfolioAllocationSummary> portfolios;

  std::uint64_t checked_rows = 0;
  std::uint64_t duplicate_rows = 0;
  std::uint64_t invalid_rows = 0;

  bool Ok() const;
  std::uint64_t ErrorCount() const;
  std::uint64_t WarningCount() const;
};

class AllocationDiagnostics {
 public:
  AllocationDiagnostics();

  void SetWeightTolerance(double value);
  void SetDriftTolerance(double value);
  void SetMaxRiskScore(double value);
  void SetMinLiquidityScore(double value);
  void SetCashTolerance(double value);
  void SetAllowShortSales(bool value);
  void SetAllowRestrictedSymbols(bool value);

  AllocationDiagnosticReport Analyze(
      const std::vector<AllocationTargetRow>& rows) const;

 private:
  double weight_tolerance_;
  double drift_tolerance_;
  double max_risk_score_;
  double min_liquidity_score_;
  double cash_tolerance_;
  bool allow_short_sales_;
  bool allow_restricted_symbols_;

  bool ValidateRow(
      const AllocationTargetRow& row,
      AllocationDiagnosticReport* report) const;

  void CheckPortfolioWeightTotals(
      const std::map<std::string, std::vector<AllocationTargetRow>>& by_portfolio,
      AllocationDiagnosticReport* report) const;

  void CheckTradeSizing(
      const AllocationTargetRow& row,
      AllocationDiagnosticReport* report) const;

  void CheckRiskAndLiquidity(
      const AllocationTargetRow& row,
      AllocationDiagnosticReport* report) const;

  void AddIssue(
      AllocationIssueType type,
      AllocationIssueSeverity severity,
      const std::string& key,
      const std::string& message,
      const std::string& expected,
      const std::string& actual,
      AllocationDiagnosticReport* report) const;
};

std::string NormalizeAllocationText(const std::string& value);
std::string AllocationSeverityName(AllocationIssueSeverity severity);
std::string AllocationIssueTypeName(AllocationIssueType type);

}  // namespace marketdata
