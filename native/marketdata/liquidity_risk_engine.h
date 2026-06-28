
#pragma once
#ifndef MARKETDATA_LIQUIDITY_RISK_ENGINE_H_
#define MARKETDATA_LIQUIDITY_RISK_ENGINE_H_

#include <cstdint>
#include <map>
#include <string>
#include <vector>

namespace marketdata {

enum class LiquidityRiskSeverity {
  kInfo,
  kWarning,
  kError,
  kCritical,
};

enum class LiquidityRiskIssueType {
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

struct LiquidityRiskInputRow {
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

struct LiquidityRiskIssue {
  LiquidityRiskIssueType type = LiquidityRiskIssueType::kInvalidInput;
  LiquidityRiskSeverity severity = LiquidityRiskSeverity::kError;
  std::string key;
  std::string rule;
  std::string message;
  std::string expected;
  std::string actual;
};

struct LiquidityRiskReport {
  std::vector<LiquidityRiskIssue> issues;
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

class LiquidityRiskEngine {
 public:
  LiquidityRiskEngine();

  void SetThreshold(const std::string& rule, double value);
  void SetStrictMode(bool value);
  void SetAllowManualOverrides(bool value);

  LiquidityRiskReport Analyze(const std::vector<LiquidityRiskInputRow>& rows) const;

 private:
  std::map<std::string, double> thresholds_;
  bool strict_mode_;
  bool allow_manual_overrides_;

  double Threshold(const std::string& rule, double fallback) const;
  bool ValidateCommonFields(const LiquidityRiskInputRow& row, LiquidityRiskReport* report) const;
  void AddIssue(LiquidityRiskIssueType type,
                LiquidityRiskSeverity severity,
                const LiquidityRiskInputRow& row,
                const std::string& rule,
                const std::string& message,
                const std::string& expected,
                const std::string& actual,
                LiquidityRiskReport* report) const;

  bool CheckADVParticipationCap(const LiquidityRiskInputRow& row, LiquidityRiskReport* report) const;
  bool CheckSpreadBpsCap(const LiquidityRiskInputRow& row, LiquidityRiskReport* report) const;
  bool CheckDepthFloor(const LiquidityRiskInputRow& row, LiquidityRiskReport* report) const;
  bool CheckOrderBookImbalanceCap(const LiquidityRiskInputRow& row, LiquidityRiskReport* report) const;
  bool CheckAuctionDependencyCap(const LiquidityRiskInputRow& row, LiquidityRiskReport* report) const;
  bool CheckDarkLiquidityCap(const LiquidityRiskInputRow& row, LiquidityRiskReport* report) const;
  bool CheckDisplayedLiquidityFloor(const LiquidityRiskInputRow& row, LiquidityRiskReport* report) const;
  bool CheckHiddenLiquidityDecay(const LiquidityRiskInputRow& row, LiquidityRiskReport* report) const;
  bool CheckVolumeCollapseCap(const LiquidityRiskInputRow& row, LiquidityRiskReport* report) const;
  bool CheckTurnoverFloor(const LiquidityRiskInputRow& row, LiquidityRiskReport* report) const;
  bool CheckDaysToLiquidateCap(const LiquidityRiskInputRow& row, LiquidityRiskReport* report) const;
  bool CheckLiquidationCostCap(const LiquidityRiskInputRow& row, LiquidityRiskReport* report) const;
  bool CheckSlippageStressCap(const LiquidityRiskInputRow& row, LiquidityRiskReport* report) const;
  bool CheckLiquidityScoreFloor(const LiquidityRiskInputRow& row, LiquidityRiskReport* report) const;
  bool CheckVenueFragmentationCap(const LiquidityRiskInputRow& row, LiquidityRiskReport* report) const;
  bool CheckMarketMakerWithdrawalCap(const LiquidityRiskInputRow& row, LiquidityRiskReport* report) const;
  bool CheckHaltProbabilityCap(const LiquidityRiskInputRow& row, LiquidityRiskReport* report) const;
  bool CheckGapRiskCap(const LiquidityRiskInputRow& row, LiquidityRiskReport* report) const;
  bool CheckBorrowLiquidityFloor(const LiquidityRiskInputRow& row, LiquidityRiskReport* report) const;
  bool CheckFundingLiquidityFloor(const LiquidityRiskInputRow& row, LiquidityRiskReport* report) const;
  bool CheckCollateralLiquidityFloor(const LiquidityRiskInputRow& row, LiquidityRiskReport* report) const;
  bool CheckRedemptionPressureCap(const LiquidityRiskInputRow& row, LiquidityRiskReport* report) const;
  bool CheckCashBufferFloor(const LiquidityRiskInputRow& row, LiquidityRiskReport* report) const;
  bool CheckSettlementLiquidityCap(const LiquidityRiskInputRow& row, LiquidityRiskReport* report) const;
  bool CheckIntradayLiquidityCap(const LiquidityRiskInputRow& row, LiquidityRiskReport* report) const;
  bool CheckOvernightLiquidityCap(const LiquidityRiskInputRow& row, LiquidityRiskReport* report) const;
  bool CheckCrossCurrencyFundingCap(const LiquidityRiskInputRow& row, LiquidityRiskReport* report) const;
  bool CheckRepoFundingCap(const LiquidityRiskInputRow& row, LiquidityRiskReport* report) const;
  bool CheckFXSwapFundingCap(const LiquidityRiskInputRow& row, LiquidityRiskReport* report) const;
  bool CheckConcentrationLiquidityCap(const LiquidityRiskInputRow& row, LiquidityRiskReport* report) const;
  bool CheckETFCreateRedeemCap(const LiquidityRiskInputRow& row, LiquidityRiskReport* report) const;
  bool CheckUnderlyingBasketLiquidityCap(const LiquidityRiskInputRow& row, LiquidityRiskReport* report) const;
  bool CheckIndexRebalanceLiquidityCap(const LiquidityRiskInputRow& row, LiquidityRiskReport* report) const;
  bool CheckEventLiquidityCap(const LiquidityRiskInputRow& row, LiquidityRiskReport* report) const;
  bool CheckNewsShockLiquidityCap(const LiquidityRiskInputRow& row, LiquidityRiskReport* report) const;
  bool CheckLimitOrderBookThinnessCap(const LiquidityRiskInputRow& row, LiquidityRiskReport* report) const;
  bool CheckQueuePositionDecayCap(const LiquidityRiskInputRow& row, LiquidityRiskReport* report) const;
  bool CheckVenueOutageLiquidityCap(const LiquidityRiskInputRow& row, LiquidityRiskReport* report) const;
  bool CheckQuoteFadeCap(const LiquidityRiskInputRow& row, LiquidityRiskReport* report) const;
  bool CheckTradeSizeBucketCap(const LiquidityRiskInputRow& row, LiquidityRiskReport* report) const;
};

std::string LiquidityRiskSeverityName(LiquidityRiskSeverity severity);
std::string LiquidityRiskIssueTypeName(LiquidityRiskIssueType type);

}  // namespace marketdata

#endif
