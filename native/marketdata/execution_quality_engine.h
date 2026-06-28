
#pragma once
#ifndef MARKETDATA_EXECUTION_QUALITY_ENGINE_H_
#define MARKETDATA_EXECUTION_QUALITY_ENGINE_H_

#include <cstdint>
#include <map>
#include <string>
#include <vector>

namespace marketdata {

enum class ExecutionQualitySeverity {
  kInfo,
  kWarning,
  kError,
  kCritical,
};

enum class ExecutionQualityIssueType {
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

struct ExecutionQualityInputRow {
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

struct ExecutionQualityIssue {
  ExecutionQualityIssueType type = ExecutionQualityIssueType::kInvalidInput;
  ExecutionQualitySeverity severity = ExecutionQualitySeverity::kError;
  std::string key;
  std::string rule;
  std::string message;
  std::string expected;
  std::string actual;
};

struct ExecutionQualityReport {
  std::vector<ExecutionQualityIssue> issues;
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

class ExecutionQualityEngine {
 public:
  ExecutionQualityEngine();

  void SetThreshold(const std::string& rule, double value);
  void SetStrictMode(bool value);
  void SetAllowManualOverrides(bool value);

  ExecutionQualityReport Analyze(const std::vector<ExecutionQualityInputRow>& rows) const;

 private:
  std::map<std::string, double> thresholds_;
  bool strict_mode_;
  bool allow_manual_overrides_;

  double Threshold(const std::string& rule, double fallback) const;
  bool ValidateCommonFields(const ExecutionQualityInputRow& row, ExecutionQualityReport* report) const;
  void AddIssue(ExecutionQualityIssueType type,
                ExecutionQualitySeverity severity,
                const ExecutionQualityInputRow& row,
                const std::string& rule,
                const std::string& message,
                const std::string& expected,
                const std::string& actual,
                ExecutionQualityReport* report) const;

  bool CheckArrivalSlippageCap(const ExecutionQualityInputRow& row, ExecutionQualityReport* report) const;
  bool CheckVWAPSlippageCap(const ExecutionQualityInputRow& row, ExecutionQualityReport* report) const;
  bool CheckTWAPSlippageCap(const ExecutionQualityInputRow& row, ExecutionQualityReport* report) const;
  bool CheckParticipationRateCap(const ExecutionQualityInputRow& row, ExecutionQualityReport* report) const;
  bool CheckRejectRateCap(const ExecutionQualityInputRow& row, ExecutionQualityReport* report) const;
  bool CheckCancelRateCap(const ExecutionQualityInputRow& row, ExecutionQualityReport* report) const;
  bool CheckLatencyCap(const ExecutionQualityInputRow& row, ExecutionQualityReport* report) const;
  bool CheckQueueDelayCap(const ExecutionQualityInputRow& row, ExecutionQualityReport* report) const;
  bool CheckFillRateFloor(const ExecutionQualityInputRow& row, ExecutionQualityReport* report) const;
  bool CheckPartialFillCap(const ExecutionQualityInputRow& row, ExecutionQualityReport* report) const;
  bool CheckVenueConcentrationCap(const ExecutionQualityInputRow& row, ExecutionQualityReport* report) const;
  bool CheckCrossVenueDriftCap(const ExecutionQualityInputRow& row, ExecutionQualityReport* report) const;
  bool CheckSpreadCaptureFloor(const ExecutionQualityInputRow& row, ExecutionQualityReport* report) const;
  bool CheckAdverseSelectionCap(const ExecutionQualityInputRow& row, ExecutionQualityReport* report) const;
  bool CheckMarketImpactCap(const ExecutionQualityInputRow& row, ExecutionQualityReport* report) const;
  bool CheckPriceImprovementFloor(const ExecutionQualityInputRow& row, ExecutionQualityReport* report) const;
  bool CheckOddLotCap(const ExecutionQualityInputRow& row, ExecutionQualityReport* report) const;
  bool CheckRoundLotFloor(const ExecutionQualityInputRow& row, ExecutionQualityReport* report) const;
  bool CheckDarkPoolFillCap(const ExecutionQualityInputRow& row, ExecutionQualityReport* report) const;
  bool CheckAuctionFillCap(const ExecutionQualityInputRow& row, ExecutionQualityReport* report) const;
  bool CheckRoutingLoopCap(const ExecutionQualityInputRow& row, ExecutionQualityReport* report) const;
  bool CheckStaleQuoteCap(const ExecutionQualityInputRow& row, ExecutionQualityReport* report) const;
  bool CheckLockedMarketCap(const ExecutionQualityInputRow& row, ExecutionQualityReport* report) const;
  bool CheckCrossedMarketCap(const ExecutionQualityInputRow& row, ExecutionQualityReport* report) const;
  bool CheckSelfTradePrevention(const ExecutionQualityInputRow& row, ExecutionQualityReport* report) const;
  bool CheckDuplicateExecutionCap(const ExecutionQualityInputRow& row, ExecutionQualityReport* report) const;
  bool CheckOutOfOrderExecutionCap(const ExecutionQualityInputRow& row, ExecutionQualityReport* report) const;
  bool CheckLateReportCap(const ExecutionQualityInputRow& row, ExecutionQualityReport* report) const;
  bool CheckExecutionFeeCap(const ExecutionQualityInputRow& row, ExecutionQualityReport* report) const;
  bool CheckLiquidityTakingCap(const ExecutionQualityInputRow& row, ExecutionQualityReport* report) const;
  bool CheckLiquidityProvidingFloor(const ExecutionQualityInputRow& row, ExecutionQualityReport* report) const;
  bool CheckPostOnlyRejectCap(const ExecutionQualityInputRow& row, ExecutionQualityReport* report) const;
  bool CheckMinimumQuantityRejectCap(const ExecutionQualityInputRow& row, ExecutionQualityReport* report) const;
  bool CheckPeggedOrderDriftCap(const ExecutionQualityInputRow& row, ExecutionQualityReport* report) const;
  bool CheckMidpointDriftCap(const ExecutionQualityInputRow& row, ExecutionQualityReport* report) const;
  bool CheckChildOrderBurstCap(const ExecutionQualityInputRow& row, ExecutionQualityReport* report) const;
  bool CheckParentOrderDriftCap(const ExecutionQualityInputRow& row, ExecutionQualityReport* report) const;
  bool CheckResidualQuantityCap(const ExecutionQualityInputRow& row, ExecutionQualityReport* report) const;
  bool CheckTradeThroughCap(const ExecutionQualityInputRow& row, ExecutionQualityReport* report) const;
  bool CheckVenueOutageCap(const ExecutionQualityInputRow& row, ExecutionQualityReport* report) const;
};

std::string ExecutionQualitySeverityName(ExecutionQualitySeverity severity);
std::string ExecutionQualityIssueTypeName(ExecutionQualityIssueType type);

}  // namespace marketdata

#endif
