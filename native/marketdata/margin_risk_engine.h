
#pragma once
#ifndef MARKETDATA_MARGIN_RISK_ENGINE_H_
#define MARKETDATA_MARGIN_RISK_ENGINE_H_

#include <cstdint>
#include <map>
#include <string>
#include <vector>

namespace marketdata {

enum class MarginRiskSeverity {
  kInfo,
  kWarning,
  kError,
  kCritical,
};

enum class MarginRiskIssueType {
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

struct MarginRiskInputRow {
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

struct MarginRiskIssue {
  MarginRiskIssueType type = MarginRiskIssueType::kInvalidInput;
  MarginRiskSeverity severity = MarginRiskSeverity::kError;
  std::string key;
  std::string rule;
  std::string message;
  std::string expected;
  std::string actual;
};

struct MarginRiskReport {
  std::vector<MarginRiskIssue> issues;
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

class MarginRiskEngine {
 public:
  MarginRiskEngine();

  void SetThreshold(const std::string& rule, double value);
  void SetStrictMode(bool value);
  void SetAllowManualOverrides(bool value);

  MarginRiskReport Analyze(const std::vector<MarginRiskInputRow>& rows) const;

 private:
  std::map<std::string, double> thresholds_;
  bool strict_mode_;
  bool allow_manual_overrides_;

  double Threshold(const std::string& rule, double fallback) const;
  bool ValidateCommonFields(const MarginRiskInputRow& row, MarginRiskReport* report) const;
  void AddIssue(MarginRiskIssueType type,
                MarginRiskSeverity severity,
                const MarginRiskInputRow& row,
                const std::string& rule,
                const std::string& message,
                const std::string& expected,
                const std::string& actual,
                MarginRiskReport* report) const;

  bool CheckInitialMarginFloor(const MarginRiskInputRow& row, MarginRiskReport* report) const;
  bool CheckMaintenanceMarginFloor(const MarginRiskInputRow& row, MarginRiskReport* report) const;
  bool CheckConcentrationCap(const MarginRiskInputRow& row, MarginRiskReport* report) const;
  bool CheckLeverageCap(const MarginRiskInputRow& row, MarginRiskReport* report) const;
  bool CheckBorrowRateCap(const MarginRiskInputRow& row, MarginRiskReport* report) const;
  bool CheckHaircutFloor(const MarginRiskInputRow& row, MarginRiskReport* report) const;
  bool CheckVolatilityCap(const MarginRiskInputRow& row, MarginRiskReport* report) const;
  bool CheckStressLossCap(const MarginRiskInputRow& row, MarginRiskReport* report) const;
  bool CheckCashCoverageFloor(const MarginRiskInputRow& row, MarginRiskReport* report) const;
  bool CheckExposureCap(const MarginRiskInputRow& row, MarginRiskReport* report) const;
  bool CheckNetExposureBand(const MarginRiskInputRow& row, MarginRiskReport* report) const;
  bool CheckGrossExposureCap(const MarginRiskInputRow& row, MarginRiskReport* report) const;
  bool CheckPortfolioBetaCap(const MarginRiskInputRow& row, MarginRiskReport* report) const;
  bool CheckCorrelationClusterCap(const MarginRiskInputRow& row, MarginRiskReport* report) const;
  bool CheckShortInventoryCap(const MarginRiskInputRow& row, MarginRiskReport* report) const;
  bool CheckOptionPremiumCap(const MarginRiskInputRow& row, MarginRiskReport* report) const;
  bool CheckFutureSpanMarginCap(const MarginRiskInputRow& row, MarginRiskReport* report) const;
  bool CheckCrossMarginOffsetCap(const MarginRiskInputRow& row, MarginRiskReport* report) const;
  bool CheckCurrencyHaircutFloor(const MarginRiskInputRow& row, MarginRiskReport* report) const;
  bool CheckLiquidationBufferFloor(const MarginRiskInputRow& row, MarginRiskReport* report) const;
  bool CheckIntradayMarginCap(const MarginRiskInputRow& row, MarginRiskReport* report) const;
  bool CheckOvernightMarginCap(const MarginRiskInputRow& row, MarginRiskReport* report) const;
  bool CheckHouseMarginAddOnCap(const MarginRiskInputRow& row, MarginRiskReport* report) const;
  bool CheckRegulatoryMarginFloor(const MarginRiskInputRow& row, MarginRiskReport* report) const;
  bool CheckRiskWeightCap(const MarginRiskInputRow& row, MarginRiskReport* report) const;
  bool CheckDeltaAdjustedExposureCap(const MarginRiskInputRow& row, MarginRiskReport* report) const;
  bool CheckGammaNotionalCap(const MarginRiskInputRow& row, MarginRiskReport* report) const;
  bool CheckVegaNotionalCap(const MarginRiskInputRow& row, MarginRiskReport* report) const;
  bool CheckThetaDecayCap(const MarginRiskInputRow& row, MarginRiskReport* report) const;
  bool CheckStressLiquidityCap(const MarginRiskInputRow& row, MarginRiskReport* report) const;
  bool CheckCollateralUtilizationCap(const MarginRiskInputRow& row, MarginRiskReport* report) const;
  bool CheckFreeEquityFloor(const MarginRiskInputRow& row, MarginRiskReport* report) const;
  bool CheckDrawdownCap(const MarginRiskInputRow& row, MarginRiskReport* report) const;
  bool CheckSingleIssuerCap(const MarginRiskInputRow& row, MarginRiskReport* report) const;
  bool CheckSectorExposureCap(const MarginRiskInputRow& row, MarginRiskReport* report) const;
  bool CheckCountryExposureCap(const MarginRiskInputRow& row, MarginRiskReport* report) const;
  bool CheckRestrictedAssetCap(const MarginRiskInputRow& row, MarginRiskReport* report) const;
  bool CheckUnsettledCashCap(const MarginRiskInputRow& row, MarginRiskReport* report) const;
  bool CheckFailToDeliverCap(const MarginRiskInputRow& row, MarginRiskReport* report) const;
  bool CheckCorporateActionRiskCap(const MarginRiskInputRow& row, MarginRiskReport* report) const;
};

std::string MarginRiskSeverityName(MarginRiskSeverity severity);
std::string MarginRiskIssueTypeName(MarginRiskIssueType type);

}  // namespace marketdata

#endif
