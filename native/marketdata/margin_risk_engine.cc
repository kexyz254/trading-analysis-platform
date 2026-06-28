
#include "margin_risk_engine.h"

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

bool IsError(MarginRiskSeverity severity) {
  return severity == MarginRiskSeverity::kError ||
         severity == MarginRiskSeverity::kCritical;
}

double SafeRatio(double numerator, double denominator) {
  if (!IsFinite(numerator) || !IsFinite(denominator) ||
      std::fabs(denominator) < 0.0000001) {
    return std::numeric_limits<double>::infinity();
  }
  return std::fabs(numerator / denominator);
}

}  // namespace

std::string MarginRiskInputRow::Key() const {
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

bool MarginRiskReport::Ok() const {
  for (const MarginRiskIssue& issue : issues) {
    if (IsError(issue.severity)) {
      return false;
    }
  }
  return true;
}

std::uint64_t MarginRiskReport::ErrorCount() const {
  std::uint64_t total = 0;
  for (const MarginRiskIssue& issue : issues) {
    if (IsError(issue.severity)) {
      ++total;
    }
  }
  return total;
}

std::uint64_t MarginRiskReport::WarningCount() const {
  std::uint64_t total = 0;
  for (const MarginRiskIssue& issue : issues) {
    if (issue.severity == MarginRiskSeverity::kWarning) {
      ++total;
    }
  }
  return total;
}

MarginRiskEngine::MarginRiskEngine()
    : strict_mode_(false),
      allow_manual_overrides_(false) {
  thresholds_["initial_margin_floor"] = 0.250000;
  thresholds_["maintenance_margin_floor"] = 0.325000;
  thresholds_["concentration_cap"] = 0.400000;
  thresholds_["leverage_cap"] = 0.475000;
  thresholds_["borrow_rate_cap"] = 0.550000;
  thresholds_["haircut_floor"] = 0.625000;
  thresholds_["volatility_cap"] = 0.700000;
  thresholds_["stress_loss_cap"] = 0.775000;
  thresholds_["cash_coverage_floor"] = 0.850000;
  thresholds_["exposure_cap"] = 0.925000;
  thresholds_["net_exposure_band"] = 1.000000;
  thresholds_["gross_exposure_cap"] = 0.250000;
  thresholds_["portfolio_beta_cap"] = 0.325000;
  thresholds_["correlation_cluster_cap"] = 0.400000;
  thresholds_["short_inventory_cap"] = 0.475000;
  thresholds_["option_premium_cap"] = 0.550000;
  thresholds_["future_span_margin_cap"] = 0.625000;
  thresholds_["cross_margin_offset_cap"] = 0.700000;
  thresholds_["currency_haircut_floor"] = 0.775000;
  thresholds_["liquidation_buffer_floor"] = 0.850000;
  thresholds_["intraday_margin_cap"] = 0.925000;
  thresholds_["overnight_margin_cap"] = 1.000000;
  thresholds_["house_margin_add_on_cap"] = 0.250000;
  thresholds_["regulatory_margin_floor"] = 0.325000;
  thresholds_["risk_weight_cap"] = 0.400000;
  thresholds_["delta_adjusted_exposure_cap"] = 0.475000;
  thresholds_["gamma_notional_cap"] = 0.550000;
  thresholds_["vega_notional_cap"] = 0.625000;
  thresholds_["theta_decay_cap"] = 0.700000;
  thresholds_["stress_liquidity_cap"] = 0.775000;
  thresholds_["collateral_utilization_cap"] = 0.850000;
  thresholds_["free_equity_floor"] = 0.925000;
  thresholds_["drawdown_cap"] = 1.000000;
  thresholds_["single_issuer_cap"] = 0.250000;
  thresholds_["sector_exposure_cap"] = 0.325000;
  thresholds_["country_exposure_cap"] = 0.400000;
  thresholds_["restricted_asset_cap"] = 0.475000;
  thresholds_["unsettled_cash_cap"] = 0.550000;
  thresholds_["fail_to_deliver_cap"] = 0.625000;
  thresholds_["corporate_action_risk_cap"] = 0.700000;
}

void MarginRiskEngine::SetThreshold(const std::string& rule, double value) {
  if (!IsFinite(value) || value < 0.0) {
    throw std::invalid_argument("threshold must be finite and non-negative");
  }
  thresholds_[rule] = value;
}

void MarginRiskEngine::SetStrictMode(bool value) {
  strict_mode_ = value;
}

void MarginRiskEngine::SetAllowManualOverrides(bool value) {
  allow_manual_overrides_ = value;
}

MarginRiskReport MarginRiskEngine::Analyze(
    const std::vector<MarginRiskInputRow>& rows) const {
  MarginRiskReport report;

  for (const MarginRiskInputRow& row : rows) {
    ++report.checked_rows;
    report.gross_notional += std::fabs(row.notional);
    report.net_notional += row.notional;
    report.total_stress_loss += row.stress_loss;
    report.max_abs_exposure =
        std::max(report.max_abs_exposure, std::fabs(row.gross_exposure));

    if (!ValidateCommonFields(row, &report)) {
      continue;
    }

    CheckInitialMarginFloor(row, &report);
    CheckMaintenanceMarginFloor(row, &report);
    CheckConcentrationCap(row, &report);
    CheckLeverageCap(row, &report);
    CheckBorrowRateCap(row, &report);
    CheckHaircutFloor(row, &report);
    CheckVolatilityCap(row, &report);
    CheckStressLossCap(row, &report);
    CheckCashCoverageFloor(row, &report);
    CheckExposureCap(row, &report);
    CheckNetExposureBand(row, &report);
    CheckGrossExposureCap(row, &report);
    CheckPortfolioBetaCap(row, &report);
    CheckCorrelationClusterCap(row, &report);
    CheckShortInventoryCap(row, &report);
    CheckOptionPremiumCap(row, &report);
    CheckFutureSpanMarginCap(row, &report);
    CheckCrossMarginOffsetCap(row, &report);
    CheckCurrencyHaircutFloor(row, &report);
    CheckLiquidationBufferFloor(row, &report);
    CheckIntradayMarginCap(row, &report);
    CheckOvernightMarginCap(row, &report);
    CheckHouseMarginAddOnCap(row, &report);
    CheckRegulatoryMarginFloor(row, &report);
    CheckRiskWeightCap(row, &report);
    CheckDeltaAdjustedExposureCap(row, &report);
    CheckGammaNotionalCap(row, &report);
    CheckVegaNotionalCap(row, &report);
    CheckThetaDecayCap(row, &report);
    CheckStressLiquidityCap(row, &report);
    CheckCollateralUtilizationCap(row, &report);
    CheckFreeEquityFloor(row, &report);
    CheckDrawdownCap(row, &report);
    CheckSingleIssuerCap(row, &report);
    CheckSectorExposureCap(row, &report);
    CheckCountryExposureCap(row, &report);
    CheckRestrictedAssetCap(row, &report);
    CheckUnsettledCashCap(row, &report);
    CheckFailToDeliverCap(row, &report);
    CheckCorporateActionRiskCap(row, &report);
  }

  report.error_rows = report.ErrorCount();
  report.warning_rows = report.WarningCount();
  return report;
}

double MarginRiskEngine::Threshold(
    const std::string& rule,
    double fallback) const {
  const auto it = thresholds_.find(rule);
  if (it == thresholds_.end()) {
    return fallback;
  }
  return it->second;
}

bool MarginRiskEngine::ValidateCommonFields(
    const MarginRiskInputRow& row,
    MarginRiskReport* report) const {
  bool valid = true;

  if (row.portfolio_id.empty() || row.account_id.empty()) {
    ++report->invalid_rows;
    AddIssue(
        MarginRiskIssueType::kMissingIdentifier,
        MarginRiskSeverity::kError,
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
        MarginRiskIssueType::kNonFiniteMetric,
        MarginRiskSeverity::kError,
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
        MarginRiskIssueType::kPolicyViolation,
        MarginRiskSeverity::kWarning,
        row,
        "manual_override",
        "manual override present but overrides disabled",
        "no override",
        "manual override",
        report);
  }

  return valid;
}

void MarginRiskEngine::AddIssue(
    MarginRiskIssueType type,
    MarginRiskSeverity severity,
    const MarginRiskInputRow& row,
    const std::string& rule,
    const std::string& message,
    const std::string& expected,
    const std::string& actual,
    MarginRiskReport* report) const {
  report->issues.push_back(MarginRiskIssue{
      type,
      severity,
      row.Key(),
      rule,
      message,
      expected,
      actual,
  });
}


bool MarginRiskEngine::CheckInitialMarginFloor(
    const MarginRiskInputRow& row,
    MarginRiskReport* report) const {
  const std::string rule = "initial_margin_floor";
  const double threshold = Threshold(rule, 0.250000);

  if (std::fabs(row.notional) > threshold) {
    AddIssue(
        MarginRiskIssueType::kLimitExceeded,
        MarginRiskSeverity::kError,
        row,
        rule,
        "InitialMarginFloor breached configured validation rule",
        "absolute metric below threshold",
        NumberText(row.notional),
        report);
    return false;
  }

  return true;
}


bool MarginRiskEngine::CheckMaintenanceMarginFloor(
    const MarginRiskInputRow& row,
    MarginRiskReport* report) const {
  const std::string rule = "maintenance_margin_floor";
  const double threshold = Threshold(rule, 0.325000);

  if (row.market_value < threshold) {
    AddIssue(
        MarginRiskIssueType::kFloorBreached,
        MarginRiskSeverity::kWarning,
        row,
        rule,
        "MaintenanceMarginFloor breached configured validation rule",
        "metric above configured floor",
        NumberText(row.market_value),
        report);
    return false;
  }

  return true;
}


bool MarginRiskEngine::CheckConcentrationCap(
    const MarginRiskInputRow& row,
    MarginRiskReport* report) const {
  const std::string rule = "concentration_cap";
  const double threshold = Threshold(rule, 0.400000);

  if (SafeRatio(row.quantity, row.unrealized_pnl) > threshold) {
    AddIssue(
        MarginRiskIssueType::kRatioViolation,
        MarginRiskSeverity::kError,
        row,
        rule,
        "ConcentrationCap breached configured validation rule",
        "ratio below configured threshold",
        NumberText(SafeRatio(row.quantity, row.unrealized_pnl)),
        report);
    return false;
  }

  return true;
}


bool MarginRiskEngine::CheckLeverageCap(
    const MarginRiskInputRow& row,
    MarginRiskReport* report) const {
  const std::string rule = "leverage_cap";
  const double threshold = Threshold(rule, 0.475000);

  if (row.restricted && !allow_manual_overrides_) {
    AddIssue(
        MarginRiskIssueType::kPolicyViolation,
        MarginRiskSeverity::kCritical,
        row,
        rule,
        "LeverageCap breached configured validation rule",
        "unrestricted row or approved override",
        "restricted row without override",
        report);
    return false;
  }

  return true;
}


bool MarginRiskEngine::CheckBorrowRateCap(
    const MarginRiskInputRow& row,
    MarginRiskReport* report) const {
  const std::string rule = "borrow_rate_cap";
  const double threshold = Threshold(rule, 0.550000);

  if (strict_mode_ && std::fabs(row.volatility - row.gross_exposure) > threshold) {
    AddIssue(
        MarginRiskIssueType::kScenarioFailure,
        MarginRiskSeverity::kWarning,
        row,
        rule,
        "BorrowRateCap breached configured validation rule",
        "strict-mode metric difference inside threshold",
        NumberText(row.volatility - row.gross_exposure),
        report);
    return false;
  }

  return true;
}


bool MarginRiskEngine::CheckHaircutFloor(
    const MarginRiskInputRow& row,
    MarginRiskReport* report) const {
  const std::string rule = "haircut_floor";
  const double threshold = Threshold(rule, 0.625000);

  if (std::fabs(row.average_daily_volume) > threshold) {
    AddIssue(
        MarginRiskIssueType::kLimitExceeded,
        MarginRiskSeverity::kError,
        row,
        rule,
        "HaircutFloor breached configured validation rule",
        "absolute metric below threshold",
        NumberText(row.average_daily_volume),
        report);
    return false;
  }

  return true;
}


bool MarginRiskEngine::CheckVolatilityCap(
    const MarginRiskInputRow& row,
    MarginRiskReport* report) const {
  const std::string rule = "volatility_cap";
  const double threshold = Threshold(rule, 0.700000);

  if (row.spread_bps < threshold) {
    AddIssue(
        MarginRiskIssueType::kFloorBreached,
        MarginRiskSeverity::kWarning,
        row,
        rule,
        "VolatilityCap breached configured validation rule",
        "metric above configured floor",
        NumberText(row.spread_bps),
        report);
    return false;
  }

  return true;
}


bool MarginRiskEngine::CheckStressLossCap(
    const MarginRiskInputRow& row,
    MarginRiskReport* report) const {
  const std::string rule = "stress_loss_cap";
  const double threshold = Threshold(rule, 0.775000);

  if (SafeRatio(row.margin_rate, row.confidence) > threshold) {
    AddIssue(
        MarginRiskIssueType::kRatioViolation,
        MarginRiskSeverity::kError,
        row,
        rule,
        "StressLossCap breached configured validation rule",
        "ratio below configured threshold",
        NumberText(SafeRatio(row.margin_rate, row.confidence)),
        report);
    return false;
  }

  return true;
}


bool MarginRiskEngine::CheckCashCoverageFloor(
    const MarginRiskInputRow& row,
    MarginRiskReport* report) const {
  const std::string rule = "cash_coverage_floor";
  const double threshold = Threshold(rule, 0.850000);

  if (row.restricted && !allow_manual_overrides_) {
    AddIssue(
        MarginRiskIssueType::kPolicyViolation,
        MarginRiskSeverity::kCritical,
        row,
        rule,
        "CashCoverageFloor breached configured validation rule",
        "unrestricted row or approved override",
        "restricted row without override",
        report);
    return false;
  }

  return true;
}


bool MarginRiskEngine::CheckExposureCap(
    const MarginRiskInputRow& row,
    MarginRiskReport* report) const {
  const std::string rule = "exposure_cap";
  const double threshold = Threshold(rule, 0.925000);

  if (strict_mode_ && std::fabs(row.unrealized_pnl - row.fill_rate) > threshold) {
    AddIssue(
        MarginRiskIssueType::kScenarioFailure,
        MarginRiskSeverity::kWarning,
        row,
        rule,
        "ExposureCap breached configured validation rule",
        "strict-mode metric difference inside threshold",
        NumberText(row.unrealized_pnl - row.fill_rate),
        report);
    return false;
  }

  return true;
}


bool MarginRiskEngine::CheckNetExposureBand(
    const MarginRiskInputRow& row,
    MarginRiskReport* report) const {
  const std::string rule = "net_exposure_band";
  const double threshold = Threshold(rule, 1.000000);

  if (std::fabs(row.cash) > threshold) {
    AddIssue(
        MarginRiskIssueType::kLimitExceeded,
        MarginRiskSeverity::kError,
        row,
        rule,
        "NetExposureBand breached configured validation rule",
        "absolute metric below threshold",
        NumberText(row.cash),
        report);
    return false;
  }

  return true;
}


bool MarginRiskEngine::CheckGrossExposureCap(
    const MarginRiskInputRow& row,
    MarginRiskReport* report) const {
  const std::string rule = "gross_exposure_cap";
  const double threshold = Threshold(rule, 0.250000);

  if (row.gross_exposure < threshold) {
    AddIssue(
        MarginRiskIssueType::kFloorBreached,
        MarginRiskSeverity::kWarning,
        row,
        rule,
        "GrossExposureCap breached configured validation rule",
        "metric above configured floor",
        NumberText(row.gross_exposure),
        report);
    return false;
  }

  return true;
}


bool MarginRiskEngine::CheckPortfolioBetaCap(
    const MarginRiskInputRow& row,
    MarginRiskReport* report) const {
  const std::string rule = "portfolio_beta_cap";
  const double threshold = Threshold(rule, 0.325000);

  if (SafeRatio(row.net_exposure, row.settlement_amount) > threshold) {
    AddIssue(
        MarginRiskIssueType::kRatioViolation,
        MarginRiskSeverity::kError,
        row,
        rule,
        "PortfolioBetaCap breached configured validation rule",
        "ratio below configured threshold",
        NumberText(SafeRatio(row.net_exposure, row.settlement_amount)),
        report);
    return false;
  }

  return true;
}


bool MarginRiskEngine::CheckCorrelationClusterCap(
    const MarginRiskInputRow& row,
    MarginRiskReport* report) const {
  const std::string rule = "correlation_cluster_cap";
  const double threshold = Threshold(rule, 0.400000);

  if (row.restricted && !allow_manual_overrides_) {
    AddIssue(
        MarginRiskIssueType::kPolicyViolation,
        MarginRiskSeverity::kCritical,
        row,
        rule,
        "CorrelationClusterCap breached configured validation rule",
        "unrestricted row or approved override",
        "restricted row without override",
        report);
    return false;
  }

  return true;
}


bool MarginRiskEngine::CheckShortInventoryCap(
    const MarginRiskInputRow& row,
    MarginRiskReport* report) const {
  const std::string rule = "short_inventory_cap";
  const double threshold = Threshold(rule, 0.475000);

  if (strict_mode_ && std::fabs(row.confidence - row.available_liquidity) > threshold) {
    AddIssue(
        MarginRiskIssueType::kScenarioFailure,
        MarginRiskSeverity::kWarning,
        row,
        rule,
        "ShortInventoryCap breached configured validation rule",
        "strict-mode metric difference inside threshold",
        NumberText(row.confidence - row.available_liquidity),
        report);
    return false;
  }

  return true;
}


bool MarginRiskEngine::CheckOptionPremiumCap(
    const MarginRiskInputRow& row,
    MarginRiskReport* report) const {
  const std::string rule = "option_premium_cap";
  const double threshold = Threshold(rule, 0.550000);

  if (std::fabs(row.latency_ms) > threshold) {
    AddIssue(
        MarginRiskIssueType::kLimitExceeded,
        MarginRiskSeverity::kError,
        row,
        rule,
        "OptionPremiumCap breached configured validation rule",
        "absolute metric below threshold",
        NumberText(row.latency_ms),
        report);
    return false;
  }

  return true;
}


bool MarginRiskEngine::CheckFutureSpanMarginCap(
    const MarginRiskInputRow& row,
    MarginRiskReport* report) const {
  const std::string rule = "future_span_margin_cap";
  const double threshold = Threshold(rule, 0.625000);

  if (row.fill_rate < threshold) {
    AddIssue(
        MarginRiskIssueType::kFloorBreached,
        MarginRiskSeverity::kWarning,
        row,
        rule,
        "FutureSpanMarginCap breached configured validation rule",
        "metric above configured floor",
        NumberText(row.fill_rate),
        report);
    return false;
  }

  return true;
}


bool MarginRiskEngine::CheckCrossMarginOffsetCap(
    const MarginRiskInputRow& row,
    MarginRiskReport* report) const {
  const std::string rule = "cross_margin_offset_cap";
  const double threshold = Threshold(rule, 0.700000);

  if (SafeRatio(row.reject_rate, row.beta) > threshold) {
    AddIssue(
        MarginRiskIssueType::kRatioViolation,
        MarginRiskSeverity::kError,
        row,
        rule,
        "CrossMarginOffsetCap breached configured validation rule",
        "ratio below configured threshold",
        NumberText(SafeRatio(row.reject_rate, row.beta)),
        report);
    return false;
  }

  return true;
}


bool MarginRiskEngine::CheckCurrencyHaircutFloor(
    const MarginRiskInputRow& row,
    MarginRiskReport* report) const {
  const std::string rule = "currency_haircut_floor";
  const double threshold = Threshold(rule, 0.775000);

  if (row.restricted && !allow_manual_overrides_) {
    AddIssue(
        MarginRiskIssueType::kPolicyViolation,
        MarginRiskSeverity::kCritical,
        row,
        rule,
        "CurrencyHaircutFloor breached configured validation rule",
        "unrestricted row or approved override",
        "restricted row without override",
        report);
    return false;
  }

  return true;
}


bool MarginRiskEngine::CheckLiquidationBufferFloor(
    const MarginRiskInputRow& row,
    MarginRiskReport* report) const {
  const std::string rule = "liquidation_buffer_floor";
  const double threshold = Threshold(rule, 0.850000);

  if (strict_mode_ && std::fabs(row.settlement_amount - row.var_10d) > threshold) {
    AddIssue(
        MarginRiskIssueType::kScenarioFailure,
        MarginRiskSeverity::kWarning,
        row,
        rule,
        "LiquidationBufferFloor breached configured validation rule",
        "strict-mode metric difference inside threshold",
        NumberText(row.settlement_amount - row.var_10d),
        report);
    return false;
  }

  return true;
}


bool MarginRiskEngine::CheckIntradayMarginCap(
    const MarginRiskInputRow& row,
    MarginRiskReport* report) const {
  const std::string rule = "intraday_margin_cap";
  const double threshold = Threshold(rule, 0.925000);

  if (std::fabs(row.expected_amount) > threshold) {
    AddIssue(
        MarginRiskIssueType::kLimitExceeded,
        MarginRiskSeverity::kError,
        row,
        rule,
        "IntradayMarginCap breached configured validation rule",
        "absolute metric below threshold",
        NumberText(row.expected_amount),
        report);
    return false;
  }

  return true;
}


bool MarginRiskEngine::CheckOvernightMarginCap(
    const MarginRiskInputRow& row,
    MarginRiskReport* report) const {
  const std::string rule = "overnight_margin_cap";
  const double threshold = Threshold(rule, 1.000000);

  if (row.available_liquidity < threshold) {
    AddIssue(
        MarginRiskIssueType::kFloorBreached,
        MarginRiskSeverity::kWarning,
        row,
        rule,
        "OvernightMarginCap breached configured validation rule",
        "metric above configured floor",
        NumberText(row.available_liquidity),
        report);
    return false;
  }

  return true;
}


bool MarginRiskEngine::CheckHouseMarginAddOnCap(
    const MarginRiskInputRow& row,
    MarginRiskReport* report) const {
  const std::string rule = "house_margin_add_on_cap";
  const double threshold = Threshold(rule, 0.250000);

  if (SafeRatio(row.correlation, row.fee_rate) > threshold) {
    AddIssue(
        MarginRiskIssueType::kRatioViolation,
        MarginRiskSeverity::kError,
        row,
        rule,
        "HouseMarginAddOnCap breached configured validation rule",
        "ratio below configured threshold",
        NumberText(SafeRatio(row.correlation, row.fee_rate)),
        report);
    return false;
  }

  return true;
}


bool MarginRiskEngine::CheckRegulatoryMarginFloor(
    const MarginRiskInputRow& row,
    MarginRiskReport* report) const {
  const std::string rule = "regulatory_margin_floor";
  const double threshold = Threshold(rule, 0.325000);

  if (row.restricted && !allow_manual_overrides_) {
    AddIssue(
        MarginRiskIssueType::kPolicyViolation,
        MarginRiskSeverity::kCritical,
        row,
        rule,
        "RegulatoryMarginFloor breached configured validation rule",
        "unrestricted row or approved override",
        "restricted row without override",
        report);
    return false;
  }

  return true;
}


bool MarginRiskEngine::CheckRiskWeightCap(
    const MarginRiskInputRow& row,
    MarginRiskReport* report) const {
  const std::string rule = "risk_weight_cap";
  const double threshold = Threshold(rule, 0.400000);

  if (strict_mode_ && std::fabs(row.beta - row.turnover_rate) > threshold) {
    AddIssue(
        MarginRiskIssueType::kScenarioFailure,
        MarginRiskSeverity::kWarning,
        row,
        rule,
        "RiskWeightCap breached configured validation rule",
        "strict-mode metric difference inside threshold",
        NumberText(row.beta - row.turnover_rate),
        report);
    return false;
  }

  return true;
}


bool MarginRiskEngine::CheckDeltaAdjustedExposureCap(
    const MarginRiskInputRow& row,
    MarginRiskReport* report) const {
  const std::string rule = "delta_adjusted_exposure_cap";
  const double threshold = Threshold(rule, 0.475000);

  if (std::fabs(row.var_1d) > threshold) {
    AddIssue(
        MarginRiskIssueType::kLimitExceeded,
        MarginRiskSeverity::kError,
        row,
        rule,
        "DeltaAdjustedExposureCap breached configured validation rule",
        "absolute metric below threshold",
        NumberText(row.var_1d),
        report);
    return false;
  }

  return true;
}


bool MarginRiskEngine::CheckGammaNotionalCap(
    const MarginRiskInputRow& row,
    MarginRiskReport* report) const {
  const std::string rule = "gamma_notional_cap";
  const double threshold = Threshold(rule, 0.550000);

  if (row.var_10d < threshold) {
    AddIssue(
        MarginRiskIssueType::kFloorBreached,
        MarginRiskSeverity::kWarning,
        row,
        rule,
        "GammaNotionalCap breached configured validation rule",
        "metric above configured floor",
        NumberText(row.var_10d),
        report);
    return false;
  }

  return true;
}


bool MarginRiskEngine::CheckVegaNotionalCap(
    const MarginRiskInputRow& row,
    MarginRiskReport* report) const {
  const std::string rule = "vega_notional_cap";
  const double threshold = Threshold(rule, 0.625000);

  if (SafeRatio(row.haircut, row.quantity) > threshold) {
    AddIssue(
        MarginRiskIssueType::kRatioViolation,
        MarginRiskSeverity::kError,
        row,
        rule,
        "VegaNotionalCap breached configured validation rule",
        "ratio below configured threshold",
        NumberText(SafeRatio(row.haircut, row.quantity)),
        report);
    return false;
  }

  return true;
}


bool MarginRiskEngine::CheckThetaDecayCap(
    const MarginRiskInputRow& row,
    MarginRiskReport* report) const {
  const std::string rule = "theta_decay_cap";
  const double threshold = Threshold(rule, 0.700000);

  if (row.restricted && !allow_manual_overrides_) {
    AddIssue(
        MarginRiskIssueType::kPolicyViolation,
        MarginRiskSeverity::kCritical,
        row,
        rule,
        "ThetaDecayCap breached configured validation rule",
        "unrestricted row or approved override",
        "restricted row without override",
        report);
    return false;
  }

  return true;
}


bool MarginRiskEngine::CheckStressLiquidityCap(
    const MarginRiskInputRow& row,
    MarginRiskReport* report) const {
  const std::string rule = "stress_liquidity_cap";
  const double threshold = Threshold(rule, 0.775000);

  if (strict_mode_ && std::fabs(row.fee_rate - row.volatility) > threshold) {
    AddIssue(
        MarginRiskIssueType::kScenarioFailure,
        MarginRiskSeverity::kWarning,
        row,
        rule,
        "StressLiquidityCap breached configured validation rule",
        "strict-mode metric difference inside threshold",
        NumberText(row.fee_rate - row.volatility),
        report);
    return false;
  }

  return true;
}


bool MarginRiskEngine::CheckCollateralUtilizationCap(
    const MarginRiskInputRow& row,
    MarginRiskReport* report) const {
  const std::string rule = "collateral_utilization_cap";
  const double threshold = Threshold(rule, 0.850000);

  if (std::fabs(row.slippage_bps) > threshold) {
    AddIssue(
        MarginRiskIssueType::kLimitExceeded,
        MarginRiskSeverity::kError,
        row,
        rule,
        "CollateralUtilizationCap breached configured validation rule",
        "absolute metric below threshold",
        NumberText(row.slippage_bps),
        report);
    return false;
  }

  return true;
}


bool MarginRiskEngine::CheckFreeEquityFloor(
    const MarginRiskInputRow& row,
    MarginRiskReport* report) const {
  const std::string rule = "free_equity_floor";
  const double threshold = Threshold(rule, 0.925000);

  if (row.turnover_rate < threshold) {
    AddIssue(
        MarginRiskIssueType::kFloorBreached,
        MarginRiskSeverity::kWarning,
        row,
        rule,
        "FreeEquityFloor breached configured validation rule",
        "metric above configured floor",
        NumberText(row.turnover_rate),
        report);
    return false;
  }

  return true;
}


bool MarginRiskEngine::CheckDrawdownCap(
    const MarginRiskInputRow& row,
    MarginRiskReport* report) const {
  const std::string rule = "drawdown_cap";
  const double threshold = Threshold(rule, 1.000000);

  if (SafeRatio(row.notional, row.margin_rate) > threshold) {
    AddIssue(
        MarginRiskIssueType::kRatioViolation,
        MarginRiskSeverity::kError,
        row,
        rule,
        "DrawdownCap breached configured validation rule",
        "ratio below configured threshold",
        NumberText(SafeRatio(row.notional, row.margin_rate)),
        report);
    return false;
  }

  return true;
}


bool MarginRiskEngine::CheckSingleIssuerCap(
    const MarginRiskInputRow& row,
    MarginRiskReport* report) const {
  const std::string rule = "single_issuer_cap";
  const double threshold = Threshold(rule, 0.250000);

  if (row.restricted && !allow_manual_overrides_) {
    AddIssue(
        MarginRiskIssueType::kPolicyViolation,
        MarginRiskSeverity::kCritical,
        row,
        rule,
        "SingleIssuerCap breached configured validation rule",
        "unrestricted row or approved override",
        "restricted row without override",
        report);
    return false;
  }

  return true;
}


bool MarginRiskEngine::CheckSectorExposureCap(
    const MarginRiskInputRow& row,
    MarginRiskReport* report) const {
  const std::string rule = "sector_exposure_cap";
  const double threshold = Threshold(rule, 0.325000);

  if (strict_mode_ && std::fabs(row.quantity - row.unrealized_pnl) > threshold) {
    AddIssue(
        MarginRiskIssueType::kScenarioFailure,
        MarginRiskSeverity::kWarning,
        row,
        rule,
        "SectorExposureCap breached configured validation rule",
        "strict-mode metric difference inside threshold",
        NumberText(row.quantity - row.unrealized_pnl),
        report);
    return false;
  }

  return true;
}


bool MarginRiskEngine::CheckCountryExposureCap(
    const MarginRiskInputRow& row,
    MarginRiskReport* report) const {
  const std::string rule = "country_exposure_cap";
  const double threshold = Threshold(rule, 0.400000);

  if (std::fabs(row.price) > threshold) {
    AddIssue(
        MarginRiskIssueType::kLimitExceeded,
        MarginRiskSeverity::kError,
        row,
        rule,
        "CountryExposureCap breached configured validation rule",
        "absolute metric below threshold",
        NumberText(row.price),
        report);
    return false;
  }

  return true;
}


bool MarginRiskEngine::CheckRestrictedAssetCap(
    const MarginRiskInputRow& row,
    MarginRiskReport* report) const {
  const std::string rule = "restricted_asset_cap";
  const double threshold = Threshold(rule, 0.475000);

  if (row.volatility < threshold) {
    AddIssue(
        MarginRiskIssueType::kFloorBreached,
        MarginRiskSeverity::kWarning,
        row,
        rule,
        "RestrictedAssetCap breached configured validation rule",
        "metric above configured floor",
        NumberText(row.volatility),
        report);
    return false;
  }

  return true;
}


bool MarginRiskEngine::CheckUnsettledCashCap(
    const MarginRiskInputRow& row,
    MarginRiskReport* report) const {
  const std::string rule = "unsettled_cash_cap";
  const double threshold = Threshold(rule, 0.550000);

  if (SafeRatio(row.average_daily_volume, row.net_exposure) > threshold) {
    AddIssue(
        MarginRiskIssueType::kRatioViolation,
        MarginRiskSeverity::kError,
        row,
        rule,
        "UnsettledCashCap breached configured validation rule",
        "ratio below configured threshold",
        NumberText(SafeRatio(row.average_daily_volume, row.net_exposure)),
        report);
    return false;
  }

  return true;
}


bool MarginRiskEngine::CheckFailToDeliverCap(
    const MarginRiskInputRow& row,
    MarginRiskReport* report) const {
  const std::string rule = "fail_to_deliver_cap";
  const double threshold = Threshold(rule, 0.625000);

  if (row.restricted && !allow_manual_overrides_) {
    AddIssue(
        MarginRiskIssueType::kPolicyViolation,
        MarginRiskSeverity::kCritical,
        row,
        rule,
        "FailToDeliverCap breached configured validation rule",
        "unrestricted row or approved override",
        "restricted row without override",
        report);
    return false;
  }

  return true;
}


bool MarginRiskEngine::CheckCorporateActionRiskCap(
    const MarginRiskInputRow& row,
    MarginRiskReport* report) const {
  const std::string rule = "corporate_action_risk_cap";
  const double threshold = Threshold(rule, 0.700000);

  if (strict_mode_ && std::fabs(row.margin_rate - row.confidence) > threshold) {
    AddIssue(
        MarginRiskIssueType::kScenarioFailure,
        MarginRiskSeverity::kWarning,
        row,
        rule,
        "CorporateActionRiskCap breached configured validation rule",
        "strict-mode metric difference inside threshold",
        NumberText(row.margin_rate - row.confidence),
        report);
    return false;
  }

  return true;
}


std::string MarginRiskSeverityName(MarginRiskSeverity severity) {
  switch (severity) {
    case MarginRiskSeverity::kInfo:
      return "info";
    case MarginRiskSeverity::kWarning:
      return "warning";
    case MarginRiskSeverity::kError:
      return "error";
    case MarginRiskSeverity::kCritical:
      return "critical";
  }
  return "unknown";
}

std::string MarginRiskIssueTypeName(MarginRiskIssueType type) {
  switch (type) {
    case MarginRiskIssueType::kInvalidInput:
      return "invalid_input";
    case MarginRiskIssueType::kMissingIdentifier:
      return "missing_identifier";
    case MarginRiskIssueType::kNonFiniteMetric:
      return "non_finite_metric";
    case MarginRiskIssueType::kThresholdBreach:
      return "threshold_breach";
    case MarginRiskIssueType::kLimitExceeded:
      return "limit_exceeded";
    case MarginRiskIssueType::kFloorBreached:
      return "floor_breached";
    case MarginRiskIssueType::kRatioViolation:
      return "ratio_violation";
    case MarginRiskIssueType::kPolicyViolation:
      return "policy_violation";
    case MarginRiskIssueType::kScenarioFailure:
      return "scenario_failure";
    case MarginRiskIssueType::kAggregationFailure:
      return "aggregation_failure";
  }
  return "unknown";
}

}  // namespace marketdata
