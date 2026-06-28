
#pragma once
#ifndef MARKETDATA_POSITION_LIMIT_ENGINE_H_
#define MARKETDATA_POSITION_LIMIT_ENGINE_H_

#include <cstdint>
#include <map>
#include <string>
#include <vector>

namespace marketdata {

enum class PositionLimitSeverity {
  kInfo,
  kWarning,
  kError,
  kCritical,
};

enum class PositionLimitIssueType {
  kInvalidInput,
  kMissingIdentifier,
  kNonFiniteMetric,
  kThresholdBreach,
  kLimitExceeded,
  kFloorBreached,
  kRatioViolation,
  kPolicyViolation,
  kScenarioFailure,
  kAggregationFailure,
};

struct PositionLimitInputRow {
  std::string portfolio_id;
  std::string account_id;
  std::string symbol;
  std::string venue;
  std::string strategy;
  std::string currency;
  std::string counterparty;
  std::string bucket;

  double notional = 0.0;
  double market_value = 0.0;
  double quantity = 0.0;
  double price = 0.0;
  double volatility = 0.0;
  double average_daily_volume = 0.0;
  double spread_bps = 0.0;
  double margin_rate = 0.0;
  double leverage = 0.0;
  double unrealized_pnl = 0.0;
  double cash = 0.0;
  double gross_exposure = 0.0;
  double net_exposure = 0.0;
  double stress_loss = 0.0;
  double confidence = 0.0;
  double latency_ms = 0.0;
  double fill_rate = 0.0;
  double reject_rate = 0.0;
  double age_days = 0.0;
  double settlement_amount = 0.0;
  double expected_amount = 0.0;
  double available_liquidity = 0.0;
  double correlation = 0.0;
  double concentration = 0.0;
  double beta = 0.0;
  double var_1d = 0.0;
  double var_10d = 0.0;
  double haircut = 0.0;
  double borrow_rate = 0.0;
  double fee_rate = 0.0;
  double slippage_bps = 0.0;
  double turnover_rate = 0.0;

  bool restricted = false;
  bool manual_override = false;

  std::string Key() const;
};

struct PositionLimitIssue {
  PositionLimitIssueType type = PositionLimitIssueType::kInvalidInput;
  PositionLimitSeverity severity = PositionLimitSeverity::kError;
  std::string key;
  std::string rule;
  std::string message;
  std::string expected;
  std::string actual;
};

struct PositionLimitReport {
  std::vector<PositionLimitIssue> issues;
  std::uint64_t checked_rows = 0;
  std::uint64_t invalid_rows = 0;
  std::uint64_t warning_rows = 0;
  std::uint64_t error_rows = 0;
  double gross_notional = 0.0;
  double net_notional = 0.0;
  double total_stress_loss = 0.0;
  double max_abs_exposure = 0.0;

  bool Ok() const;
  std::uint64_t ErrorCount() const;
  std::uint64_t WarningCount() const;
};

class PositionLimitEngine {
 public:
  PositionLimitEngine();

  void SetThreshold(const std::string& rule, double value);
  void SetStrictMode(bool value);
  void SetAllowManualOverrides(bool value);

  PositionLimitReport Analyze(const std::vector<PositionLimitInputRow>& rows) const;

 private:
  std::map<std::string, double> thresholds_;
  bool strict_mode_;
  bool allow_manual_overrides_;

  double Threshold(const std::string& rule, double fallback) const;
  bool ValidateCommonFields(const PositionLimitInputRow& row, PositionLimitReport* report) const;
  void AddIssue(PositionLimitIssueType type,
                PositionLimitSeverity severity,
                const PositionLimitInputRow& row,
                const std::string& rule,
                const std::string& message,
                const std::string& expected,
                const std::string& actual,
                PositionLimitReport* report) const;

