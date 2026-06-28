
#pragma once
#ifndef MARKETDATA_SETTLEMENT_RISK_ENGINE_H_
#define MARKETDATA_SETTLEMENT_RISK_ENGINE_H_

#include <cstdint>
#include <map>
#include <string>
#include <vector>

namespace marketdata {

enum class SettlementRiskSeverity {
  kInfo,
  kWarning,
  kError,
  kCritical,
};

enum class SettlementRiskIssueType {
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

struct SettlementRiskInputRow {
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

struct SettlementRiskIssue {
  SettlementRiskIssueType type = SettlementRiskIssueType::kInvalidInput;
  SettlementRiskSeverity severity = SettlementRiskSeverity::kError;
  std::string key;
  std::string rule;
  std::string message;
  std::string expected;
  std::string actual;
};

struct SettlementRiskReport {
  std::vector<SettlementRiskIssue> issues;
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

class SettlementRiskEngine {
 public:
  SettlementRiskEngine();

  void SetThreshold(const std::string& rule, double value);
  void SetStrictMode(bool value);
  void SetAllowManualOverrides(bool value);

  SettlementRiskReport Analyze(const std::vector<SettlementRiskInputRow>& rows) const;

 private:
  std::map<std::string, double> thresholds_;
  bool strict_mode_;
  bool allow_manual_overrides_;

  double Threshold(const std::string& rule, double fallback) const;
  bool ValidateCommonFields(const SettlementRiskInputRow& row, SettlementRiskReport* report) const;
  void AddIssue(SettlementRiskIssueType type,
                SettlementRiskSeverity severity,
                const SettlementRiskInputRow& row,
                const std::string& rule,
                const std::string& message,
                const std::string& expected,
                const std::string& actual,
                SettlementRiskReport* report) const;

  bool CheckCashMismatchCap(const SettlementRiskInputRow& row, SettlementRiskReport* report) const;
  bool CheckSecurityMismatchCap(const SettlementRiskInputRow& row, SettlementRiskReport* report) const;
  bool CheckAgedFailCap(const SettlementRiskInputRow& row, SettlementRiskReport* report) const;
  bool CheckCounterpartyExposureCap(const SettlementRiskInputRow& row, SettlementRiskReport* report) const;
  bool CheckSettlementCurrencyCap(const SettlementRiskInputRow& row, SettlementRiskReport* report) const;
  bool CheckInstructionAgeCap(const SettlementRiskInputRow& row, SettlementRiskReport* report) const;
  bool CheckMissingSSIFlag(const SettlementRiskInputRow& row, SettlementRiskReport* report) const;
  bool CheckLateAffirmationCap(const SettlementRiskInputRow& row, SettlementRiskReport* report) const;
  bool CheckUnmatchedTradeCap(const SettlementRiskInputRow& row, SettlementRiskReport* report) const;
  bool CheckPartialSettlementCap(const SettlementRiskInputRow& row, SettlementRiskReport* report) const;
  bool CheckBuyInRiskCap(const SettlementRiskInputRow& row, SettlementRiskReport* report) const;
  bool CheckRecallRiskCap(const SettlementRiskInputRow& row, SettlementRiskReport* report) const;
  bool CheckCorporateActionConflict(const SettlementRiskInputRow& row, SettlementRiskReport* report) const;
  bool CheckDividendEntitlementMismatch(const SettlementRiskInputRow& row, SettlementRiskReport* report) const;
  bool CheckTaxWithholdingMismatch(const SettlementRiskInputRow& row, SettlementRiskReport* report) const;
  bool CheckFXFundingMismatch(const SettlementRiskInputRow& row, SettlementRiskReport* report) const;
  bool CheckCustodianBreakCap(const SettlementRiskInputRow& row, SettlementRiskReport* report) const;
  bool CheckBrokerBreakCap(const SettlementRiskInputRow& row, SettlementRiskReport* report) const;
  bool CheckClearingBreakCap(const SettlementRiskInputRow& row, SettlementRiskReport* report) const;
  bool CheckDepotBreakCap(const SettlementRiskInputRow& row, SettlementRiskReport* report) const;
  bool CheckExternalReferenceMissing(const SettlementRiskInputRow& row, SettlementRiskReport* report) const;
  bool CheckDuplicateSettlementInstruction(const SettlementRiskInputRow& row, SettlementRiskReport* report) const;
  bool CheckSettlementDateDrift(const SettlementRiskInputRow& row, SettlementRiskReport* report) const;
  bool CheckHolidayCalendarMismatch(const SettlementRiskInputRow& row, SettlementRiskReport* report) const;
  bool CheckCutoffMissCap(const SettlementRiskInputRow& row, SettlementRiskReport* report) const;
  bool CheckNettingSetMismatch(const SettlementRiskInputRow& row, SettlementRiskReport* report) const;
  bool CheckCollateralMovementMismatch(const SettlementRiskInputRow& row, SettlementRiskReport* report) const;
  bool CheckRepoMaturityMismatch(const SettlementRiskInputRow& row, SettlementRiskReport* report) const;
  bool CheckStockLoanReturnMismatch(const SettlementRiskInputRow& row, SettlementRiskReport* report) const;
  bool CheckFailChargeCap(const SettlementRiskInputRow& row, SettlementRiskReport* report) const;
  bool CheckPenaltyFeeCap(const SettlementRiskInputRow& row, SettlementRiskReport* report) const;
  bool CheckClaimAmountMismatch(const SettlementRiskInputRow& row, SettlementRiskReport* report) const;
  bool CheckMarketValueBreakCap(const SettlementRiskInputRow& row, SettlementRiskReport* report) const;
  bool CheckPrincipalBreakCap(const SettlementRiskInputRow& row, SettlementRiskReport* report) const;
  bool CheckInterestBreakCap(const SettlementRiskInputRow& row, SettlementRiskReport* report) const;
  bool CheckAccruedInterestBreakCap(const SettlementRiskInputRow& row, SettlementRiskReport* report) const;
  bool CheckInstructionRepairCap(const SettlementRiskInputRow& row, SettlementRiskReport* report) const;
  bool CheckManualRepairCap(const SettlementRiskInputRow& row, SettlementRiskReport* report) const;
  bool CheckEscalationAgeCap(const SettlementRiskInputRow& row, SettlementRiskReport* report) const;
  bool CheckSettlementPriorityMismatch(const SettlementRiskInputRow& row, SettlementRiskReport* report) const;
};

std::string SettlementRiskSeverityName(SettlementRiskSeverity severity);
std::string SettlementRiskIssueTypeName(SettlementRiskIssueType type);

}  // namespace marketdata

#endif
