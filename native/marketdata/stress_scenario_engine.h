
#pragma once
#ifndef MARKETDATA_STRESS_SCENARIO_ENGINE_H_
#define MARKETDATA_STRESS_SCENARIO_ENGINE_H_

#include <cstdint>
#include <map>
#include <string>
#include <vector>

namespace marketdata {

enum class StressScenarioSeverity {
  kInfo,
  kWarning,
  kError,
  kCritical,
};

enum class StressScenarioIssueType {
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

struct StressScenarioInputRow {
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

struct StressScenarioIssue {
  StressScenarioIssueType type = StressScenarioIssueType::kInvalidInput;
  StressScenarioSeverity severity = StressScenarioSeverity::kError;
  std::string key;
  std::string rule;
  std::string message;
  std::string expected;
  std::string actual;
};

struct StressScenarioReport {
  std::vector<StressScenarioIssue> issues;
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

class StressScenarioEngine {
 public:
  StressScenarioEngine();

  void SetThreshold(const std::string& rule, double value);
  void SetStrictMode(bool value);
  void SetAllowManualOverrides(bool value);

  StressScenarioReport Analyze(const std::vector<StressScenarioInputRow>& rows) const;

 private:
  std::map<std::string, double> thresholds_;
  bool strict_mode_;
  bool allow_manual_overrides_;

  double Threshold(const std::string& rule, double fallback) const;
  bool ValidateCommonFields(const StressScenarioInputRow& row, StressScenarioReport* report) const;
  void AddIssue(StressScenarioIssueType type,
                StressScenarioSeverity severity,
                const StressScenarioInputRow& row,
                const std::string& rule,
                const std::string& message,
                const std::string& expected,
                const std::string& actual,
                StressScenarioReport* report) const;

  bool CheckEquityShockDown(const StressScenarioInputRow& row, StressScenarioReport* report) const;
  bool CheckEquityShockUp(const StressScenarioInputRow& row, StressScenarioReport* report) const;
  bool CheckRateShockParallel(const StressScenarioInputRow& row, StressScenarioReport* report) const;
  bool CheckRateShockSteepener(const StressScenarioInputRow& row, StressScenarioReport* report) const;
  bool CheckRateShockFlattener(const StressScenarioInputRow& row, StressScenarioReport* report) const;
  bool CheckCreditSpreadShock(const StressScenarioInputRow& row, StressScenarioReport* report) const;
  bool CheckFxDevaluationShock(const StressScenarioInputRow& row, StressScenarioReport* report) const;
  bool CheckCommodityShock(const StressScenarioInputRow& row, StressScenarioReport* report) const;
  bool CheckVolatilitySpike(const StressScenarioInputRow& row, StressScenarioReport* report) const;
  bool CheckLiquidityFreeze(const StressScenarioInputRow& row, StressScenarioReport* report) const;
  bool CheckCorrelationBreakdown(const StressScenarioInputRow& row, StressScenarioReport* report) const;
  bool CheckGapOpenLoss(const StressScenarioInputRow& row, StressScenarioReport* report) const;
  bool CheckLimitDownScenario(const StressScenarioInputRow& row, StressScenarioReport* report) const;
  bool CheckLimitUpScenario(const StressScenarioInputRow& row, StressScenarioReport* report) const;
  bool CheckShortSqueezeScenario(const StressScenarioInputRow& row, StressScenarioReport* report) const;
  bool CheckBorrowRecallScenario(const StressScenarioInputRow& row, StressScenarioReport* report) const;
  bool CheckSettlementDelayScenario(const StressScenarioInputRow& row, StressScenarioReport* report) const;
  bool CheckMarginCallScenario(const StressScenarioInputRow& row, StressScenarioReport* report) const;
  bool CheckCashRunoffScenario(const StressScenarioInputRow& row, StressScenarioReport* report) const;
  bool CheckConcentrationUnwind(const StressScenarioInputRow& row, StressScenarioReport* report) const;
  bool CheckCrowdedTradeExit(const StressScenarioInputRow& row, StressScenarioReport* report) const;
  bool CheckIndexRebalanceShock(const StressScenarioInputRow& row, StressScenarioReport* report) const;
  bool CheckCorporateActionShock(const StressScenarioInputRow& row, StressScenarioReport* report) const;
  bool CheckDividendCutShock(const StressScenarioInputRow& row, StressScenarioReport* report) const;
  bool CheckEarningsGapScenario(const StressScenarioInputRow& row, StressScenarioReport* report) const;
  bool CheckMacroEventScenario(const StressScenarioInputRow& row, StressScenarioReport* report) const;
  bool CheckDefaultScenario(const StressScenarioInputRow& row, StressScenarioReport* report) const;
  bool CheckCounterpartyFailure(const StressScenarioInputRow& row, StressScenarioReport* report) const;
  bool CheckVenueHaltScenario(const StressScenarioInputRow& row, StressScenarioReport* report) const;
  bool CheckCircuitBreakerScenario(const StressScenarioInputRow& row, StressScenarioReport* report) const;
  bool CheckSlippageWidening(const StressScenarioInputRow& row, StressScenarioReport* report) const;
  bool CheckSpreadExplosion(const StressScenarioInputRow& row, StressScenarioReport* report) const;
  bool CheckVolumeCollapse(const StressScenarioInputRow& row, StressScenarioReport* report) const;
  bool CheckBetaExpansion(const StressScenarioInputRow& row, StressScenarioReport* report) const;
  bool CheckBasisRiskExpansion(const StressScenarioInputRow& row, StressScenarioReport* report) const;
  bool CheckCrossAssetContagion(const StressScenarioInputRow& row, StressScenarioReport* report) const;
  bool CheckVolSurfaceTwist(const StressScenarioInputRow& row, StressScenarioReport* report) const;
  bool CheckCurveConvexityShock(const StressScenarioInputRow& row, StressScenarioReport* report) const;
  bool CheckTailLossScenario(const StressScenarioInputRow& row, StressScenarioReport* report) const;
  bool CheckRecoveryDelayScenario(const StressScenarioInputRow& row, StressScenarioReport* report) const;
};

std::string StressScenarioSeverityName(StressScenarioSeverity severity);
std::string StressScenarioIssueTypeName(StressScenarioIssueType type);

}  // namespace marketdata

#endif
