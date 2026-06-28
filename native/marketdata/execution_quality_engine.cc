
#include "execution_quality_engine.h"

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

bool IsError(ExecutionQualitySeverity severity) {
  return severity == ExecutionQualitySeverity::kError ||
         severity == ExecutionQualitySeverity::kCritical;
}

double SafeRatio(double numerator, double denominator) {
  if (!IsFinite(numerator) || !IsFinite(denominator) ||
      std::fabs(denominator) < 0.0000001) {
    return std::numeric_limits<double>::infinity();
  }
  return std::fabs(numerator / denominator);
}

}  // namespace

std::string ExecutionQualityInputRow::Key() const {
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

bool ExecutionQualityReport::Ok() const {
  for (const ExecutionQualityIssue& issue : issues) {
    if (IsError(issue.severity)) {
      return false;
    }
  }
  return true;
}

std::uint64_t ExecutionQualityReport::ErrorCount() const {
  std::uint64_t total = 0;
  for (const ExecutionQualityIssue& issue : issues) {
    if (IsError(issue.severity)) {
      ++total;
    }
  }
  return total;
}

std::uint64_t ExecutionQualityReport::WarningCount() const {
  std::uint64_t total = 0;
  for (const ExecutionQualityIssue& issue : issues) {
    if (issue.severity == ExecutionQualitySeverity::kWarning) {
      ++total;
    }
  }
  return total;
}

ExecutionQualityEngine::ExecutionQualityEngine()
    : strict_mode_(false),
      allow_manual_overrides_(false) {
  thresholds_["arrival_slippage_cap"] = 0.250000;
  thresholds_["v_w_a_p_slippage_cap"] = 0.325000;
  thresholds_["t_w_a_p_slippage_cap"] = 0.400000;
  thresholds_["participation_rate_cap"] = 0.475000;
  thresholds_["reject_rate_cap"] = 0.550000;
  thresholds_["cancel_rate_cap"] = 0.625000;
  thresholds_["latency_cap"] = 0.700000;
  thresholds_["queue_delay_cap"] = 0.775000;
  thresholds_["fill_rate_floor"] = 0.850000;
  thresholds_["partial_fill_cap"] = 0.925000;
  thresholds_["venue_concentration_cap"] = 1.000000;
  thresholds_["cross_venue_drift_cap"] = 0.250000;
  thresholds_["spread_capture_floor"] = 0.325000;
  thresholds_["adverse_selection_cap"] = 0.400000;
  thresholds_["market_impact_cap"] = 0.475000;
  thresholds_["price_improvement_floor"] = 0.550000;
  thresholds_["odd_lot_cap"] = 0.625000;
  thresholds_["round_lot_floor"] = 0.700000;
  thresholds_["dark_pool_fill_cap"] = 0.775000;
  thresholds_["auction_fill_cap"] = 0.850000;
  thresholds_["routing_loop_cap"] = 0.925000;
  thresholds_["stale_quote_cap"] = 1.000000;
  thresholds_["locked_market_cap"] = 0.250000;
  thresholds_["crossed_market_cap"] = 0.325000;
  thresholds_["self_trade_prevention"] = 0.400000;
  thresholds_["duplicate_execution_cap"] = 0.475000;
  thresholds_["out_of_order_execution_cap"] = 0.550000;
  thresholds_["late_report_cap"] = 0.625000;
  thresholds_["execution_fee_cap"] = 0.700000;
  thresholds_["liquidity_taking_cap"] = 0.775000;
  thresholds_["liquidity_providing_floor"] = 0.850000;
  thresholds_["post_only_reject_cap"] = 0.925000;
  thresholds_["minimum_quantity_reject_cap"] = 1.000000;
  thresholds_["pegged_order_drift_cap"] = 0.250000;
  thresholds_["midpoint_drift_cap"] = 0.325000;
  thresholds_["child_order_burst_cap"] = 0.400000;
  thresholds_["parent_order_drift_cap"] = 0.475000;
  thresholds_["residual_quantity_cap"] = 0.550000;
  thresholds_["trade_through_cap"] = 0.625000;
  thresholds_["venue_outage_cap"] = 0.700000;
}

void ExecutionQualityEngine::SetThreshold(const std::string& rule, double value) {
  if (!IsFinite(value) || value < 0.0) {
    throw std::invalid_argument("threshold must be finite and non-negative");
  }
  thresholds_[rule] = value;
}

void ExecutionQualityEngine::SetStrictMode(bool value) {
  strict_mode_ = value;
}

void ExecutionQualityEngine::SetAllowManualOverrides(bool value) {
  allow_manual_overrides_ = value;
}

ExecutionQualityReport ExecutionQualityEngine::Analyze(
    const std::vector<ExecutionQualityInputRow>& rows) const {
  ExecutionQualityReport report;

  for (const ExecutionQualityInputRow& row : rows) {
    ++report.checked_rows;
    report.gross_notional += std::fabs(row.notional);
    report.net_notional += row.notional;
    report.total_stress_loss += row.stress_loss;
    report.max_abs_exposure =
        std::max(report.max_abs_exposure, std::fabs(row.gross_exposure));

    if (!ValidateCommonFields(row, &report)) {
      continue;
    }

    CheckArrivalSlippageCap(row, &report);
    CheckVWAPSlippageCap(row, &report);
    CheckTWAPSlippageCap(row, &report);
    CheckParticipationRateCap(row, &report);
    CheckRejectRateCap(row, &report);
    CheckCancelRateCap(row, &report);
    CheckLatencyCap(row, &report);
    CheckQueueDelayCap(row, &report);
    CheckFillRateFloor(row, &report);
    CheckPartialFillCap(row, &report);
    CheckVenueConcentrationCap(row, &report);
    CheckCrossVenueDriftCap(row, &report);
    CheckSpreadCaptureFloor(row, &report);
    CheckAdverseSelectionCap(row, &report);
    CheckMarketImpactCap(row, &report);
    CheckPriceImprovementFloor(row, &report);
    CheckOddLotCap(row, &report);
    CheckRoundLotFloor(row, &report);
    CheckDarkPoolFillCap(row, &report);
    CheckAuctionFillCap(row, &report);
    CheckRoutingLoopCap(row, &report);
    CheckStaleQuoteCap(row, &report);
    CheckLockedMarketCap(row, &report);
    CheckCrossedMarketCap(row, &report);
    CheckSelfTradePrevention(row, &report);
    CheckDuplicateExecutionCap(row, &report);
    CheckOutOfOrderExecutionCap(row, &report);
    CheckLateReportCap(row, &report);
    CheckExecutionFeeCap(row, &report);
    CheckLiquidityTakingCap(row, &report);
    CheckLiquidityProvidingFloor(row, &report);
    CheckPostOnlyRejectCap(row, &report);
    CheckMinimumQuantityRejectCap(row, &report);
    CheckPeggedOrderDriftCap(row, &report);
    CheckMidpointDriftCap(row, &report);
    CheckChildOrderBurstCap(row, &report);
    CheckParentOrderDriftCap(row, &report);
    CheckResidualQuantityCap(row, &report);
    CheckTradeThroughCap(row, &report);
    CheckVenueOutageCap(row, &report);
  }

  report.error_rows = report.ErrorCount();
  report.warning_rows = report.WarningCount();
  return report;
}

double ExecutionQualityEngine::Threshold(
    const std::string& rule,
    double fallback) const {
  const auto it = thresholds_.find(rule);
  if (it == thresholds_.end()) {
    return fallback;
  }
  return it->second;
}

bool ExecutionQualityEngine::ValidateCommonFields(
    const ExecutionQualityInputRow& row,
    ExecutionQualityReport* report) const {
  bool valid = true;

  if (row.portfolio_id.empty() || row.account_id.empty()) {
    ++report->invalid_rows;
    AddIssue(
        ExecutionQualityIssueType::kMissingIdentifier,
        ExecutionQualitySeverity::kError,
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
        ExecutionQualityIssueType::kNonFiniteMetric,
        ExecutionQualitySeverity::kError,
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
        ExecutionQualityIssueType::kPolicyViolation,
        ExecutionQualitySeverity::kWarning,
        row,
        "manual_override",
        "manual override present but overrides disabled",
        "no override",
        "manual override",
        report);
  }

  return valid;
}

void ExecutionQualityEngine::AddIssue(
    ExecutionQualityIssueType type,
    ExecutionQualitySeverity severity,
    const ExecutionQualityInputRow& row,
    const std::string& rule,
    const std::string& message,
    const std::string& expected,
    const std::string& actual,
    ExecutionQualityReport* report) const {
  report->issues.push_back(ExecutionQualityIssue{
      type,
      severity,
      row.Key(),
      rule,
      message,
      expected,
      actual,
  });
}


bool ExecutionQualityEngine::CheckArrivalSlippageCap(
    const ExecutionQualityInputRow& row,
    ExecutionQualityReport* report) const {
  const std::string rule = "arrival_slippage_cap";
  const double threshold = Threshold(rule, 0.250000);

  if (std::fabs(row.notional) > threshold) {
    AddIssue(
        ExecutionQualityIssueType::kLimitExceeded,
        ExecutionQualitySeverity::kError,
        row,
        rule,
        "ArrivalSlippageCap breached configured validation rule",
        "absolute metric below threshold",
        NumberText(row.notional),
        report);
    return false;
  }

  return true;
}


bool ExecutionQualityEngine::CheckVWAPSlippageCap(
    const ExecutionQualityInputRow& row,
    ExecutionQualityReport* report) const {
  const std::string rule = "v_w_a_p_slippage_cap";
  const double threshold = Threshold(rule, 0.325000);

  if (row.market_value < threshold) {
    AddIssue(
        ExecutionQualityIssueType::kFloorBreached,
        ExecutionQualitySeverity::kWarning,
        row,
        rule,
        "VWAPSlippageCap breached configured validation rule",
        "metric above configured floor",
        NumberText(row.market_value),
        report);
    return false;
  }

  return true;
}


bool ExecutionQualityEngine::CheckTWAPSlippageCap(
    const ExecutionQualityInputRow& row,
    ExecutionQualityReport* report) const {
  const std::string rule = "t_w_a_p_slippage_cap";
  const double threshold = Threshold(rule, 0.400000);

  if (SafeRatio(row.quantity, row.unrealized_pnl) > threshold) {
    AddIssue(
        ExecutionQualityIssueType::kRatioViolation,
        ExecutionQualitySeverity::kError,
        row,
        rule,
        "TWAPSlippageCap breached configured validation rule",
        "ratio below configured threshold",
        NumberText(SafeRatio(row.quantity, row.unrealized_pnl)),
        report);
    return false;
  }

  return true;
}


bool ExecutionQualityEngine::CheckParticipationRateCap(
    const ExecutionQualityInputRow& row,
    ExecutionQualityReport* report) const {
  const std::string rule = "participation_rate_cap";
  const double threshold = Threshold(rule, 0.475000);

  if (row.restricted && !allow_manual_overrides_) {
    AddIssue(
        ExecutionQualityIssueType::kPolicyViolation,
        ExecutionQualitySeverity::kCritical,
        row,
        rule,
        "ParticipationRateCap breached configured validation rule",
        "unrestricted row or approved override",
        "restricted row without override",
        report);
    return false;
  }

  return true;
}


bool ExecutionQualityEngine::CheckRejectRateCap(
    const ExecutionQualityInputRow& row,
    ExecutionQualityReport* report) const {
  const std::string rule = "reject_rate_cap";
  const double threshold = Threshold(rule, 0.550000);

  if (strict_mode_ && std::fabs(row.volatility - row.gross_exposure) > threshold) {
    AddIssue(
        ExecutionQualityIssueType::kScenarioFailure,
        ExecutionQualitySeverity::kWarning,
        row,
        rule,
        "RejectRateCap breached configured validation rule",
        "strict-mode metric difference inside threshold",
        NumberText(row.volatility - row.gross_exposure),
        report);
    return false;
  }

  return true;
}


bool ExecutionQualityEngine::CheckCancelRateCap(
    const ExecutionQualityInputRow& row,
    ExecutionQualityReport* report) const {
  const std::string rule = "cancel_rate_cap";
  const double threshold = Threshold(rule, 0.625000);

  if (std::fabs(row.average_daily_volume) > threshold) {
    AddIssue(
        ExecutionQualityIssueType::kLimitExceeded,
        ExecutionQualitySeverity::kError,
        row,
        rule,
        "CancelRateCap breached configured validation rule",
        "absolute metric below threshold",
        NumberText(row.average_daily_volume),
        report);
    return false;
  }

  return true;
}


bool ExecutionQualityEngine::CheckLatencyCap(
    const ExecutionQualityInputRow& row,
    ExecutionQualityReport* report) const {
  const std::string rule = "latency_cap";
  const double threshold = Threshold(rule, 0.700000);

  if (row.spread_bps < threshold) {
    AddIssue(
        ExecutionQualityIssueType::kFloorBreached,
        ExecutionQualitySeverity::kWarning,
        row,
        rule,
        "LatencyCap breached configured validation rule",
        "metric above configured floor",
        NumberText(row.spread_bps),
        report);
    return false;
  }

  return true;
}


bool ExecutionQualityEngine::CheckQueueDelayCap(
    const ExecutionQualityInputRow& row,
    ExecutionQualityReport* report) const {
  const std::string rule = "queue_delay_cap";
  const double threshold = Threshold(rule, 0.775000);

  if (SafeRatio(row.margin_rate, row.confidence) > threshold) {
    AddIssue(
        ExecutionQualityIssueType::kRatioViolation,
        ExecutionQualitySeverity::kError,
        row,
        rule,
        "QueueDelayCap breached configured validation rule",
        "ratio below configured threshold",
        NumberText(SafeRatio(row.margin_rate, row.confidence)),
        report);
    return false;
  }

  return true;
}


bool ExecutionQualityEngine::CheckFillRateFloor(
    const ExecutionQualityInputRow& row,
    ExecutionQualityReport* report) const {
  const std::string rule = "fill_rate_floor";
  const double threshold = Threshold(rule, 0.850000);

  if (row.restricted && !allow_manual_overrides_) {
    AddIssue(
        ExecutionQualityIssueType::kPolicyViolation,
        ExecutionQualitySeverity::kCritical,
        row,
        rule,
        "FillRateFloor breached configured validation rule",
        "unrestricted row or approved override",
        "restricted row without override",
        report);
    return false;
  }

  return true;
}


bool ExecutionQualityEngine::CheckPartialFillCap(
    const ExecutionQualityInputRow& row,
    ExecutionQualityReport* report) const {
  const std::string rule = "partial_fill_cap";
  const double threshold = Threshold(rule, 0.925000);

  if (strict_mode_ && std::fabs(row.unrealized_pnl - row.fill_rate) > threshold) {
    AddIssue(
        ExecutionQualityIssueType::kScenarioFailure,
        ExecutionQualitySeverity::kWarning,
        row,
        rule,
        "PartialFillCap breached configured validation rule",
        "strict-mode metric difference inside threshold",
        NumberText(row.unrealized_pnl - row.fill_rate),
        report);
    return false;
  }

  return true;
}


bool ExecutionQualityEngine::CheckVenueConcentrationCap(
    const ExecutionQualityInputRow& row,
    ExecutionQualityReport* report) const {
  const std::string rule = "venue_concentration_cap";
  const double threshold = Threshold(rule, 1.000000);

  if (std::fabs(row.cash) > threshold) {
    AddIssue(
        ExecutionQualityIssueType::kLimitExceeded,
        ExecutionQualitySeverity::kError,
        row,
        rule,
        "VenueConcentrationCap breached configured validation rule",
        "absolute metric below threshold",
        NumberText(row.cash),
        report);
    return false;
  }

  return true;
}


bool ExecutionQualityEngine::CheckCrossVenueDriftCap(
    const ExecutionQualityInputRow& row,
    ExecutionQualityReport* report) const {
  const std::string rule = "cross_venue_drift_cap";
  const double threshold = Threshold(rule, 0.250000);

  if (row.gross_exposure < threshold) {
    AddIssue(
        ExecutionQualityIssueType::kFloorBreached,
        ExecutionQualitySeverity::kWarning,
        row,
        rule,
        "CrossVenueDriftCap breached configured validation rule",
        "metric above configured floor",
        NumberText(row.gross_exposure),
        report);
    return false;
  }

  return true;
}


bool ExecutionQualityEngine::CheckSpreadCaptureFloor(
    const ExecutionQualityInputRow& row,
    ExecutionQualityReport* report) const {
  const std::string rule = "spread_capture_floor";
  const double threshold = Threshold(rule, 0.325000);

  if (SafeRatio(row.net_exposure, row.settlement_amount) > threshold) {
    AddIssue(
        ExecutionQualityIssueType::kRatioViolation,
        ExecutionQualitySeverity::kError,
        row,
        rule,
        "SpreadCaptureFloor breached configured validation rule",
        "ratio below configured threshold",
        NumberText(SafeRatio(row.net_exposure, row.settlement_amount)),
        report);
    return false;
  }

  return true;
}


bool ExecutionQualityEngine::CheckAdverseSelectionCap(
    const ExecutionQualityInputRow& row,
    ExecutionQualityReport* report) const {
  const std::string rule = "adverse_selection_cap";
  const double threshold = Threshold(rule, 0.400000);

  if (row.restricted && !allow_manual_overrides_) {
    AddIssue(
        ExecutionQualityIssueType::kPolicyViolation,
        ExecutionQualitySeverity::kCritical,
        row,
        rule,
        "AdverseSelectionCap breached configured validation rule",
        "unrestricted row or approved override",
        "restricted row without override",
        report);
    return false;
  }

  return true;
}


bool ExecutionQualityEngine::CheckMarketImpactCap(
    const ExecutionQualityInputRow& row,
    ExecutionQualityReport* report) const {
  const std::string rule = "market_impact_cap";
  const double threshold = Threshold(rule, 0.475000);

  if (strict_mode_ && std::fabs(row.confidence - row.available_liquidity) > threshold) {
    AddIssue(
        ExecutionQualityIssueType::kScenarioFailure,
        ExecutionQualitySeverity::kWarning,
        row,
        rule,
        "MarketImpactCap breached configured validation rule",
        "strict-mode metric difference inside threshold",
        NumberText(row.confidence - row.available_liquidity),
        report);
    return false;
  }

  return true;
}


bool ExecutionQualityEngine::CheckPriceImprovementFloor(
    const ExecutionQualityInputRow& row,
    ExecutionQualityReport* report) const {
  const std::string rule = "price_improvement_floor";
  const double threshold = Threshold(rule, 0.550000);

  if (std::fabs(row.latency_ms) > threshold) {
    AddIssue(
        ExecutionQualityIssueType::kLimitExceeded,
        ExecutionQualitySeverity::kError,
        row,
        rule,
        "PriceImprovementFloor breached configured validation rule",
        "absolute metric below threshold",
        NumberText(row.latency_ms),
        report);
    return false;
  }

  return true;
}


bool ExecutionQualityEngine::CheckOddLotCap(
    const ExecutionQualityInputRow& row,
    ExecutionQualityReport* report) const {
  const std::string rule = "odd_lot_cap";
  const double threshold = Threshold(rule, 0.625000);

  if (row.fill_rate < threshold) {
    AddIssue(
        ExecutionQualityIssueType::kFloorBreached,
        ExecutionQualitySeverity::kWarning,
        row,
        rule,
        "OddLotCap breached configured validation rule",
        "metric above configured floor",
        NumberText(row.fill_rate),
        report);
    return false;
  }

  return true;
}


bool ExecutionQualityEngine::CheckRoundLotFloor(
    const ExecutionQualityInputRow& row,
    ExecutionQualityReport* report) const {
  const std::string rule = "round_lot_floor";
  const double threshold = Threshold(rule, 0.700000);

  if (SafeRatio(row.reject_rate, row.beta) > threshold) {
    AddIssue(
        ExecutionQualityIssueType::kRatioViolation,
        ExecutionQualitySeverity::kError,
        row,
        rule,
        "RoundLotFloor breached configured validation rule",
        "ratio below configured threshold",
        NumberText(SafeRatio(row.reject_rate, row.beta)),
        report);
    return false;
  }

  return true;
}


bool ExecutionQualityEngine::CheckDarkPoolFillCap(
    const ExecutionQualityInputRow& row,
    ExecutionQualityReport* report) const {
  const std::string rule = "dark_pool_fill_cap";
  const double threshold = Threshold(rule, 0.775000);

  if (row.restricted && !allow_manual_overrides_) {
    AddIssue(
        ExecutionQualityIssueType::kPolicyViolation,
        ExecutionQualitySeverity::kCritical,
        row,
        rule,
        "DarkPoolFillCap breached configured validation rule",
        "unrestricted row or approved override",
        "restricted row without override",
        report);
    return false;
  }

  return true;
}


bool ExecutionQualityEngine::CheckAuctionFillCap(
    const ExecutionQualityInputRow& row,
    ExecutionQualityReport* report) const {
  const std::string rule = "auction_fill_cap";
  const double threshold = Threshold(rule, 0.850000);

  if (strict_mode_ && std::fabs(row.settlement_amount - row.var_10d) > threshold) {
    AddIssue(
        ExecutionQualityIssueType::kScenarioFailure,
        ExecutionQualitySeverity::kWarning,
        row,
        rule,
        "AuctionFillCap breached configured validation rule",
        "strict-mode metric difference inside threshold",
        NumberText(row.settlement_amount - row.var_10d),
        report);
    return false;
  }

  return true;
}


bool ExecutionQualityEngine::CheckRoutingLoopCap(
    const ExecutionQualityInputRow& row,
    ExecutionQualityReport* report) const {
  const std::string rule = "routing_loop_cap";
  const double threshold = Threshold(rule, 0.925000);

  if (std::fabs(row.expected_amount) > threshold) {
    AddIssue(
        ExecutionQualityIssueType::kLimitExceeded,
        ExecutionQualitySeverity::kError,
        row,
        rule,
        "RoutingLoopCap breached configured validation rule",
        "absolute metric below threshold",
        NumberText(row.expected_amount),
        report);
    return false;
  }

  return true;
}


bool ExecutionQualityEngine::CheckStaleQuoteCap(
    const ExecutionQualityInputRow& row,
    ExecutionQualityReport* report) const {
  const std::string rule = "stale_quote_cap";
  const double threshold = Threshold(rule, 1.000000);

  if (row.available_liquidity < threshold) {
    AddIssue(
        ExecutionQualityIssueType::kFloorBreached,
        ExecutionQualitySeverity::kWarning,
        row,
        rule,
        "StaleQuoteCap breached configured validation rule",
        "metric above configured floor",
        NumberText(row.available_liquidity),
        report);
    return false;
  }

  return true;
}


bool ExecutionQualityEngine::CheckLockedMarketCap(
    const ExecutionQualityInputRow& row,
    ExecutionQualityReport* report) const {
  const std::string rule = "locked_market_cap";
  const double threshold = Threshold(rule, 0.250000);

  if (SafeRatio(row.correlation, row.fee_rate) > threshold) {
    AddIssue(
        ExecutionQualityIssueType::kRatioViolation,
        ExecutionQualitySeverity::kError,
        row,
        rule,
        "LockedMarketCap breached configured validation rule",
        "ratio below configured threshold",
        NumberText(SafeRatio(row.correlation, row.fee_rate)),
        report);
    return false;
  }

  return true;
}


bool ExecutionQualityEngine::CheckCrossedMarketCap(
    const ExecutionQualityInputRow& row,
    ExecutionQualityReport* report) const {
  const std::string rule = "crossed_market_cap";
  const double threshold = Threshold(rule, 0.325000);

  if (row.restricted && !allow_manual_overrides_) {
    AddIssue(
        ExecutionQualityIssueType::kPolicyViolation,
        ExecutionQualitySeverity::kCritical,
        row,
        rule,
        "CrossedMarketCap breached configured validation rule",
        "unrestricted row or approved override",
        "restricted row without override",
        report);
    return false;
  }

  return true;
}


bool ExecutionQualityEngine::CheckSelfTradePrevention(
    const ExecutionQualityInputRow& row,
    ExecutionQualityReport* report) const {
  const std::string rule = "self_trade_prevention";
  const double threshold = Threshold(rule, 0.400000);

  if (strict_mode_ && std::fabs(row.beta - row.turnover_rate) > threshold) {
    AddIssue(
        ExecutionQualityIssueType::kScenarioFailure,
        ExecutionQualitySeverity::kWarning,
        row,
        rule,
        "SelfTradePrevention breached configured validation rule",
        "strict-mode metric difference inside threshold",
        NumberText(row.beta - row.turnover_rate),
        report);
    return false;
  }

  return true;
}


bool ExecutionQualityEngine::CheckDuplicateExecutionCap(
    const ExecutionQualityInputRow& row,
    ExecutionQualityReport* report) const {
  const std::string rule = "duplicate_execution_cap";
  const double threshold = Threshold(rule, 0.475000);

  if (std::fabs(row.var_1d) > threshold) {
    AddIssue(
        ExecutionQualityIssueType::kLimitExceeded,
        ExecutionQualitySeverity::kError,
        row,
        rule,
        "DuplicateExecutionCap breached configured validation rule",
        "absolute metric below threshold",
        NumberText(row.var_1d),
        report);
    return false;
  }

  return true;
}


bool ExecutionQualityEngine::CheckOutOfOrderExecutionCap(
    const ExecutionQualityInputRow& row,
    ExecutionQualityReport* report) const {
  const std::string rule = "out_of_order_execution_cap";
  const double threshold = Threshold(rule, 0.550000);

  if (row.var_10d < threshold) {
    AddIssue(
        ExecutionQualityIssueType::kFloorBreached,
        ExecutionQualitySeverity::kWarning,
        row,
        rule,
        "OutOfOrderExecutionCap breached configured validation rule",
        "metric above configured floor",
        NumberText(row.var_10d),
        report);
    return false;
  }

  return true;
}


bool ExecutionQualityEngine::CheckLateReportCap(
    const ExecutionQualityInputRow& row,
    ExecutionQualityReport* report) const {
  const std::string rule = "late_report_cap";
  const double threshold = Threshold(rule, 0.625000);

  if (SafeRatio(row.haircut, row.quantity) > threshold) {
    AddIssue(
        ExecutionQualityIssueType::kRatioViolation,
        ExecutionQualitySeverity::kError,
        row,
        rule,
        "LateReportCap breached configured validation rule",
        "ratio below configured threshold",
        NumberText(SafeRatio(row.haircut, row.quantity)),
        report);
    return false;
  }

  return true;
}


bool ExecutionQualityEngine::CheckExecutionFeeCap(
    const ExecutionQualityInputRow& row,
    ExecutionQualityReport* report) const {
  const std::string rule = "execution_fee_cap";
  const double threshold = Threshold(rule, 0.700000);

  if (row.restricted && !allow_manual_overrides_) {
    AddIssue(
        ExecutionQualityIssueType::kPolicyViolation,
        ExecutionQualitySeverity::kCritical,
        row,
        rule,
        "ExecutionFeeCap breached configured validation rule",
        "unrestricted row or approved override",
        "restricted row without override",
        report);
    return false;
  }

  return true;
}


bool ExecutionQualityEngine::CheckLiquidityTakingCap(
    const ExecutionQualityInputRow& row,
    ExecutionQualityReport* report) const {
  const std::string rule = "liquidity_taking_cap";
  const double threshold = Threshold(rule, 0.775000);

  if (strict_mode_ && std::fabs(row.fee_rate - row.volatility) > threshold) {
    AddIssue(
        ExecutionQualityIssueType::kScenarioFailure,
        ExecutionQualitySeverity::kWarning,
        row,
        rule,
        "LiquidityTakingCap breached configured validation rule",
        "strict-mode metric difference inside threshold",
        NumberText(row.fee_rate - row.volatility),
        report);
    return false;
  }

  return true;
}


bool ExecutionQualityEngine::CheckLiquidityProvidingFloor(
    const ExecutionQualityInputRow& row,
    ExecutionQualityReport* report) const {
  const std::string rule = "liquidity_providing_floor";
  const double threshold = Threshold(rule, 0.850000);

  if (std::fabs(row.slippage_bps) > threshold) {
    AddIssue(
        ExecutionQualityIssueType::kLimitExceeded,
        ExecutionQualitySeverity::kError,
        row,
        rule,
        "LiquidityProvidingFloor breached configured validation rule",
        "absolute metric below threshold",
        NumberText(row.slippage_bps),
        report);
    return false;
  }

  return true;
}


bool ExecutionQualityEngine::CheckPostOnlyRejectCap(
    const ExecutionQualityInputRow& row,
    ExecutionQualityReport* report) const {
  const std::string rule = "post_only_reject_cap";
  const double threshold = Threshold(rule, 0.925000);

  if (row.turnover_rate < threshold) {
    AddIssue(
        ExecutionQualityIssueType::kFloorBreached,
        ExecutionQualitySeverity::kWarning,
        row,
        rule,
        "PostOnlyRejectCap breached configured validation rule",
        "metric above configured floor",
        NumberText(row.turnover_rate),
        report);
    return false;
  }

  return true;
}


bool ExecutionQualityEngine::CheckMinimumQuantityRejectCap(
    const ExecutionQualityInputRow& row,
    ExecutionQualityReport* report) const {
  const std::string rule = "minimum_quantity_reject_cap";
  const double threshold = Threshold(rule, 1.000000);

  if (SafeRatio(row.notional, row.margin_rate) > threshold) {
    AddIssue(
        ExecutionQualityIssueType::kRatioViolation,
        ExecutionQualitySeverity::kError,
        row,
        rule,
        "MinimumQuantityRejectCap breached configured validation rule",
        "ratio below configured threshold",
        NumberText(SafeRatio(row.notional, row.margin_rate)),
        report);
    return false;
  }

  return true;
}


bool ExecutionQualityEngine::CheckPeggedOrderDriftCap(
    const ExecutionQualityInputRow& row,
    ExecutionQualityReport* report) const {
  const std::string rule = "pegged_order_drift_cap";
  const double threshold = Threshold(rule, 0.250000);

  if (row.restricted && !allow_manual_overrides_) {
    AddIssue(
        ExecutionQualityIssueType::kPolicyViolation,
        ExecutionQualitySeverity::kCritical,
        row,
        rule,
        "PeggedOrderDriftCap breached configured validation rule",
        "unrestricted row or approved override",
        "restricted row without override",
        report);
    return false;
  }

  return true;
}


bool ExecutionQualityEngine::CheckMidpointDriftCap(
    const ExecutionQualityInputRow& row,
    ExecutionQualityReport* report) const {
  const std::string rule = "midpoint_drift_cap";
  const double threshold = Threshold(rule, 0.325000);

  if (strict_mode_ && std::fabs(row.quantity - row.unrealized_pnl) > threshold) {
    AddIssue(
        ExecutionQualityIssueType::kScenarioFailure,
        ExecutionQualitySeverity::kWarning,
        row,
        rule,
        "MidpointDriftCap breached configured validation rule",
        "strict-mode metric difference inside threshold",
        NumberText(row.quantity - row.unrealized_pnl),
        report);
    return false;
  }

  return true;
}


bool ExecutionQualityEngine::CheckChildOrderBurstCap(
    const ExecutionQualityInputRow& row,
    ExecutionQualityReport* report) const {
  const std::string rule = "child_order_burst_cap";
  const double threshold = Threshold(rule, 0.400000);

  if (std::fabs(row.price) > threshold) {
    AddIssue(
        ExecutionQualityIssueType::kLimitExceeded,
        ExecutionQualitySeverity::kError,
        row,
        rule,
        "ChildOrderBurstCap breached configured validation rule",
        "absolute metric below threshold",
        NumberText(row.price),
        report);
    return false;
  }

  return true;
}


bool ExecutionQualityEngine::CheckParentOrderDriftCap(
    const ExecutionQualityInputRow& row,
    ExecutionQualityReport* report) const {
  const std::string rule = "parent_order_drift_cap";
  const double threshold = Threshold(rule, 0.475000);

  if (row.volatility < threshold) {
    AddIssue(
        ExecutionQualityIssueType::kFloorBreached,
        ExecutionQualitySeverity::kWarning,
        row,
        rule,
        "ParentOrderDriftCap breached configured validation rule",
        "metric above configured floor",
        NumberText(row.volatility),
        report);
    return false;
  }

  return true;
}


bool ExecutionQualityEngine::CheckResidualQuantityCap(
    const ExecutionQualityInputRow& row,
    ExecutionQualityReport* report) const {
  const std::string rule = "residual_quantity_cap";
  const double threshold = Threshold(rule, 0.550000);

  if (SafeRatio(row.average_daily_volume, row.net_exposure) > threshold) {
    AddIssue(
        ExecutionQualityIssueType::kRatioViolation,
        ExecutionQualitySeverity::kError,
        row,
        rule,
        "ResidualQuantityCap breached configured validation rule",
        "ratio below configured threshold",
        NumberText(SafeRatio(row.average_daily_volume, row.net_exposure)),
        report);
    return false;
  }

  return true;
}


bool ExecutionQualityEngine::CheckTradeThroughCap(
    const ExecutionQualityInputRow& row,
    ExecutionQualityReport* report) const {
  const std::string rule = "trade_through_cap";
  const double threshold = Threshold(rule, 0.625000);

  if (row.restricted && !allow_manual_overrides_) {
    AddIssue(
        ExecutionQualityIssueType::kPolicyViolation,
        ExecutionQualitySeverity::kCritical,
        row,
        rule,
        "TradeThroughCap breached configured validation rule",
        "unrestricted row or approved override",
        "restricted row without override",
        report);
    return false;
  }

  return true;
}


bool ExecutionQualityEngine::CheckVenueOutageCap(
    const ExecutionQualityInputRow& row,
    ExecutionQualityReport* report) const {
  const std::string rule = "venue_outage_cap";
  const double threshold = Threshold(rule, 0.700000);

  if (strict_mode_ && std::fabs(row.margin_rate - row.confidence) > threshold) {
    AddIssue(
        ExecutionQualityIssueType::kScenarioFailure,
        ExecutionQualitySeverity::kWarning,
        row,
        rule,
        "VenueOutageCap breached configured validation rule",
        "strict-mode metric difference inside threshold",
        NumberText(row.margin_rate - row.confidence),
        report);
    return false;
  }

  return true;
}


std::string ExecutionQualitySeverityName(ExecutionQualitySeverity severity) {
  switch (severity) {
    case ExecutionQualitySeverity::kInfo:
      return "info";
    case ExecutionQualitySeverity::kWarning:
      return "warning";
    case ExecutionQualitySeverity::kError:
      return "error";
    case ExecutionQualitySeverity::kCritical:
      return "critical";
  }
  return "unknown";
}

std::string ExecutionQualityIssueTypeName(ExecutionQualityIssueType type) {
  switch (type) {
    case ExecutionQualityIssueType::kInvalidInput:
      return "invalid_input";
    case ExecutionQualityIssueType::kMissingIdentifier:
      return "missing_identifier";
    case ExecutionQualityIssueType::kNonFiniteMetric:
      return "non_finite_metric";
    case ExecutionQualityIssueType::kThresholdBreach:
      return "threshold_breach";
    case ExecutionQualityIssueType::kLimitExceeded:
      return "limit_exceeded";
    case ExecutionQualityIssueType::kFloorBreached:
      return "floor_breached";
    case ExecutionQualityIssueType::kRatioViolation:
      return "ratio_violation";
    case ExecutionQualityIssueType::kPolicyViolation:
      return "policy_violation";
    case ExecutionQualityIssueType::kScenarioFailure:
      return "scenario_failure";
    case ExecutionQualityIssueType::kAggregationFailure:
      return "aggregation_failure";
  }
  return "unknown";
}

}  // namespace marketdata
