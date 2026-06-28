
#include "position_limit_engine.h"

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

bool IsError(PositionLimitSeverity severity) {
  return severity == PositionLimitSeverity::kError ||
         severity == PositionLimitSeverity::kCritical;
}

double SafeRatio(double numerator, double denominator) {
  if (!IsFinite(numerator) || !IsFinite(denominator) ||
      std::fabs(denominator) < 0.0000001) {
    return std::numeric_limits<double>::infinity();
  }
  return std::fabs(numerator / denominator);
}

}  // namespace

std::string PositionLimitInputRow::Key() const {
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

bool PositionLimitReport::Ok() const {
  for (const PositionLimitIssue& issue : issues) {
    if (IsError(issue.severity)) {
      return false;
    }
  }
  return true;
}

std::uint64_t PositionLimitReport::ErrorCount() const {
  std::uint64_t total = 0;
  for (const PositionLimitIssue& issue : issues) {
    if (IsError(issue.severity)) {
      ++total;
    }
  }
  return total;
}

std::uint64_t PositionLimitReport::WarningCount() const {
  std::uint64_t total = 0;
  for (const PositionLimitIssue& issue : issues) {
    if (issue.severity == PositionLimitSeverity::kWarning) {
      ++total;
    }
  }
  return total;
}

PositionLimitEngine::PositionLimitEngine()
    : strict_mode_(false),
      allow_manual_overrides_(false) {
  thresholds_["single_name_limit"] = 0.250000;
  thresholds_["sector_limit"] = 0.325000;
  thresholds_["country_limit"] = 0.400000;
  thresholds_["currency_limit"] = 0.475000;
  thresholds_["venue_limit"] = 0.550000;
  thresholds_["strategy_limit"] = 0.625000;
  thresholds_["account_limit"] = 0.700000;
  thresholds_["portfolio_limit"] = 0.775000;
  thresholds_["issuer_limit"] = 0.850000;
  thresholds_["asset_class_limit"] = 0.925000;
  thresholds_["beta_limit"] = 1.000000;
  thresholds_["delta_limit"] = 0.250000;
  thresholds_["gamma_limit"] = 0.325000;
  thresholds_["vega_limit"] = 0.400000;
  thresholds_["theta_limit"] = 0.475000;
  thresholds_["rho_limit"] = 0.550000;
  thresholds_["duration_limit"] = 0.625000;
  thresholds_["d_v01_limit"] = 0.700000;
  thresholds_["convexity_limit"] = 0.775000;
  thresholds_["liquidity_bucket_limit"] = 0.850000;
  thresholds_["concentration_bucket_limit"] = 0.925000;
  thresholds_["restricted_list_limit"] = 1.000000;
  thresholds_["watch_list_limit"] = 0.250000;
  thresholds_["short_sale_limit"] = 0.325000;
  thresholds_["locate_required_limit"] = 0.400000;
  thresholds_["borrow_availability_limit"] = 0.475000;
  thresholds_["hard_to_borrow_limit"] = 0.550000;
  thresholds_["unsettled_position_limit"] = 0.625000;
  thresholds_["aged_fail_limit"] = 0.700000;
  thresholds_["corporate_action_limit"] = 0.775000;
  thresholds_["earnings_window_limit"] = 0.850000;
  thresholds_["event_risk_limit"] = 0.925000;
  thresholds_["open_order_limit"] = 1.000000;
  thresholds_["pending_cancel_limit"] = 0.250000;
  thresholds_["pending_replace_limit"] = 0.325000;
  thresholds_["intraday_turnover_limit"] = 0.400000;
  thresholds_["overnight_inventory_limit"] = 0.475000;
  thresholds_["manual_override_limit"] = 0.550000;
  thresholds_["client_mandate_limit"] = 0.625000;
  thresholds_["regulatory_limit"] = 0.700000;
}

void PositionLimitEngine::SetThreshold(const std::string& rule, double value) {
  if (!IsFinite(value) || value < 0.0) {
    throw std::invalid_argument("threshold must be finite and non-negative");
  }
  thresholds_[rule] = value;
}

void PositionLimitEngine::SetStrictMode(bool value) {
  strict_mode_ = value;
}

void PositionLimitEngine::SetAllowManualOverrides(bool value) {
  allow_manual_overrides_ = value;
}

PositionLimitReport PositionLimitEngine::Analyze(
    const std::vector<PositionLimitInputRow>& rows) const {
  PositionLimitReport report;

  for (const PositionLimitInputRow& row : rows) {
    ++report.checked_rows;
    report.gross_notional += std::fabs(row.notional);
    report.net_notional += row.notional;
    report.total_stress_loss += row.stress_loss;
    report.max_abs_exposure =
        std::max(report.max_abs_exposure, std::fabs(row.gross_exposure));

    if (!ValidateCommonFields(row, &report)) {
      continue;
    }

    CheckSingleNameLimit(row, &report);
    CheckSectorLimit(row, &report);
    CheckCountryLimit(row, &report);
    CheckCurrencyLimit(row, &report);
    CheckVenueLimit(row, &report);
    CheckStrategyLimit(row, &report);
    CheckAccountLimit(row, &report);
    CheckPortfolioLimit(row, &report);
    CheckIssuerLimit(row, &report);
    CheckAssetClassLimit(row, &report);
    CheckBetaLimit(row, &report);
    CheckDeltaLimit(row, &report);
    CheckGammaLimit(row, &report);
    CheckVegaLimit(row, &report);
    CheckThetaLimit(row, &report);
    CheckRhoLimit(row, &report);
    CheckDurationLimit(row, &report);
    CheckDV01Limit(row, &report);
    CheckConvexityLimit(row, &report);
    CheckLiquidityBucketLimit(row, &report);
    CheckConcentrationBucketLimit(row, &report);
    CheckRestrictedListLimit(row, &report);
    CheckWatchListLimit(row, &report);
    CheckShortSaleLimit(row, &report);
    CheckLocateRequiredLimit(row, &report);
    CheckBorrowAvailabilityLimit(row, &report);
    CheckHardToBorrowLimit(row, &report);
    CheckUnsettledPositionLimit(row, &report);
    CheckAgedFailLimit(row, &report);
    CheckCorporateActionLimit(row, &report);
    CheckEarningsWindowLimit(row, &report);
    CheckEventRiskLimit(row, &report);
    CheckOpenOrderLimit(row, &report);
    CheckPendingCancelLimit(row, &report);
    CheckPendingReplaceLimit(row, &report);
    CheckIntradayTurnoverLimit(row, &report);
    CheckOvernightInventoryLimit(row, &report);
    CheckManualOverrideLimit(row, &report);
    CheckClientMandateLimit(row, &report);
    CheckRegulatoryLimit(row, &report);
  }

  report.error_rows = report.ErrorCount();
  report.warning_rows = report.WarningCount();
  return report;
}

double PositionLimitEngine::Threshold(
    const std::string& rule,
    double fallback) const {
  const auto it = thresholds_.find(rule);
  if (it == thresholds_.end()) {
    return fallback;
  }
  return it->second;
}

bool PositionLimitEngine::ValidateCommonFields(
    const PositionLimitInputRow& row,
    PositionLimitReport* report) const {
  bool valid = true;

  if (row.portfolio_id.empty() || row.account_id.empty()) {
    ++report->invalid_rows;
    AddIssue(
        PositionLimitIssueType::kMissingIdentifier,
        PositionLimitSeverity::kError,
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
        PositionLimitIssueType::kNonFiniteMetric,
        PositionLimitSeverity::kError,
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
        PositionLimitIssueType::kPolicyViolation,
        PositionLimitSeverity::kWarning,
        row,
        "manual_override",
        "manual override present but overrides disabled",
        "no override",
        "manual override",
        report);
  }

  return valid;
}

void PositionLimitEngine::AddIssue(
    PositionLimitIssueType type,
    PositionLimitSeverity severity,
    const PositionLimitInputRow& row,
    const std::string& rule,
    const std::string& message,
    const std::string& expected,
    const std::string& actual,
    PositionLimitReport* report) const {
  report->issues.push_back(PositionLimitIssue{
      type,
      severity,
      row.Key(),
      rule,
      message,
      expected,
      actual,
  });
}


bool PositionLimitEngine::CheckSingleNameLimit(
    const PositionLimitInputRow& row,
    PositionLimitReport* report) const {
  const std::string rule = "single_name_limit";
  const double threshold = Threshold(rule, 0.250000);

  if (std::fabs(row.notional) > threshold) {
    AddIssue(
        PositionLimitIssueType::kLimitExceeded,
        PositionLimitSeverity::kError,
        row,
        rule,
        "SingleNameLimit breached configured validation rule",
        "absolute metric below threshold",
        NumberText(row.notional),
        report);
    return false;
  }

  return true;
}


bool PositionLimitEngine::CheckSectorLimit(
    const PositionLimitInputRow& row,
    PositionLimitReport* report) const {
  const std::string rule = "sector_limit";
  const double threshold = Threshold(rule, 0.325000);

  if (row.market_value < threshold) {
    AddIssue(
        PositionLimitIssueType::kFloorBreached,
        PositionLimitSeverity::kWarning,
        row,
        rule,
        "SectorLimit breached configured validation rule",
        "metric above configured floor",
        NumberText(row.market_value),
        report);
    return false;
  }

  return true;
}


bool PositionLimitEngine::CheckCountryLimit(
    const PositionLimitInputRow& row,
    PositionLimitReport* report) const {
  const std::string rule = "country_limit";
  const double threshold = Threshold(rule, 0.400000);

  if (SafeRatio(row.quantity, row.unrealized_pnl) > threshold) {
    AddIssue(
        PositionLimitIssueType::kRatioViolation,
        PositionLimitSeverity::kError,
        row,
        rule,
        "CountryLimit breached configured validation rule",
        "ratio below configured threshold",
        NumberText(SafeRatio(row.quantity, row.unrealized_pnl)),
        report);
    return false;
  }

  return true;
}


bool PositionLimitEngine::CheckCurrencyLimit(
    const PositionLimitInputRow& row,
    PositionLimitReport* report) const {
  const std::string rule = "currency_limit";
  const double threshold = Threshold(rule, 0.475000);

  if (row.restricted && !allow_manual_overrides_) {
    AddIssue(
        PositionLimitIssueType::kPolicyViolation,
        PositionLimitSeverity::kCritical,
        row,
        rule,
        "CurrencyLimit breached configured validation rule",
        "unrestricted row or approved override",
        "restricted row without override",
        report);
    return false;
  }

  return true;
}


bool PositionLimitEngine::CheckVenueLimit(
    const PositionLimitInputRow& row,
    PositionLimitReport* report) const {
  const std::string rule = "venue_limit";
  const double threshold = Threshold(rule, 0.550000);

  if (strict_mode_ && std::fabs(row.volatility - row.gross_exposure) > threshold) {
    AddIssue(
        PositionLimitIssueType::kScenarioFailure,
        PositionLimitSeverity::kWarning,
        row,
        rule,
        "VenueLimit breached configured validation rule",
        "strict-mode metric difference inside threshold",
        NumberText(row.volatility - row.gross_exposure),
        report);
    return false;
  }

  return true;
}


bool PositionLimitEngine::CheckStrategyLimit(
    const PositionLimitInputRow& row,
    PositionLimitReport* report) const {
  const std::string rule = "strategy_limit";
  const double threshold = Threshold(rule, 0.625000);

  if (std::fabs(row.average_daily_volume) > threshold) {
    AddIssue(
        PositionLimitIssueType::kLimitExceeded,
        PositionLimitSeverity::kError,
        row,
        rule,
        "StrategyLimit breached configured validation rule",
        "absolute metric below threshold",
        NumberText(row.average_daily_volume),
        report);
    return false;
  }

  return true;
}


bool PositionLimitEngine::CheckAccountLimit(
    const PositionLimitInputRow& row,
    PositionLimitReport* report) const {
  const std::string rule = "account_limit";
  const double threshold = Threshold(rule, 0.700000);

  if (row.spread_bps < threshold) {
    AddIssue(
        PositionLimitIssueType::kFloorBreached,
        PositionLimitSeverity::kWarning,
        row,
        rule,
        "AccountLimit breached configured validation rule",
        "metric above configured floor",
        NumberText(row.spread_bps),
        report);
    return false;
  }

  return true;
}


bool PositionLimitEngine::CheckPortfolioLimit(
    const PositionLimitInputRow& row,
    PositionLimitReport* report) const {
  const std::string rule = "portfolio_limit";
  const double threshold = Threshold(rule, 0.775000);

  if (SafeRatio(row.margin_rate, row.confidence) > threshold) {
    AddIssue(
        PositionLimitIssueType::kRatioViolation,
        PositionLimitSeverity::kError,
        row,
        rule,
        "PortfolioLimit breached configured validation rule",
        "ratio below configured threshold",
        NumberText(SafeRatio(row.margin_rate, row.confidence)),
        report);
    return false;
  }

  return true;
}


bool PositionLimitEngine::CheckIssuerLimit(
    const PositionLimitInputRow& row,
    PositionLimitReport* report) const {
  const std::string rule = "issuer_limit";
  const double threshold = Threshold(rule, 0.850000);

  if (row.restricted && !allow_manual_overrides_) {
    AddIssue(
        PositionLimitIssueType::kPolicyViolation,
        PositionLimitSeverity::kCritical,
        row,
        rule,
        "IssuerLimit breached configured validation rule",
        "unrestricted row or approved override",
        "restricted row without override",
        report);
    return false;
  }

  return true;
}


bool PositionLimitEngine::CheckAssetClassLimit(
    const PositionLimitInputRow& row,
    PositionLimitReport* report) const {
  const std::string rule = "asset_class_limit";
  const double threshold = Threshold(rule, 0.925000);

  if (strict_mode_ && std::fabs(row.unrealized_pnl - row.fill_rate) > threshold) {
    AddIssue(
        PositionLimitIssueType::kScenarioFailure,
        PositionLimitSeverity::kWarning,
        row,
        rule,
        "AssetClassLimit breached configured validation rule",
        "strict-mode metric difference inside threshold",
        NumberText(row.unrealized_pnl - row.fill_rate),
        report);
    return false;
  }

  return true;
}


bool PositionLimitEngine::CheckBetaLimit(
    const PositionLimitInputRow& row,
    PositionLimitReport* report) const {
  const std::string rule = "beta_limit";
  const double threshold = Threshold(rule, 1.000000);

  if (std::fabs(row.cash) > threshold) {
    AddIssue(
        PositionLimitIssueType::kLimitExceeded,
        PositionLimitSeverity::kError,
        row,
        rule,
        "BetaLimit breached configured validation rule",
        "absolute metric below threshold",
        NumberText(row.cash),
        report);
    return false;
  }

  return true;
}


bool PositionLimitEngine::CheckDeltaLimit(
    const PositionLimitInputRow& row,
    PositionLimitReport* report) const {
  const std::string rule = "delta_limit";
  const double threshold = Threshold(rule, 0.250000);

  if (row.gross_exposure < threshold) {
    AddIssue(
        PositionLimitIssueType::kFloorBreached,
        PositionLimitSeverity::kWarning,
        row,
        rule,
        "DeltaLimit breached configured validation rule",
        "metric above configured floor",
        NumberText(row.gross_exposure),
        report);
    return false;
  }

  return true;
}


bool PositionLimitEngine::CheckGammaLimit(
    const PositionLimitInputRow& row,
    PositionLimitReport* report) const {
  const std::string rule = "gamma_limit";
  const double threshold = Threshold(rule, 0.325000);

  if (SafeRatio(row.net_exposure, row.settlement_amount) > threshold) {
    AddIssue(
        PositionLimitIssueType::kRatioViolation,
        PositionLimitSeverity::kError,
        row,
        rule,
        "GammaLimit breached configured validation rule",
        "ratio below configured threshold",
        NumberText(SafeRatio(row.net_exposure, row.settlement_amount)),
        report);
    return false;
  }

  return true;
}


bool PositionLimitEngine::CheckVegaLimit(
    const PositionLimitInputRow& row,
    PositionLimitReport* report) const {
  const std::string rule = "vega_limit";
  const double threshold = Threshold(rule, 0.400000);

  if (row.restricted && !allow_manual_overrides_) {
    AddIssue(
        PositionLimitIssueType::kPolicyViolation,
        PositionLimitSeverity::kCritical,
        row,
        rule,
        "VegaLimit breached configured validation rule",
        "unrestricted row or approved override",
        "restricted row without override",
        report);
    return false;
  }

  return true;
}


bool PositionLimitEngine::CheckThetaLimit(
    const PositionLimitInputRow& row,
    PositionLimitReport* report) const {
  const std::string rule = "theta_limit";
  const double threshold = Threshold(rule, 0.475000);

  if (strict_mode_ && std::fabs(row.confidence - row.available_liquidity) > threshold) {
    AddIssue(
        PositionLimitIssueType::kScenarioFailure,
        PositionLimitSeverity::kWarning,
        row,
        rule,
        "ThetaLimit breached configured validation rule",
        "strict-mode metric difference inside threshold",
        NumberText(row.confidence - row.available_liquidity),
        report);
    return false;
  }

  return true;
}


bool PositionLimitEngine::CheckRhoLimit(
    const PositionLimitInputRow& row,
    PositionLimitReport* report) const {
  const std::string rule = "rho_limit";
  const double threshold = Threshold(rule, 0.550000);

  if (std::fabs(row.latency_ms) > threshold) {
    AddIssue(
        PositionLimitIssueType::kLimitExceeded,
        PositionLimitSeverity::kError,
        row,
        rule,
        "RhoLimit breached configured validation rule",
        "absolute metric below threshold",
        NumberText(row.latency_ms),
        report);
    return false;
  }

  return true;
}


bool PositionLimitEngine::CheckDurationLimit(
    const PositionLimitInputRow& row,
    PositionLimitReport* report) const {
  const std::string rule = "duration_limit";
  const double threshold = Threshold(rule, 0.625000);

  if (row.fill_rate < threshold) {
    AddIssue(
        PositionLimitIssueType::kFloorBreached,
        PositionLimitSeverity::kWarning,
        row,
        rule,
        "DurationLimit breached configured validation rule",
        "metric above configured floor",
        NumberText(row.fill_rate),
        report);
    return false;
  }

  return true;
}


bool PositionLimitEngine::CheckDV01Limit(
    const PositionLimitInputRow& row,
    PositionLimitReport* report) const {
  const std::string rule = "d_v01_limit";
  const double threshold = Threshold(rule, 0.700000);

  if (SafeRatio(row.reject_rate, row.beta) > threshold) {
    AddIssue(
        PositionLimitIssueType::kRatioViolation,
        PositionLimitSeverity::kError,
        row,
        rule,
        "DV01Limit breached configured validation rule",
        "ratio below configured threshold",
        NumberText(SafeRatio(row.reject_rate, row.beta)),
        report);
    return false;
  }

  return true;
}


bool PositionLimitEngine::CheckConvexityLimit(
    const PositionLimitInputRow& row,
    PositionLimitReport* report) const {
  const std::string rule = "convexity_limit";
  const double threshold = Threshold(rule, 0.775000);

  if (row.restricted && !allow_manual_overrides_) {
    AddIssue(
        PositionLimitIssueType::kPolicyViolation,
        PositionLimitSeverity::kCritical,
        row,
        rule,
        "ConvexityLimit breached configured validation rule",
        "unrestricted row or approved override",
        "restricted row without override",
        report);
    return false;
  }

  return true;
}


bool PositionLimitEngine::CheckLiquidityBucketLimit(
    const PositionLimitInputRow& row,
    PositionLimitReport* report) const {
  const std::string rule = "liquidity_bucket_limit";
  const double threshold = Threshold(rule, 0.850000);

  if (strict_mode_ && std::fabs(row.settlement_amount - row.var_10d) > threshold) {
    AddIssue(
        PositionLimitIssueType::kScenarioFailure,
        PositionLimitSeverity::kWarning,
        row,
        rule,
        "LiquidityBucketLimit breached configured validation rule",
        "strict-mode metric difference inside threshold",
        NumberText(row.settlement_amount - row.var_10d),
        report);
    return false;
  }

  return true;
}


bool PositionLimitEngine::CheckConcentrationBucketLimit(
    const PositionLimitInputRow& row,
    PositionLimitReport* report) const {
  const std::string rule = "concentration_bucket_limit";
  const double threshold = Threshold(rule, 0.925000);

  if (std::fabs(row.expected_amount) > threshold) {
    AddIssue(
        PositionLimitIssueType::kLimitExceeded,
        PositionLimitSeverity::kError,
        row,
        rule,
        "ConcentrationBucketLimit breached configured validation rule",
        "absolute metric below threshold",
        NumberText(row.expected_amount),
        report);
    return false;
  }

  return true;
}


bool PositionLimitEngine::CheckRestrictedListLimit(
    const PositionLimitInputRow& row,
    PositionLimitReport* report) const {
  const std::string rule = "restricted_list_limit";
  const double threshold = Threshold(rule, 1.000000);

  if (row.available_liquidity < threshold) {
    AddIssue(
        PositionLimitIssueType::kFloorBreached,
        PositionLimitSeverity::kWarning,
        row,
        rule,
        "RestrictedListLimit breached configured validation rule",
        "metric above configured floor",
        NumberText(row.available_liquidity),
        report);
    return false;
  }

  return true;
}


bool PositionLimitEngine::CheckWatchListLimit(
    const PositionLimitInputRow& row,
    PositionLimitReport* report) const {
  const std::string rule = "watch_list_limit";
  const double threshold = Threshold(rule, 0.250000);

  if (SafeRatio(row.correlation, row.fee_rate) > threshold) {
    AddIssue(
        PositionLimitIssueType::kRatioViolation,
        PositionLimitSeverity::kError,
        row,
        rule,
        "WatchListLimit breached configured validation rule",
        "ratio below configured threshold",
        NumberText(SafeRatio(row.correlation, row.fee_rate)),
        report);
    return false;
  }

  return true;
}


bool PositionLimitEngine::CheckShortSaleLimit(
    const PositionLimitInputRow& row,
    PositionLimitReport* report) const {
  const std::string rule = "short_sale_limit";
  const double threshold = Threshold(rule, 0.325000);

  if (row.restricted && !allow_manual_overrides_) {
    AddIssue(
        PositionLimitIssueType::kPolicyViolation,
        PositionLimitSeverity::kCritical,
        row,
        rule,
        "ShortSaleLimit breached configured validation rule",
        "unrestricted row or approved override",
        "restricted row without override",
        report);
    return false;
  }

  return true;
}


bool PositionLimitEngine::CheckLocateRequiredLimit(
    const PositionLimitInputRow& row,
    PositionLimitReport* report) const {
  const std::string rule = "locate_required_limit";
  const double threshold = Threshold(rule, 0.400000);

  if (strict_mode_ && std::fabs(row.beta - row.turnover_rate) > threshold) {
    AddIssue(
        PositionLimitIssueType::kScenarioFailure,
        PositionLimitSeverity::kWarning,
        row,
        rule,
        "LocateRequiredLimit breached configured validation rule",
        "strict-mode metric difference inside threshold",
        NumberText(row.beta - row.turnover_rate),
        report);
    return false;
  }

  return true;
}


bool PositionLimitEngine::CheckBorrowAvailabilityLimit(
    const PositionLimitInputRow& row,
    PositionLimitReport* report) const {
  const std::string rule = "borrow_availability_limit";
  const double threshold = Threshold(rule, 0.475000);

  if (std::fabs(row.var_1d) > threshold) {
    AddIssue(
        PositionLimitIssueType::kLimitExceeded,
        PositionLimitSeverity::kError,
        row,
        rule,
        "BorrowAvailabilityLimit breached configured validation rule",
        "absolute metric below threshold",
        NumberText(row.var_1d),
        report);
    return false;
  }

  return true;
}


bool PositionLimitEngine::CheckHardToBorrowLimit(
    const PositionLimitInputRow& row,
    PositionLimitReport* report) const {
  const std::string rule = "hard_to_borrow_limit";
  const double threshold = Threshold(rule, 0.550000);

  if (row.var_10d < threshold) {
    AddIssue(
        PositionLimitIssueType::kFloorBreached,
        PositionLimitSeverity::kWarning,
        row,
        rule,
        "HardToBorrowLimit breached configured validation rule",
        "metric above configured floor",
        NumberText(row.var_10d),
        report);
    return false;
  }

  return true;
}


bool PositionLimitEngine::CheckUnsettledPositionLimit(
    const PositionLimitInputRow& row,
    PositionLimitReport* report) const {
  const std::string rule = "unsettled_position_limit";
  const double threshold = Threshold(rule, 0.625000);

  if (SafeRatio(row.haircut, row.quantity) > threshold) {
    AddIssue(
        PositionLimitIssueType::kRatioViolation,
        PositionLimitSeverity::kError,
        row,
        rule,
        "UnsettledPositionLimit breached configured validation rule",
        "ratio below configured threshold",
        NumberText(SafeRatio(row.haircut, row.quantity)),
        report);
    return false;
  }

  return true;
}


bool PositionLimitEngine::CheckAgedFailLimit(
    const PositionLimitInputRow& row,
    PositionLimitReport* report) const {
  const std::string rule = "aged_fail_limit";
  const double threshold = Threshold(rule, 0.700000);

  if (row.restricted && !allow_manual_overrides_) {
    AddIssue(
        PositionLimitIssueType::kPolicyViolation,
        PositionLimitSeverity::kCritical,
        row,
        rule,
        "AgedFailLimit breached configured validation rule",
        "unrestricted row or approved override",
        "restricted row without override",
        report);
    return false;
  }

  return true;
}


bool PositionLimitEngine::CheckCorporateActionLimit(
    const PositionLimitInputRow& row,
    PositionLimitReport* report) const {
  const std::string rule = "corporate_action_limit";
  const double threshold = Threshold(rule, 0.775000);

  if (strict_mode_ && std::fabs(row.fee_rate - row.volatility) > threshold) {
    AddIssue(
        PositionLimitIssueType::kScenarioFailure,
        PositionLimitSeverity::kWarning,
        row,
        rule,
        "CorporateActionLimit breached configured validation rule",
        "strict-mode metric difference inside threshold",
        NumberText(row.fee_rate - row.volatility),
        report);
    return false;
  }

  return true;
}


bool PositionLimitEngine::CheckEarningsWindowLimit(
    const PositionLimitInputRow& row,
    PositionLimitReport* report) const {
  const std::string rule = "earnings_window_limit";
  const double threshold = Threshold(rule, 0.850000);

  if (std::fabs(row.slippage_bps) > threshold) {
    AddIssue(
        PositionLimitIssueType::kLimitExceeded,
        PositionLimitSeverity::kError,
        row,
        rule,
        "EarningsWindowLimit breached configured validation rule",
        "absolute metric below threshold",
        NumberText(row.slippage_bps),
        report);
    return false;
  }

  return true;
}


bool PositionLimitEngine::CheckEventRiskLimit(
    const PositionLimitInputRow& row,
    PositionLimitReport* report) const {
  const std::string rule = "event_risk_limit";
  const double threshold = Threshold(rule, 0.925000);

  if (row.turnover_rate < threshold) {
    AddIssue(
        PositionLimitIssueType::kFloorBreached,
        PositionLimitSeverity::kWarning,
        row,
        rule,
        "EventRiskLimit breached configured validation rule",
        "metric above configured floor",
        NumberText(row.turnover_rate),
        report);
    return false;
  }

  return true;
}


bool PositionLimitEngine::CheckOpenOrderLimit(
    const PositionLimitInputRow& row,
    PositionLimitReport* report) const {
  const std::string rule = "open_order_limit";
  const double threshold = Threshold(rule, 1.000000);

  if (SafeRatio(row.notional, row.margin_rate) > threshold) {
    AddIssue(
        PositionLimitIssueType::kRatioViolation,
        PositionLimitSeverity::kError,
        row,
        rule,
        "OpenOrderLimit breached configured validation rule",
        "ratio below configured threshold",
        NumberText(SafeRatio(row.notional, row.margin_rate)),
        report);
    return false;
  }

  return true;
}


bool PositionLimitEngine::CheckPendingCancelLimit(
    const PositionLimitInputRow& row,
    PositionLimitReport* report) const {
  const std::string rule = "pending_cancel_limit";
  const double threshold = Threshold(rule, 0.250000);

  if (row.restricted && !allow_manual_overrides_) {
    AddIssue(
        PositionLimitIssueType::kPolicyViolation,
        PositionLimitSeverity::kCritical,
        row,
        rule,
        "PendingCancelLimit breached configured validation rule",
        "unrestricted row or approved override",
        "restricted row without override",
        report);
    return false;
  }

  return true;
}


bool PositionLimitEngine::CheckPendingReplaceLimit(
    const PositionLimitInputRow& row,
    PositionLimitReport* report) const {
  const std::string rule = "pending_replace_limit";
  const double threshold = Threshold(rule, 0.325000);

  if (strict_mode_ && std::fabs(row.quantity - row.unrealized_pnl) > threshold) {
    AddIssue(
        PositionLimitIssueType::kScenarioFailure,
        PositionLimitSeverity::kWarning,
        row,
        rule,
        "PendingReplaceLimit breached configured validation rule",
        "strict-mode metric difference inside threshold",
        NumberText(row.quantity - row.unrealized_pnl),
        report);
    return false;
  }

  return true;
}


bool PositionLimitEngine::CheckIntradayTurnoverLimit(
    const PositionLimitInputRow& row,
    PositionLimitReport* report) const {
  const std::string rule = "intraday_turnover_limit";
  const double threshold = Threshold(rule, 0.400000);

  if (std::fabs(row.price) > threshold) {
    AddIssue(
        PositionLimitIssueType::kLimitExceeded,
        PositionLimitSeverity::kError,
        row,
        rule,
        "IntradayTurnoverLimit breached configured validation rule",
        "absolute metric below threshold",
        NumberText(row.price),
        report);
    return false;
  }

  return true;
}


bool PositionLimitEngine::CheckOvernightInventoryLimit(
    const PositionLimitInputRow& row,
    PositionLimitReport* report) const {
  const std::string rule = "overnight_inventory_limit";
  const double threshold = Threshold(rule, 0.475000);

  if (row.volatility < threshold) {
    AddIssue(
        PositionLimitIssueType::kFloorBreached,
        PositionLimitSeverity::kWarning,
        row,
        rule,
        "OvernightInventoryLimit breached configured validation rule",
        "metric above configured floor",
        NumberText(row.volatility),
        report);
    return false;
  }

  return true;
}


bool PositionLimitEngine::CheckManualOverrideLimit(
    const PositionLimitInputRow& row,
    PositionLimitReport* report) const {
  const std::string rule = "manual_override_limit";
  const double threshold = Threshold(rule, 0.550000);

  if (SafeRatio(row.average_daily_volume, row.net_exposure) > threshold) {
    AddIssue(
        PositionLimitIssueType::kRatioViolation,
        PositionLimitSeverity::kError,
        row,
        rule,
        "ManualOverrideLimit breached configured validation rule",
        "ratio below configured threshold",
        NumberText(SafeRatio(row.average_daily_volume, row.net_exposure)),
        report);
    return false;
  }

  return true;
}


bool PositionLimitEngine::CheckClientMandateLimit(
    const PositionLimitInputRow& row,
    PositionLimitReport* report) const {
  const std::string rule = "client_mandate_limit";
  const double threshold = Threshold(rule, 0.625000);

  if (row.restricted && !allow_manual_overrides_) {
    AddIssue(
        PositionLimitIssueType::kPolicyViolation,
        PositionLimitSeverity::kCritical,
        row,
        rule,
        "ClientMandateLimit breached configured validation rule",
        "unrestricted row or approved override",
        "restricted row without override",
        report);
    return false;
  }

  return true;
}


bool PositionLimitEngine::CheckRegulatoryLimit(
    const PositionLimitInputRow& row,
    PositionLimitReport* report) const {
  const std::string rule = "regulatory_limit";
  const double threshold = Threshold(rule, 0.700000);

  if (strict_mode_ && std::fabs(row.margin_rate - row.confidence) > threshold) {
    AddIssue(
        PositionLimitIssueType::kScenarioFailure,
        PositionLimitSeverity::kWarning,
        row,
        rule,
        "RegulatoryLimit breached configured validation rule",
        "strict-mode metric difference inside threshold",
        NumberText(row.margin_rate - row.confidence),
        report);
    return false;
  }

  return true;
}


std::string PositionLimitSeverityName(PositionLimitSeverity severity) {
  switch (severity) {
    case PositionLimitSeverity::kInfo:
      return "info";
    case PositionLimitSeverity::kWarning:
      return "warning";
    case PositionLimitSeverity::kError:
      return "error";
    case PositionLimitSeverity::kCritical:
      return "critical";
  }
  return "unknown";
}

std::string PositionLimitIssueTypeName(PositionLimitIssueType type) {
  switch (type) {
    case PositionLimitIssueType::kInvalidInput:
      return "invalid_input";
    case PositionLimitIssueType::kMissingIdentifier:
      return "missing_identifier";
    case PositionLimitIssueType::kNonFiniteMetric:
      return "non_finite_metric";
    case PositionLimitIssueType::kThresholdBreach:
      return "threshold_breach";
    case PositionLimitIssueType::kLimitExceeded:
      return "limit_exceeded";
    case PositionLimitIssueType::kFloorBreached:
      return "floor_breached";
    case PositionLimitIssueType::kRatioViolation:
      return "ratio_violation";
    case PositionLimitIssueType::kPolicyViolation:
      return "policy_violation";
    case PositionLimitIssueType::kScenarioFailure:
      return "scenario_failure";
    case PositionLimitIssueType::kAggregationFailure:
      return "aggregation_failure";
  }
  return "unknown";
}

}  // namespace marketdata