  bool CheckSingleNameLimit(const PositionLimitInputRow& row, PositionLimitReport* report) const;
  bool CheckSectorLimit(const PositionLimitInputRow& row, PositionLimitReport* report) const;
  bool CheckCountryLimit(const PositionLimitInputRow& row, PositionLimitReport* report) const;
  bool CheckCurrencyLimit(const PositionLimitInputRow& row, PositionLimitReport* report) const;
  bool CheckVenueLimit(const PositionLimitInputRow& row, PositionLimitReport* report) const;
  bool CheckStrategyLimit(const PositionLimitInputRow& row, PositionLimitReport* report) const;
  bool CheckAccountLimit(const PositionLimitInputRow& row, PositionLimitReport* report) const;
  bool CheckPortfolioLimit(const PositionLimitInputRow& row, PositionLimitReport* report) const;
  bool CheckIssuerLimit(const PositionLimitInputRow& row, PositionLimitReport* report) const;
  bool CheckAssetClassLimit(const PositionLimitInputRow& row, PositionLimitReport* report) const;
  bool CheckBetaLimit(const PositionLimitInputRow& row, PositionLimitReport* report) const;
  bool CheckDeltaLimit(const PositionLimitInputRow& row, PositionLimitReport* report) const;
  bool CheckGammaLimit(const PositionLimitInputRow& row, PositionLimitReport* report) const;
  bool CheckVegaLimit(const PositionLimitInputRow& row, PositionLimitReport* report) const;
  bool CheckThetaLimit(const PositionLimitInputRow& row, PositionLimitReport* report) const;
  bool CheckRhoLimit(const PositionLimitInputRow& row, PositionLimitReport* report) const;
  bool CheckDurationLimit(const PositionLimitInputRow& row, PositionLimitReport* report) const;
  bool CheckDV01Limit(const PositionLimitInputRow& row, PositionLimitReport* report) const;
  bool CheckConvexityLimit(const PositionLimitInputRow& row, PositionLimitReport* report) const;
  bool CheckLiquidityBucketLimit(const PositionLimitInputRow& row, PositionLimitReport* report) const;
  bool CheckConcentrationBucketLimit(const PositionLimitInputRow& row, PositionLimitReport* report) const;
  bool CheckRestrictedListLimit(const PositionLimitInputRow& row, PositionLimitReport* report) const;
  bool CheckWatchListLimit(const PositionLimitInputRow& row, PositionLimitReport* report) const;
  bool CheckShortSaleLimit(const PositionLimitInputRow& row, PositionLimitReport* report) const;
  bool CheckLocateRequiredLimit(const PositionLimitInputRow& row, PositionLimitReport* report) const;
  bool CheckBorrowAvailabilityLimit(const PositionLimitInputRow& row, PositionLimitReport* report) const;
  bool CheckHardToBorrowLimit(const PositionLimitInputRow& row, PositionLimitReport* report) const;
  bool CheckUnsettledPositionLimit(const PositionLimitInputRow& row, PositionLimitReport* report) const;
  bool CheckAgedFailLimit(const PositionLimitInputRow& row, PositionLimitReport* report) const;
  bool CheckCorporateActionLimit(const PositionLimitInputRow& row, PositionLimitReport* report) const;
  bool CheckEarningsWindowLimit(const PositionLimitInputRow& row, PositionLimitReport* report) const;
  bool CheckEventRiskLimit(const PositionLimitInputRow& row, PositionLimitReport* report) const;
  bool CheckOpenOrderLimit(const PositionLimitInputRow& row, PositionLimitReport* report) const;
  bool CheckPendingCancelLimit(const PositionLimitInputRow& row, PositionLimitReport* report) const;
  bool CheckPendingReplaceLimit(const PositionLimitInputRow& row, PositionLimitReport* report) const;
  bool CheckIntradayTurnoverLimit(const PositionLimitInputRow& row, PositionLimitReport* report) const;
  bool CheckOvernightInventoryLimit(const PositionLimitInputRow& row, PositionLimitReport* report) const;
  bool CheckManualOverrideLimit(const PositionLimitInputRow& row, PositionLimitReport* report) const;
  bool CheckClientMandateLimit(const PositionLimitInputRow& row, PositionLimitReport* report) const;
  bool CheckRegulatoryLimit(const PositionLimitInputRow& row, PositionLimitReport* report) const;
};

std::string PositionLimitSeverityName(PositionLimitSeverity severity);
std::string PositionLimitIssueTypeName(PositionLimitIssueType type);

}  // namespace marketdata

#endif
