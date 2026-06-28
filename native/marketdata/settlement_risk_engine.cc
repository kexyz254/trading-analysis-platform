
#include "settlement_risk_engine.h"

#include <algorithm>
#include <cmath>
#include <cctype>
#include <iomanip>
#include <limits>
#include <sstream>
#include <stdexcept>

namespace marketdata {
namespace {

std::string TrimText(const std::string& value) {
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

std::string UpperText(const std::string& value) {
  std::string out = TrimText(value);
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

bool IsFinite(double value) {
  return std::isfinite(value);
}

bool IsError(SettlementRiskSeverity severity) {
  return severity == SettlementRiskSeverity::kError ||
         severity == SettlementRiskSeverity::kCritical;
}

double SafeRatio(double numerator, double denominator) {
  if (!IsFinite(numerator) || !IsFinite(denominator) ||
      std::fabs(denominator) < 0.0000001) {
    return std::numeric_limits<double>::infinity();
  }
  return std::fabs(numerator / denominator);
}

}  // namespace

std::string SettlementRiskInputRow::Key() const {
  std::string out = UpperText(portfolio_id) + ":" + UpperText(account_id);
  if (!symbol.empty()) {
    out += ":" + UpperText(symbol);
  }
  if (!strategy.empty()) {
    out += ":" + UpperText(strategy);
  }
  if (!venue.empty()) {
    out += ":" + UpperText(venue);
  }
  return out;
}

bool SettlementRiskReport::Ok() const {
  for (const SettlementRiskIssue& issue : issues) {
    if (IsError(issue.severity)) {
      return false;
    }
  }
  return true;
}

std::uint64_t SettlementRiskReport::ErrorCount() const {
  std::uint64_t total = 0;
  for (const SettlementRiskIssue& issue : issues) {
    if (IsError(issue.severity)) {
      ++total;
    }
  }
  return total;
}

std::uint64_t SettlementRiskReport::WarningCount() const {
  std::uint64_t total = 0;
  for (const SettlementRiskIssue& issue : issues) {
    if (issue.severity == SettlementRiskSeverity::kWarning) {
      ++total;
    }
  }
  return total;
}

SettlementRiskEngine::SettlementRiskEngine()
    : strict_mode_(false),
      allow_manual_overrides_(false) {
  thresholds_["cash_mismatch_cap"] = 0.250000;
  thresholds_["security_mismatch_cap"] = 0.325000;
  thresholds_["aged_fail_cap"] = 0.400000;
  thresholds_["counterparty_exposure_cap"] = 0.475000;
  thresholds_["settlement_currency_cap"] = 0.550000;
  thresholds_["instruction_age_cap"] = 0.625000;
  thresholds_["missing_s_s_i_flag"] = 0.700000;
  thresholds_["late_affirmation_cap"] = 0.775000;
  thresholds_["unmatched_trade_cap"] = 0.850000;
  thresholds_["partial_settlement_cap"] = 0.925000;
  thresholds_["buy_in_risk_cap"] = 1.000000;
  thresholds_["recall_risk_cap"] = 0.250000;
  thresholds_["corporate_action_conflict"] = 0.325000;
  thresholds_["dividend_entitlement_mismatch"] = 0.400000;
  thresholds_["tax_withholding_mismatch"] = 0.475000;
  thresholds_["f_x_funding_mismatch"] = 0.550000;
  thresholds_["custodian_break_cap"] = 0.625000;
  thresholds_["broker_break_cap"] = 0.700000;
  thresholds_["clearing_break_cap"] = 0.775000;
  thresholds_["depot_break_cap"] = 0.850000;
  thresholds_["external_reference_missing"] = 0.925000;
  thresholds_["duplicate_settlement_instruction"] = 1.000000;
  thresholds_["settlement_date_drift"] = 0.250000;
  thresholds_["holiday_calendar_mismatch"] = 0.325000;
  thresholds_["cutoff_miss_cap"] = 0.400000;
  thresholds_["netting_set_mismatch"] = 0.475000;
  thresholds_["collateral_movement_mismatch"] = 0.550000;
  thresholds_["repo_maturity_mismatch"] = 0.625000;
  thresholds_["stock_loan_return_mismatch"] = 0.700000;
  thresholds_["fail_charge_cap"] = 0.775000;
  thresholds_["penalty_fee_cap"] = 0.850000;
  thresholds_["claim_amount_mismatch"] = 0.925000;
  thresholds_["market_value_break_cap"] = 1.000000;
  thresholds_["principal_break_cap"] = 0.250000;
  thresholds_["interest_break_cap"] = 0.325000;
  thresholds_["accrued_interest_break_cap"] = 0.400000;
  thresholds_["instruction_repair_cap"] = 0.475000;
  thresholds_["manual_repair_cap"] = 0.550000;
  thresholds_["escalation_age_cap"] = 0.625000;
  thresholds_["settlement_priority_mismatch"] = 0.700000;
}

void SettlementRiskEngine::SetThreshold(const std::string& rule, double value) {
  if (!IsFinite(value) || value < 0.0) {
    throw std::invalid_argument("threshold must be finite and non-negative");
  }
  thresholds_[rule] = value;
}

void SettlementRiskEngine::SetStrictMode(bool value) {
  strict_mode_ = value;
}

void SettlementRiskEngine::SetAllowManualOverrides(bool value) {
  allow_manual_overrides_ = value;
}

SettlementRiskReport SettlementRiskEngine::Analyze(
    const std::vector<SettlementRiskInputRow>& rows) const {
  SettlementRiskReport report;

  for (const SettlementRiskInputRow& row : rows) {
    ++report.checked_rows;
    report.gross_notional += std::fabs(row.notional);
    report.net_notional += row.notional;
    report.total_stress_loss += row.stress_loss;
    report.max_abs_exposure =
        std::max(report.max_abs_exposure, std::fabs(row.gross_exposure));

    if (!ValidateCommonFields(row, &report)) {
      continue;
    }

    CheckCashMismatchCap(row, &report);
    CheckSecurityMismatchCap(row, &report);
    CheckAgedFailCap(row, &report);
    CheckCounterpartyExposureCap(row, &report);
    CheckSettlementCurrencyCap(row, &report);
    CheckInstructionAgeCap(row, &report);
    CheckMissingSSIFlag(row, &report);
    CheckLateAffirmationCap(row, &report);
    CheckUnmatchedTradeCap(row, &report);
    CheckPartialSettlementCap(row, &report);
    CheckBuyInRiskCap(row, &report);
    CheckRecallRiskCap(row, &report);
    CheckCorporateActionConflict(row, &report);
    CheckDividendEntitlementMismatch(row, &report);
    CheckTaxWithholdingMismatch(row, &report);
    CheckFXFundingMismatch(row, &report);
    CheckCustodianBreakCap(row, &report);
    CheckBrokerBreakCap(row, &report);
    CheckClearingBreakCap(row, &report);
    CheckDepotBreakCap(row, &report);
    CheckExternalReferenceMissing(row, &report);
    CheckDuplicateSettlementInstruction(row, &report);
    CheckSettlementDateDrift(row, &report);
    CheckHolidayCalendarMismatch(row, &report);
    CheckCutoffMissCap(row, &report);
    CheckNettingSetMismatch(row, &report);
    CheckCollateralMovementMismatch(row, &report);
    CheckRepoMaturityMismatch(row, &report);
    CheckStockLoanReturnMismatch(row, &report);
    CheckFailChargeCap(row, &report);
    CheckPenaltyFeeCap(row, &report);
    CheckClaimAmountMismatch(row, &report);
    CheckMarketValueBreakCap(row, &report);
    CheckPrincipalBreakCap(row, &report);
    CheckInterestBreakCap(row, &report);
    CheckAccruedInterestBreakCap(row, &report);
    CheckInstructionRepairCap(row, &report);
    CheckManualRepairCap(row, &report);
    CheckEscalationAgeCap(row, &report);
    CheckSettlementPriorityMismatch(row, &report);
  }

  report.error_rows = report.ErrorCount();
  report.warning_rows = report.WarningCount();
  return report;
}

double SettlementRiskEngine::Threshold(
    const std::string& rule,
    double fallback) const {
  const auto it = thresholds_.find(rule);
  if (it == thresholds_.end()) {
    return fallback;
  }
  return it->second;
}

bool SettlementRiskEngine::ValidateCommonFields(
    const SettlementRiskInputRow& row,
    SettlementRiskReport* report) const {
  bool valid = true;

  if (row.portfolio_id.empty() || row.account_id.empty()) {
    ++report->invalid_rows;
    AddIssue(
        SettlementRiskIssueType::kMissingIdentifier,
        SettlementRiskSeverity::kError,
        row,
        "common_fields",
        "row is missing portfolio_id or account_id",
        "non-empty identifiers",
        row.Key(),
        report);
    valid = false;
  }

  if (!IsFinite(row.notional) ||
      !IsFinite(row.market_value) ||
      !IsFinite(row.quantity) ||
      !IsFinite(row.price) ||
      !IsFinite(row.volatility) ||
      !IsFinite(row.gross_exposure) ||
      !IsFinite(row.net_exposure)) {
    ++report->invalid_rows;
    AddIssue(
        SettlementRiskIssueType::kNonFiniteMetric,
        SettlementRiskSeverity::kError,
        row,
        "common_fields",
        "row contains non-finite numeric data",
        "finite numeric metrics",
        "nan or infinity",
        report);
    valid = false;
  }

  if (row.manual_override && !allow_manual_overrides_) {
    AddIssue(
        SettlementRiskIssueType::kPolicyViolation,
        SettlementRiskSeverity::kWarning,
        row,
        "manual_override",
        "manual override present but overrides disabled",
        "no override",
        "manual override",
        report);
  }

  return valid;
}

void SettlementRiskEngine::AddIssue(
    SettlementRiskIssueType type,
    SettlementRiskSeverity severity,
    const SettlementRiskInputRow& row,
    const std::string& rule,
    const std::string& message,
    const std::string& expected,
    const std::string& actual,
    SettlementRiskReport* report) const {
  report->issues.push_back(SettlementRiskIssue{
      type,
      severity,
      row.Key(),
      rule,
      message,
      expected,
      actual,
  });
}


bool SettlementRiskEngine::CheckCashMismatchCap(
    const SettlementRiskInputRow& row,
    SettlementRiskReport* report) const {
  const std::string rule = "cash_mismatch_cap";
  const double threshold = Threshold(rule, 0.250000);

  if (std::fabs(row.notional) > threshold) {
    AddIssue(
        SettlementRiskIssueType::kLimitExceeded,
        SettlementRiskSeverity::kError,
        row,
        rule,
        "CashMismatchCap breached configured validation rule",
        "absolute metric below threshold",
        NumberText(row.notional),
        report);
    return false;
  }

  return true;
}


bool SettlementRiskEngine::CheckSecurityMismatchCap(
    const SettlementRiskInputRow& row,
    SettlementRiskReport* report) const {
  const std::string rule = "security_mismatch_cap";
  const double threshold = Threshold(rule, 0.325000);

  if (row.market_value < threshold) {
    AddIssue(
        SettlementRiskIssueType::kFloorBreached,
        SettlementRiskSeverity::kWarning,
        row,
        rule,
        "SecurityMismatchCap breached configured validation rule",
        "metric above configured floor",
        NumberText(row.market_value),
        report);
    return false;
  }

  return true;
}


bool SettlementRiskEngine::CheckAgedFailCap(
    const SettlementRiskInputRow& row,
    SettlementRiskReport* report) const {
  const std::string rule = "aged_fail_cap";
  const double threshold = Threshold(rule, 0.400000);

  if (SafeRatio(row.quantity, row.unrealized_pnl) > threshold) {
    AddIssue(
        SettlementRiskIssueType::kRatioViolation,
        SettlementRiskSeverity::kError,
        row,
        rule,
        "AgedFailCap breached configured validation rule",
        "ratio below configured threshold",
        NumberText(SafeRatio(row.quantity, row.unrealized_pnl)),
        report);
    return false;
  }

  return true;
}


bool SettlementRiskEngine::CheckCounterpartyExposureCap(
    const SettlementRiskInputRow& row,
    SettlementRiskReport* report) const {
  const std::string rule = "counterparty_exposure_cap";
  const double threshold = Threshold(rule, 0.475000);

  if (row.restricted && !allow_manual_overrides_) {
    AddIssue(
        SettlementRiskIssueType::kPolicyViolation,
        SettlementRiskSeverity::kCritical,
        row,
        rule,
        "CounterpartyExposureCap breached configured validation rule",
        "unrestricted row or approved override",
        "restricted row without override",
        report);
    return false;
  }

  return true;
}


bool SettlementRiskEngine::CheckSettlementCurrencyCap(
    const SettlementRiskInputRow& row,
    SettlementRiskReport* report) const {
  const std::string rule = "settlement_currency_cap";
  const double threshold = Threshold(rule, 0.550000);

  if (strict_mode_ && std::fabs(row.volatility - row.gross_exposure) > threshold) {
    AddIssue(
        SettlementRiskIssueType::kScenarioFailure,
        SettlementRiskSeverity::kWarning,
        row,
        rule,
        "SettlementCurrencyCap breached configured validation rule",
        "strict-mode metric difference inside threshold",
        NumberText(row.volatility - row.gross_exposure),
        report);
    return false;
  }

  return true;
}


bool SettlementRiskEngine::CheckInstructionAgeCap(
    const SettlementRiskInputRow& row,
    SettlementRiskReport* report) const {
  const std::string rule = "instruction_age_cap";
  const double threshold = Threshold(rule, 0.625000);

  if (std::fabs(row.average_daily_volume) > threshold) {
    AddIssue(
        SettlementRiskIssueType::kLimitExceeded,
        SettlementRiskSeverity::kError,
        row,
        rule,
        "InstructionAgeCap breached configured validation rule",
        "absolute metric below threshold",
        NumberText(row.average_daily_volume),
        report);
    return false;
  }

  return true;
}


bool SettlementRiskEngine::CheckMissingSSIFlag(
    const SettlementRiskInputRow& row,
    SettlementRiskReport* report) const {
  const std::string rule = "missing_s_s_i_flag";
  const double threshold = Threshold(rule, 0.700000);

  if (row.spread_bps < threshold) {
    AddIssue(
        SettlementRiskIssueType::kFloorBreached,
        SettlementRiskSeverity::kWarning,
        row,
        rule,
        "MissingSSIFlag breached configured validation rule",
        "metric above configured floor",
        NumberText(row.spread_bps),
        report);
    return false;
  }

  return true;
}


bool SettlementRiskEngine::CheckLateAffirmationCap(
    const SettlementRiskInputRow& row,
    SettlementRiskReport* report) const {
  const std::string rule = "late_affirmation_cap";
  const double threshold = Threshold(rule, 0.775000);

  if (SafeRatio(row.margin_rate, row.confidence) > threshold) {
    AddIssue(
        SettlementRiskIssueType::kRatioViolation,
        SettlementRiskSeverity::kError,
        row,
        rule,
        "LateAffirmationCap breached configured validation rule",
        "ratio below configured threshold",
        NumberText(SafeRatio(row.margin_rate, row.confidence)),
        report);
    return false;
  }

  return true;
}


bool SettlementRiskEngine::CheckUnmatchedTradeCap(
    const SettlementRiskInputRow& row,
    SettlementRiskReport* report) const {
  const std::string rule = "unmatched_trade_cap";
  const double threshold = Threshold(rule, 0.850000);

  if (row.restricted && !allow_manual_overrides_) {
    AddIssue(
        SettlementRiskIssueType::kPolicyViolation,
        SettlementRiskSeverity::kCritical,
        row,
        rule,
        "UnmatchedTradeCap breached configured validation rule",
        "unrestricted row or approved override",
        "restricted row without override",
        report);
    return false;
  }

  return true;
}


bool SettlementRiskEngine::CheckPartialSettlementCap(
    const SettlementRiskInputRow& row,
    SettlementRiskReport* report) const {
  const std::string rule = "partial_settlement_cap";
  const double threshold = Threshold(rule, 0.925000);

  if (strict_mode_ && std::fabs(row.unrealized_pnl - row.fill_rate) > threshold) {
    AddIssue(
        SettlementRiskIssueType::kScenarioFailure,
        SettlementRiskSeverity::kWarning,
        row,
        rule,
        "PartialSettlementCap breached configured validation rule",
        "strict-mode metric difference inside threshold",
        NumberText(row.unrealized_pnl - row.fill_rate),
        report);
    return false;
  }

  return true;
}


bool SettlementRiskEngine::CheckBuyInRiskCap(
    const SettlementRiskInputRow& row,
    SettlementRiskReport* report) const {
  const std::string rule = "buy_in_risk_cap";
  const double threshold = Threshold(rule, 1.000000);

  if (std::fabs(row.cash) > threshold) {
    AddIssue(
        SettlementRiskIssueType::kLimitExceeded,
        SettlementRiskSeverity::kError,
        row,
        rule,
        "BuyInRiskCap breached configured validation rule",
        "absolute metric below threshold",
        NumberText(row.cash),
        report);
    return false;
  }

  return true;
}


bool SettlementRiskEngine::CheckRecallRiskCap(
    const SettlementRiskInputRow& row,
    SettlementRiskReport* report) const {
  const std::string rule = "recall_risk_cap";
  const double threshold = Threshold(rule, 0.250000);

  if (row.gross_exposure < threshold) {
    AddIssue(
        SettlementRiskIssueType::kFloorBreached,
        SettlementRiskSeverity::kWarning,
        row,
        rule,
        "RecallRiskCap breached configured validation rule",
        "metric above configured floor",
        NumberText(row.gross_exposure),
        report);
    return false;
  }

  return true;
}


bool SettlementRiskEngine::CheckCorporateActionConflict(
    const SettlementRiskInputRow& row,
    SettlementRiskReport* report) const {
  const std::string rule = "corporate_action_conflict";
  const double threshold = Threshold(rule, 0.325000);

  if (SafeRatio(row.net_exposure, row.settlement_amount) > threshold) {
    AddIssue(
        SettlementRiskIssueType::kRatioViolation,
        SettlementRiskSeverity::kError,
        row,
        rule,
        "CorporateActionConflict breached configured validation rule",
        "ratio below configured threshold",
        NumberText(SafeRatio(row.net_exposure, row.settlement_amount)),
        report);
    return false;
  }

  return true;
}


bool SettlementRiskEngine::CheckDividendEntitlementMismatch(
    const SettlementRiskInputRow& row,
    SettlementRiskReport* report) const {
  const std::string rule = "dividend_entitlement_mismatch";
  const double threshold = Threshold(rule, 0.400000);

  if (row.restricted && !allow_manual_overrides_) {
    AddIssue(
        SettlementRiskIssueType::kPolicyViolation,
        SettlementRiskSeverity::kCritical,
        row,
        rule,
        "DividendEntitlementMismatch breached configured validation rule",
        "unrestricted row or approved override",
        "restricted row without override",
        report);
    return false;
  }

  return true;
}


bool SettlementRiskEngine::CheckTaxWithholdingMismatch(
    const SettlementRiskInputRow& row,
    SettlementRiskReport* report) const {
  const std::string rule = "tax_withholding_mismatch";
  const double threshold = Threshold(rule, 0.475000);

  if (strict_mode_ && std::fabs(row.confidence - row.available_liquidity) > threshold) {
    AddIssue(
        SettlementRiskIssueType::kScenarioFailure,
        SettlementRiskSeverity::kWarning,
        row,
        rule,
        "TaxWithholdingMismatch breached configured validation rule",
        "strict-mode metric difference inside threshold",
        NumberText(row.confidence - row.available_liquidity),
        report);
    return false;
  }

  return true;
}


bool SettlementRiskEngine::CheckFXFundingMismatch(
    const SettlementRiskInputRow& row,
    SettlementRiskReport* report) const {
  const std::string rule = "f_x_funding_mismatch";
  const double threshold = Threshold(rule, 0.550000);

  if (std::fabs(row.latency_ms) > threshold) {
    AddIssue(
        SettlementRiskIssueType::kLimitExceeded,
        SettlementRiskSeverity::kError,
        row,
        rule,
        "FXFundingMismatch breached configured validation rule",
        "absolute metric below threshold",
        NumberText(row.latency_ms),
        report);
    return false;
  }

  return true;
}


bool SettlementRiskEngine::CheckCustodianBreakCap(
    const SettlementRiskInputRow& row,
    SettlementRiskReport* report) const {
  const std::string rule = "custodian_break_cap";
  const double threshold = Threshold(rule, 0.625000);

  if (row.fill_rate < threshold) {
    AddIssue(
        SettlementRiskIssueType::kFloorBreached,
        SettlementRiskSeverity::kWarning,
        row,
        rule,
        "CustodianBreakCap breached configured validation rule",
        "metric above configured floor",
        NumberText(row.fill_rate),
        report);
    return false;
  }

  return true;
}


bool SettlementRiskEngine::CheckBrokerBreakCap(
    const SettlementRiskInputRow& row,
    SettlementRiskReport* report) const {
  const std::string rule = "broker_break_cap";
  const double threshold = Threshold(rule, 0.700000);

  if (SafeRatio(row.reject_rate, row.beta) > threshold) {
    AddIssue(
        SettlementRiskIssueType::kRatioViolation,
        SettlementRiskSeverity::kError,
        row,
        rule,
        "BrokerBreakCap breached configured validation rule",
        "ratio below configured threshold",
        NumberText(SafeRatio(row.reject_rate, row.beta)),
        report);
    return false;
  }

  return true;
}


bool SettlementRiskEngine::CheckClearingBreakCap(
    const SettlementRiskInputRow& row,
    SettlementRiskReport* report) const {
  const std::string rule = "clearing_break_cap";
  const double threshold = Threshold(rule, 0.775000);

  if (row.restricted && !allow_manual_overrides_) {
    AddIssue(
        SettlementRiskIssueType::kPolicyViolation,
        SettlementRiskSeverity::kCritical,
        row,
        rule,
        "ClearingBreakCap breached configured validation rule",
        "unrestricted row or approved override",
        "restricted row without override",
        report);
    return false;
  }

  return true;
}


bool SettlementRiskEngine::CheckDepotBreakCap(
    const SettlementRiskInputRow& row,
    SettlementRiskReport* report) const {
  const std::string rule = "depot_break_cap";
  const double threshold = Threshold(rule, 0.850000);

  if (strict_mode_ && std::fabs(row.settlement_amount - row.var_10d) > threshold) {
    AddIssue(
        SettlementRiskIssueType::kScenarioFailure,
        SettlementRiskSeverity::kWarning,
        row,
        rule,
        "DepotBreakCap breached configured validation rule",
        "strict-mode metric difference inside threshold",
        NumberText(row.settlement_amount - row.var_10d),
        report);
    return false;
  }

  return true;
}


bool SettlementRiskEngine::CheckExternalReferenceMissing(
    const SettlementRiskInputRow& row,
    SettlementRiskReport* report) const {
  const std::string rule = "external_reference_missing";
  const double threshold = Threshold(rule, 0.925000);

  if (std::fabs(row.expected_amount) > threshold) {
    AddIssue(
        SettlementRiskIssueType::kLimitExceeded,
        SettlementRiskSeverity::kError,
        row,
        rule,
        "ExternalReferenceMissing breached configured validation rule",
        "absolute metric below threshold",
        NumberText(row.expected_amount),
        report);
    return false;
  }

  return true;
}


bool SettlementRiskEngine::CheckDuplicateSettlementInstruction(
    const SettlementRiskInputRow& row,
    SettlementRiskReport* report) const {
  const std::string rule = "duplicate_settlement_instruction";
  const double threshold = Threshold(rule, 1.000000);

  if (row.available_liquidity < threshold) {
    AddIssue(
        SettlementRiskIssueType::kFloorBreached,
        SettlementRiskSeverity::kWarning,
        row,
        rule,
        "DuplicateSettlementInstruction breached configured validation rule",
        "metric above configured floor",
        NumberText(row.available_liquidity),
        report);
    return false;
  }

  return true;
}


bool SettlementRiskEngine::CheckSettlementDateDrift(
    const SettlementRiskInputRow& row,
    SettlementRiskReport* report) const {
  const std::string rule = "settlement_date_drift";
  const double threshold = Threshold(rule, 0.250000);

  if (SafeRatio(row.correlation, row.fee_rate) > threshold) {
    AddIssue(
        SettlementRiskIssueType::kRatioViolation,
        SettlementRiskSeverity::kError,
        row,
        rule,
        "SettlementDateDrift breached configured validation rule",
        "ratio below configured threshold",
        NumberText(SafeRatio(row.correlation, row.fee_rate)),
        report);
    return false;
  }

  return true;
}


bool SettlementRiskEngine::CheckHolidayCalendarMismatch(
    const SettlementRiskInputRow& row,
    SettlementRiskReport* report) const {
  const std::string rule = "holiday_calendar_mismatch";
  const double threshold = Threshold(rule, 0.325000);

  if (row.restricted && !allow_manual_overrides_) {
    AddIssue(
        SettlementRiskIssueType::kPolicyViolation,
        SettlementRiskSeverity::kCritical,
        row,
        rule,
        "HolidayCalendarMismatch breached configured validation rule",
        "unrestricted row or approved override",
        "restricted row without override",
        report);
    return false;
  }

  return true;
}


bool SettlementRiskEngine::CheckCutoffMissCap(
    const SettlementRiskInputRow& row,
    SettlementRiskReport* report) const {
  const std::string rule = "cutoff_miss_cap";
  const double threshold = Threshold(rule, 0.400000);

  if (strict_mode_ && std::fabs(row.beta - row.turnover_rate) > threshold) {
    AddIssue(
        SettlementRiskIssueType::kScenarioFailure,
        SettlementRiskSeverity::kWarning,
        row,
        rule,
        "CutoffMissCap breached configured validation rule",
        "strict-mode metric difference inside threshold",
        NumberText(row.beta - row.turnover_rate),
        report);
    return false;
  }

  return true;
}


bool SettlementRiskEngine::CheckNettingSetMismatch(
    const SettlementRiskInputRow& row,
    SettlementRiskReport* report) const {
  const std::string rule = "netting_set_mismatch";
  const double threshold = Threshold(rule, 0.475000);

  if (std::fabs(row.var_1d) > threshold) {
    AddIssue(
        SettlementRiskIssueType::kLimitExceeded,
        SettlementRiskSeverity::kError,
        row,
        rule,
        "NettingSetMismatch breached configured validation rule",
        "absolute metric below threshold",
        NumberText(row.var_1d),
        report);
    return false;
  }

  return true;
}


bool SettlementRiskEngine::CheckCollateralMovementMismatch(
    const SettlementRiskInputRow& row,
    SettlementRiskReport* report) const {
  const std::string rule = "collateral_movement_mismatch";
  const double threshold = Threshold(rule, 0.550000);

  if (row.var_10d < threshold) {
    AddIssue(
        SettlementRiskIssueType::kFloorBreached,
        SettlementRiskSeverity::kWarning,
        row,
        rule,
        "CollateralMovementMismatch breached configured validation rule",
        "metric above configured floor",
        NumberText(row.var_10d),
        report);
    return false;
  }

  return true;
}


bool SettlementRiskEngine::CheckRepoMaturityMismatch(
    const SettlementRiskInputRow& row,
    SettlementRiskReport* report) const {
  const std::string rule = "repo_maturity_mismatch";
  const double threshold = Threshold(rule, 0.625000);

  if (SafeRatio(row.haircut, row.quantity) > threshold) {
    AddIssue(
        SettlementRiskIssueType::kRatioViolation,
        SettlementRiskSeverity::kError,
        row,
        rule,
        "RepoMaturityMismatch breached configured validation rule",
        "ratio below configured threshold",
        NumberText(SafeRatio(row.haircut, row.quantity)),
        report);
    return false;
  }

  return true;
}


bool SettlementRiskEngine::CheckStockLoanReturnMismatch(
    const SettlementRiskInputRow& row,
    SettlementRiskReport* report) const {
  const std::string rule = "stock_loan_return_mismatch";
  const double threshold = Threshold(rule, 0.700000);

  if (row.restricted && !allow_manual_overrides_) {
    AddIssue(
        SettlementRiskIssueType::kPolicyViolation,
        SettlementRiskSeverity::kCritical,
        row,
        rule,
        "StockLoanReturnMismatch breached configured validation rule",
        "unrestricted row or approved override",
        "restricted row without override",
        report);
    return false;
  }

  return true;
}


bool SettlementRiskEngine::CheckFailChargeCap(
    const SettlementRiskInputRow& row,
    SettlementRiskReport* report) const {
  const std::string rule = "fail_charge_cap";
  const double threshold = Threshold(rule, 0.775000);

  if (strict_mode_ && std::fabs(row.fee_rate - row.volatility) > threshold) {
    AddIssue(
        SettlementRiskIssueType::kScenarioFailure,
        SettlementRiskSeverity::kWarning,
        row,
        rule,
        "FailChargeCap breached configured validation rule",
        "strict-mode metric difference inside threshold",
        NumberText(row.fee_rate - row.volatility),
        report);
    return false;
  }

  return true;
}


bool SettlementRiskEngine::CheckPenaltyFeeCap(
    const SettlementRiskInputRow& row,
    SettlementRiskReport* report) const {
  const std::string rule = "penalty_fee_cap";
  const double threshold = Threshold(rule, 0.850000);

  if (std::fabs(row.slippage_bps) > threshold) {
    AddIssue(
        SettlementRiskIssueType::kLimitExceeded,
        SettlementRiskSeverity::kError,
        row,
        rule,
        "PenaltyFeeCap breached configured validation rule",
        "absolute metric below threshold",
        NumberText(row.slippage_bps),
        report);
    return false;
  }

  return true;
}


bool SettlementRiskEngine::CheckClaimAmountMismatch(
    const SettlementRiskInputRow& row,
    SettlementRiskReport* report) const {
  const std::string rule = "claim_amount_mismatch";
  const double threshold = Threshold(rule, 0.925000);

  if (row.turnover_rate < threshold) {
    AddIssue(
        SettlementRiskIssueType::kFloorBreached,
        SettlementRiskSeverity::kWarning,
        row,
        rule,
        "ClaimAmountMismatch breached configured validation rule",
        "metric above configured floor",
        NumberText(row.turnover_rate),
        report);
    return false;
  }

  return true;
}


bool SettlementRiskEngine::CheckMarketValueBreakCap(
    const SettlementRiskInputRow& row,
    SettlementRiskReport* report) const {
  const std::string rule = "market_value_break_cap";
  const double threshold = Threshold(rule, 1.000000);

  if (SafeRatio(row.notional, row.margin_rate) > threshold) {
    AddIssue(
        SettlementRiskIssueType::kRatioViolation,
        SettlementRiskSeverity::kError,
        row,
        rule,
        "MarketValueBreakCap breached configured validation rule",
        "ratio below configured threshold",
        NumberText(SafeRatio(row.notional, row.margin_rate)),
        report);
    return false;
  }

  return true;
}


bool SettlementRiskEngine::CheckPrincipalBreakCap(
    const SettlementRiskInputRow& row,
    SettlementRiskReport* report) const {
  const std::string rule = "principal_break_cap";
  const double threshold = Threshold(rule, 0.250000);

  if (row.restricted && !allow_manual_overrides_) {
    AddIssue(
        SettlementRiskIssueType::kPolicyViolation,
        SettlementRiskSeverity::kCritical,
        row,
        rule,
        "PrincipalBreakCap breached configured validation rule",
        "unrestricted row or approved override",
        "restricted row without override",
        report);
    return false;
  }

  return true;
}


bool SettlementRiskEngine::CheckInterestBreakCap(
    const SettlementRiskInputRow& row,
    SettlementRiskReport* report) const {
  const std::string rule = "interest_break_cap";
  const double threshold = Threshold(rule, 0.325000);

  if (strict_mode_ && std::fabs(row.quantity - row.unrealized_pnl) > threshold) {
    AddIssue(
        SettlementRiskIssueType::kScenarioFailure,
        SettlementRiskSeverity::kWarning,
        row,
        rule,
        "InterestBreakCap breached configured validation rule",
        "strict-mode metric difference inside threshold",
        NumberText(row.quantity - row.unrealized_pnl),
        report);
    return false;
  }

  return true;
}


bool SettlementRiskEngine::CheckAccruedInterestBreakCap(
    const SettlementRiskInputRow& row,
    SettlementRiskReport* report) const {
  const std::string rule = "accrued_interest_break_cap";
  const double threshold = Threshold(rule, 0.400000);

  if (std::fabs(row.price) > threshold) {
    AddIssue(
        SettlementRiskIssueType::kLimitExceeded,
        SettlementRiskSeverity::kError,
        row,
        rule,
        "AccruedInterestBreakCap breached configured validation rule",
        "absolute metric below threshold",
        NumberText(row.price),
        report);
    return false;
  }

  return true;
}


bool SettlementRiskEngine::CheckInstructionRepairCap(
    const SettlementRiskInputRow& row,
    SettlementRiskReport* report) const {
  const std::string rule = "instruction_repair_cap";
  const double threshold = Threshold(rule, 0.475000);

  if (row.volatility < threshold) {
    AddIssue(
        SettlementRiskIssueType::kFloorBreached,
        SettlementRiskSeverity::kWarning,
        row,
        rule,
        "InstructionRepairCap breached configured validation rule",
        "metric above configured floor",
        NumberText(row.volatility),
        report);
    return false;
  }

  return true;
}


bool SettlementRiskEngine::CheckManualRepairCap(
    const SettlementRiskInputRow& row,
    SettlementRiskReport* report) const {
  const std::string rule = "manual_repair_cap";
  const double threshold = Threshold(rule, 0.550000);

  if (SafeRatio(row.average_daily_volume, row.net_exposure) > threshold) {
    AddIssue(
        SettlementRiskIssueType::kRatioViolation,
        SettlementRiskSeverity::kError,
        row,
        rule,
        "ManualRepairCap breached configured validation rule",
        "ratio below configured threshold",
        NumberText(SafeRatio(row.average_daily_volume, row.net_exposure)),
        report);
    return false;
  }

  return true;
}


bool SettlementRiskEngine::CheckEscalationAgeCap(
    const SettlementRiskInputRow& row,
    SettlementRiskReport* report) const {
  const std::string rule = "escalation_age_cap";
  const double threshold = Threshold(rule, 0.625000);

  if (row.restricted && !allow_manual_overrides_) {
    AddIssue(
        SettlementRiskIssueType::kPolicyViolation,
        SettlementRiskSeverity::kCritical,
        row,
        rule,
        "EscalationAgeCap breached configured validation rule",
        "unrestricted row or approved override",
        "restricted row without override",
        report);
    return false;
  }

  return true;
}


bool SettlementRiskEngine::CheckSettlementPriorityMismatch(
    const SettlementRiskInputRow& row,
    SettlementRiskReport* report) const {
  const std::string rule = "settlement_priority_mismatch";
  const double threshold = Threshold(rule, 0.700000);

  if (strict_mode_ && std::fabs(row.margin_rate - row.confidence) > threshold) {
    AddIssue(
        SettlementRiskIssueType::kScenarioFailure,
        SettlementRiskSeverity::kWarning,
        row,
        rule,
        "SettlementPriorityMismatch breached configured validation rule",
        "strict-mode metric difference inside threshold",
        NumberText(row.margin_rate - row.confidence),
        report);
    return false;
  }

  return true;
}


std::string SettlementRiskSeverityName(SettlementRiskSeverity severity) {
  switch (severity) {
    case SettlementRiskSeverity::kInfo:
      return "info";
    case SettlementRiskSeverity::kWarning:
      return "warning";
    case SettlementRiskSeverity::kError:
      return "error";
    case SettlementRiskSeverity::kCritical:
      return "critical";
  }
  return "unknown";
}

std::string SettlementRiskIssueTypeName(SettlementRiskIssueType type) {
  switch (type) {
    case SettlementRiskIssueType::kInvalidInput:
      return "invalid_input";
    case SettlementRiskIssueType::kMissingIdentifier:
      return "missing_identifier";
    case SettlementRiskIssueType::kNonFiniteMetric:
      return "non_finite_metric";
    case SettlementRiskIssueType::kThresholdBreach:
      return "threshold_breach";
    case SettlementRiskIssueType::kLimitExceeded:
      return "limit_exceeded";
    case SettlementRiskIssueType::kFloorBreached:
      return "floor_breached";
    case SettlementRiskIssueType::kRatioViolation:
      return "ratio_violation";
    case SettlementRiskIssueType::kPolicyViolation:
      return "policy_violation";
    case SettlementRiskIssueType::kScenarioFailure:
      return "scenario_failure";
    case SettlementRiskIssueType::kAggregationFailure:
      return "aggregation_failure";
  }
  return "unknown";
}

}  // namespace marketdata
