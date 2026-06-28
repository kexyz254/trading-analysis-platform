
#include "stress_scenario_engine.h"

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

bool IsError(StressScenarioSeverity severity) {
  return severity == StressScenarioSeverity::kError ||
         severity == StressScenarioSeverity::kCritical;
}

double SafeRatio(double numerator, double denominator) {
  if (!IsFinite(numerator) || !IsFinite(denominator) ||
      std::fabs(denominator) < 0.0000001) {
    return std::numeric_limits<double>::infinity();
  }
  return std::fabs(numerator / denominator);
}

}  // namespace

std::string StressScenarioInputRow::Key() const {
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

bool StressScenarioReport::Ok() const {
  for (const StressScenarioIssue& issue : issues) {
    if (IsError(issue.severity)) {
      return false;
    }
  }
  return true;
}

std::uint64_t StressScenarioReport::ErrorCount() const {
  std::uint64_t total = 0;
  for (const StressScenarioIssue& issue : issues) {
    if (IsError(issue.severity)) {
      ++total;
    }
  }
  return total;
}

std::uint64_t StressScenarioReport::WarningCount() const {
  std::uint64_t total = 0;
  for (const StressScenarioIssue& issue : issues) {
    if (issue.severity == StressScenarioSeverity::kWarning) {
      ++total;
    }
  }
  return total;
}

StressScenarioEngine::StressScenarioEngine()
    : strict_mode_(false),
      allow_manual_overrides_(false) {
  thresholds_["equity_shock_down"] = 0.250000;
  thresholds_["equity_shock_up"] = 0.325000;
  thresholds_["rate_shock_parallel"] = 0.400000;
  thresholds_["rate_shock_steepener"] = 0.475000;
  thresholds_["rate_shock_flattener"] = 0.550000;
  thresholds_["credit_spread_shock"] = 0.625000;
  thresholds_["fx_devaluation_shock"] = 0.700000;
  thresholds_["commodity_shock"] = 0.775000;
  thresholds_["volatility_spike"] = 0.850000;
  thresholds_["liquidity_freeze"] = 0.925000;
  thresholds_["correlation_breakdown"] = 1.000000;
  thresholds_["gap_open_loss"] = 0.250000;
  thresholds_["limit_down_scenario"] = 0.325000;
  thresholds_["limit_up_scenario"] = 0.400000;
  thresholds_["short_squeeze_scenario"] = 0.475000;
  thresholds_["borrow_recall_scenario"] = 0.550000;
  thresholds_["settlement_delay_scenario"] = 0.625000;
  thresholds_["margin_call_scenario"] = 0.700000;
  thresholds_["cash_runoff_scenario"] = 0.775000;
  thresholds_["concentration_unwind"] = 0.850000;
  thresholds_["crowded_trade_exit"] = 0.925000;
  thresholds_["index_rebalance_shock"] = 1.000000;
  thresholds_["corporate_action_shock"] = 0.250000;
  thresholds_["dividend_cut_shock"] = 0.325000;
  thresholds_["earnings_gap_scenario"] = 0.400000;
  thresholds_["macro_event_scenario"] = 0.475000;
  thresholds_["default_scenario"] = 0.550000;
  thresholds_["counterparty_failure"] = 0.625000;
  thresholds_["venue_halt_scenario"] = 0.700000;
  thresholds_["circuit_breaker_scenario"] = 0.775000;
  thresholds_["slippage_widening"] = 0.850000;
  thresholds_["spread_explosion"] = 0.925000;
  thresholds_["volume_collapse"] = 1.000000;
  thresholds_["beta_expansion"] = 0.250000;
  thresholds_["basis_risk_expansion"] = 0.325000;
  thresholds_["cross_asset_contagion"] = 0.400000;
  thresholds_["vol_surface_twist"] = 0.475000;
  thresholds_["curve_convexity_shock"] = 0.550000;
  thresholds_["tail_loss_scenario"] = 0.625000;
  thresholds_["recovery_delay_scenario"] = 0.700000;
}

void StressScenarioEngine::SetThreshold(const std::string& rule, double value) {
  if (!IsFinite(value) || value < 0.0) {
    throw std::invalid_argument("threshold must be finite and non-negative");
  }
  thresholds_[rule] = value;
}

void StressScenarioEngine::SetStrictMode(bool value) {
  strict_mode_ = value;
}

void StressScenarioEngine::SetAllowManualOverrides(bool value) {
  allow_manual_overrides_ = value;
}

StressScenarioReport StressScenarioEngine::Analyze(
    const std::vector<StressScenarioInputRow>& rows) const {
  StressScenarioReport report;

  for (const StressScenarioInputRow& row : rows) {
    ++report.checked_rows;
    report.gross_notional += std::fabs(row.notional);
    report.net_notional += row.notional;
    report.total_stress_loss += row.stress_loss;
    report.max_abs_exposure =
        std::max(report.max_abs_exposure, std::fabs(row.gross_exposure));

    if (!ValidateCommonFields(row, &report)) {
      continue;
    }

    CheckEquityShockDown(row, &report);
    CheckEquityShockUp(row, &report);
    CheckRateShockParallel(row, &report);
    CheckRateShockSteepener(row, &report);
    CheckRateShockFlattener(row, &report);
    CheckCreditSpreadShock(row, &report);
    CheckFxDevaluationShock(row, &report);
    CheckCommodityShock(row, &report);
    CheckVolatilitySpike(row, &report);
    CheckLiquidityFreeze(row, &report);
    CheckCorrelationBreakdown(row, &report);
    CheckGapOpenLoss(row, &report);
    CheckLimitDownScenario(row, &report);
    CheckLimitUpScenario(row, &report);
    CheckShortSqueezeScenario(row, &report);
    CheckBorrowRecallScenario(row, &report);
    CheckSettlementDelayScenario(row, &report);
    CheckMarginCallScenario(row, &report);
    CheckCashRunoffScenario(row, &report);
    CheckConcentrationUnwind(row, &report);
    CheckCrowdedTradeExit(row, &report);
    CheckIndexRebalanceShock(row, &report);
    CheckCorporateActionShock(row, &report);
    CheckDividendCutShock(row, &report);
    CheckEarningsGapScenario(row, &report);
    CheckMacroEventScenario(row, &report);
    CheckDefaultScenario(row, &report);
    CheckCounterpartyFailure(row, &report);
    CheckVenueHaltScenario(row, &report);
    CheckCircuitBreakerScenario(row, &report);
    CheckSlippageWidening(row, &report);
    CheckSpreadExplosion(row, &report);
    CheckVolumeCollapse(row, &report);
    CheckBetaExpansion(row, &report);
    CheckBasisRiskExpansion(row, &report);
    CheckCrossAssetContagion(row, &report);
    CheckVolSurfaceTwist(row, &report);
    CheckCurveConvexityShock(row, &report);
    CheckTailLossScenario(row, &report);
    CheckRecoveryDelayScenario(row, &report);
  }

  report.error_rows = report.ErrorCount();
  report.warning_rows = report.WarningCount();
  return report;
}

double StressScenarioEngine::Threshold(
    const std::string& rule,
    double fallback) const {
  const auto it = thresholds_.find(rule);
  if (it == thresholds_.end()) {
    return fallback;
  }
  return it->second;
}

bool StressScenarioEngine::ValidateCommonFields(
    const StressScenarioInputRow& row,
    StressScenarioReport* report) const {
  bool valid = true;

  if (row.portfolio_id.empty() || row.account_id.empty()) {
    ++report->invalid_rows;
    AddIssue(
        StressScenarioIssueType::kMissingIdentifier,
        StressScenarioSeverity::kError,
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
        StressScenarioIssueType::kNonFiniteMetric,
        StressScenarioSeverity::kError,
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
        StressScenarioIssueType::kPolicyViolation,
        StressScenarioSeverity::kWarning,
        row,
        "manual_override",
        "manual override present but overrides disabled",
        "no override",
        "manual override",
        report);
  }

  return valid;
}

void StressScenarioEngine::AddIssue(
    StressScenarioIssueType type,
    StressScenarioSeverity severity,
    const StressScenarioInputRow& row,
    const std::string& rule,
    const std::string& message,
    const std::string& expected,
    const std::string& actual,
    StressScenarioReport* report) const {
  report->issues.push_back(StressScenarioIssue{
      type,
      severity,
      row.Key(),
      rule,
      message,
      expected,
      actual,
  });
}


bool StressScenarioEngine::CheckEquityShockDown(
    const StressScenarioInputRow& row,
    StressScenarioReport* report) const {
  const std::string rule = "equity_shock_down";
  const double threshold = Threshold(rule, 0.250000);

  if (std::fabs(row.notional) > threshold) {
    AddIssue(
        StressScenarioIssueType::kLimitExceeded,
        StressScenarioSeverity::kError,
        row,
        rule,
        "EquityShockDown breached configured validation rule",
        "absolute metric below threshold",
        NumberText(row.notional),
        report);
    return false;
  }

  return true;
}


bool StressScenarioEngine::CheckEquityShockUp(
    const StressScenarioInputRow& row,
    StressScenarioReport* report) const {
  const std::string rule = "equity_shock_up";
  const double threshold = Threshold(rule, 0.325000);

  if (row.market_value < threshold) {
    AddIssue(
        StressScenarioIssueType::kFloorBreached,
        StressScenarioSeverity::kWarning,
        row,
        rule,
        "EquityShockUp breached configured validation rule",
        "metric above configured floor",
        NumberText(row.market_value),
        report);
    return false;
  }

  return true;
}


bool StressScenarioEngine::CheckRateShockParallel(
    const StressScenarioInputRow& row,
    StressScenarioReport* report) const {
  const std::string rule = "rate_shock_parallel";
  const double threshold = Threshold(rule, 0.400000);

  if (SafeRatio(row.quantity, row.unrealized_pnl) > threshold) {
    AddIssue(
        StressScenarioIssueType::kRatioViolation,
        StressScenarioSeverity::kError,
        row,
        rule,
        "RateShockParallel breached configured validation rule",
        "ratio below configured threshold",
        NumberText(SafeRatio(row.quantity, row.unrealized_pnl)),
        report);
    return false;
  }

  return true;
}


bool StressScenarioEngine::CheckRateShockSteepener(
    const StressScenarioInputRow& row,
    StressScenarioReport* report) const {
  const std::string rule = "rate_shock_steepener";
  const double threshold = Threshold(rule, 0.475000);

  if (row.restricted && !allow_manual_overrides_) {
    AddIssue(
        StressScenarioIssueType::kPolicyViolation,
        StressScenarioSeverity::kCritical,
        row,
        rule,
        "RateShockSteepener breached configured validation rule",
        "unrestricted row or approved override",
        "restricted row without override",
        report);
    return false;
  }

  return true;
}


bool StressScenarioEngine::CheckRateShockFlattener(
    const StressScenarioInputRow& row,
    StressScenarioReport* report) const {
  const std::string rule = "rate_shock_flattener";
  const double threshold = Threshold(rule, 0.550000);

  if (strict_mode_ && std::fabs(row.volatility - row.gross_exposure) > threshold) {
    AddIssue(
        StressScenarioIssueType::kScenarioFailure,
        StressScenarioSeverity::kWarning,
        row,
        rule,
        "RateShockFlattener breached configured validation rule",
        "strict-mode metric difference inside threshold",
        NumberText(row.volatility - row.gross_exposure),
        report);
    return false;
  }

  return true;
}


bool StressScenarioEngine::CheckCreditSpreadShock(
    const StressScenarioInputRow& row,
    StressScenarioReport* report) const {
  const std::string rule = "credit_spread_shock";
  const double threshold = Threshold(rule, 0.625000);

  if (std::fabs(row.average_daily_volume) > threshold) {
    AddIssue(
        StressScenarioIssueType::kLimitExceeded,
        StressScenarioSeverity::kError,
        row,
        rule,
        "CreditSpreadShock breached configured validation rule",
        "absolute metric below threshold",
        NumberText(row.average_daily_volume),
        report);
    return false;
  }

  return true;
}


bool StressScenarioEngine::CheckFxDevaluationShock(
    const StressScenarioInputRow& row,
    StressScenarioReport* report) const {
  const std::string rule = "fx_devaluation_shock";
  const double threshold = Threshold(rule, 0.700000);

  if (row.spread_bps < threshold) {
    AddIssue(
        StressScenarioIssueType::kFloorBreached,
        StressScenarioSeverity::kWarning,
        row,
        rule,
        "FxDevaluationShock breached configured validation rule",
        "metric above configured floor",
        NumberText(row.spread_bps),
        report);
    return false;
  }

  return true;
}


bool StressScenarioEngine::CheckCommodityShock(
    const StressScenarioInputRow& row,
    StressScenarioReport* report) const {
  const std::string rule = "commodity_shock";
  const double threshold = Threshold(rule, 0.775000);

  if (SafeRatio(row.margin_rate, row.confidence) > threshold) {
    AddIssue(
        StressScenarioIssueType::kRatioViolation,
        StressScenarioSeverity::kError,
        row,
        rule,
        "CommodityShock breached configured validation rule",
        "ratio below configured threshold",
        NumberText(SafeRatio(row.margin_rate, row.confidence)),
        report);
    return false;
  }

  return true;
}


bool StressScenarioEngine::CheckVolatilitySpike(
    const StressScenarioInputRow& row,
    StressScenarioReport* report) const {
  const std::string rule = "volatility_spike";
  const double threshold = Threshold(rule, 0.850000);

  if (row.restricted && !allow_manual_overrides_) {
    AddIssue(
        StressScenarioIssueType::kPolicyViolation,
        StressScenarioSeverity::kCritical,
        row,
        rule,
        "VolatilitySpike breached configured validation rule",
        "unrestricted row or approved override",
        "restricted row without override",
        report);
    return false;
  }

  return true;
}


bool StressScenarioEngine::CheckLiquidityFreeze(
    const StressScenarioInputRow& row,
    StressScenarioReport* report) const {
  const std::string rule = "liquidity_freeze";
  const double threshold = Threshold(rule, 0.925000);

  if (strict_mode_ && std::fabs(row.unrealized_pnl - row.fill_rate) > threshold) {
    AddIssue(
        StressScenarioIssueType::kScenarioFailure,
        StressScenarioSeverity::kWarning,
        row,
        rule,
        "LiquidityFreeze breached configured validation rule",
        "strict-mode metric difference inside threshold",
        NumberText(row.unrealized_pnl - row.fill_rate),
        report);
    return false;
  }

  return true;
}


bool StressScenarioEngine::CheckCorrelationBreakdown(
    const StressScenarioInputRow& row,
    StressScenarioReport* report) const {
  const std::string rule = "correlation_breakdown";
  const double threshold = Threshold(rule, 1.000000);

  if (std::fabs(row.cash) > threshold) {
    AddIssue(
        StressScenarioIssueType::kLimitExceeded,
        StressScenarioSeverity::kError,
        row,
        rule,
        "CorrelationBreakdown breached configured validation rule",
        "absolute metric below threshold",
        NumberText(row.cash),
        report);
    return false;
  }

  return true;
}


bool StressScenarioEngine::CheckGapOpenLoss(
    const StressScenarioInputRow& row,
    StressScenarioReport* report) const {
  const std::string rule = "gap_open_loss";
  const double threshold = Threshold(rule, 0.250000);

  if (row.gross_exposure < threshold) {
    AddIssue(
        StressScenarioIssueType::kFloorBreached,
        StressScenarioSeverity::kWarning,
        row,
        rule,
        "GapOpenLoss breached configured validation rule",
        "metric above configured floor",
        NumberText(row.gross_exposure),
        report);
    return false;
  }

  return true;
}


bool StressScenarioEngine::CheckLimitDownScenario(
    const StressScenarioInputRow& row,
    StressScenarioReport* report) const {
  const std::string rule = "limit_down_scenario";
  const double threshold = Threshold(rule, 0.325000);

  if (SafeRatio(row.net_exposure, row.settlement_amount) > threshold) {
    AddIssue(
        StressScenarioIssueType::kRatioViolation,
        StressScenarioSeverity::kError,
        row,
        rule,
        "LimitDownScenario breached configured validation rule",
        "ratio below configured threshold",
        NumberText(SafeRatio(row.net_exposure, row.settlement_amount)),
        report);
    return false;
  }

  return true;
}


bool StressScenarioEngine::CheckLimitUpScenario(
    const StressScenarioInputRow& row,
    StressScenarioReport* report) const {
  const std::string rule = "limit_up_scenario";
  const double threshold = Threshold(rule, 0.400000);

  if (row.restricted && !allow_manual_overrides_) {
    AddIssue(
        StressScenarioIssueType::kPolicyViolation,
        StressScenarioSeverity::kCritical,
        row,
        rule,
        "LimitUpScenario breached configured validation rule",
        "unrestricted row or approved override",
        "restricted row without override",
        report);
    return false;
  }

  return true;
}


bool StressScenarioEngine::CheckShortSqueezeScenario(
    const StressScenarioInputRow& row,
    StressScenarioReport* report) const {
  const std::string rule = "short_squeeze_scenario";
  const double threshold = Threshold(rule, 0.475000);

  if (strict_mode_ && std::fabs(row.confidence - row.available_liquidity) > threshold) {
    AddIssue(
        StressScenarioIssueType::kScenarioFailure,
        StressScenarioSeverity::kWarning,
        row,
        rule,
        "ShortSqueezeScenario breached configured validation rule",
        "strict-mode metric difference inside threshold",
        NumberText(row.confidence - row.available_liquidity),
        report);
    return false;
  }

  return true;
}


bool StressScenarioEngine::CheckBorrowRecallScenario(
    const StressScenarioInputRow& row,
    StressScenarioReport* report) const {
  const std::string rule = "borrow_recall_scenario";
  const double threshold = Threshold(rule, 0.550000);

  if (std::fabs(row.latency_ms) > threshold) {
    AddIssue(
        StressScenarioIssueType::kLimitExceeded,
        StressScenarioSeverity::kError,
        row,
        rule,
        "BorrowRecallScenario breached configured validation rule",
        "absolute metric below threshold",
        NumberText(row.latency_ms),
        report);
    return false;
  }

  return true;
}


bool StressScenarioEngine::CheckSettlementDelayScenario(
    const StressScenarioInputRow& row,
    StressScenarioReport* report) const {
  const std::string rule = "settlement_delay_scenario";
  const double threshold = Threshold(rule, 0.625000);

  if (row.fill_rate < threshold) {
    AddIssue(
        StressScenarioIssueType::kFloorBreached,
        StressScenarioSeverity::kWarning,
        row,
        rule,
        "SettlementDelayScenario breached configured validation rule",
        "metric above configured floor",
        NumberText(row.fill_rate),
        report);
    return false;
  }

  return true;
}


bool StressScenarioEngine::CheckMarginCallScenario(
    const StressScenarioInputRow& row,
    StressScenarioReport* report) const {
  const std::string rule = "margin_call_scenario";
  const double threshold = Threshold(rule, 0.700000);

  if (SafeRatio(row.reject_rate, row.beta) > threshold) {
    AddIssue(
        StressScenarioIssueType::kRatioViolation,
        StressScenarioSeverity::kError,
        row,
        rule,
        "MarginCallScenario breached configured validation rule",
        "ratio below configured threshold",
        NumberText(SafeRatio(row.reject_rate, row.beta)),
        report);
    return false;
  }

  return true;
}


bool StressScenarioEngine::CheckCashRunoffScenario(
    const StressScenarioInputRow& row,
    StressScenarioReport* report) const {
  const std::string rule = "cash_runoff_scenario";
  const double threshold = Threshold(rule, 0.775000);

  if (row.restricted && !allow_manual_overrides_) {
    AddIssue(
        StressScenarioIssueType::kPolicyViolation,
        StressScenarioSeverity::kCritical,
        row,
        rule,
        "CashRunoffScenario breached configured validation rule",
        "unrestricted row or approved override",
        "restricted row without override",
        report);
    return false;
  }

  return true;
}


bool StressScenarioEngine::CheckConcentrationUnwind(
    const StressScenarioInputRow& row,
    StressScenarioReport* report) const {
  const std::string rule = "concentration_unwind";
  const double threshold = Threshold(rule, 0.850000);

  if (strict_mode_ && std::fabs(row.settlement_amount - row.var_10d) > threshold) {
    AddIssue(
        StressScenarioIssueType::kScenarioFailure,
        StressScenarioSeverity::kWarning,
        row,
        rule,
        "ConcentrationUnwind breached configured validation rule",
        "strict-mode metric difference inside threshold",
        NumberText(row.settlement_amount - row.var_10d),
        report);
    return false;
  }

  return true;
}


bool StressScenarioEngine::CheckCrowdedTradeExit(
    const StressScenarioInputRow& row,
    StressScenarioReport* report) const {
  const std::string rule = "crowded_trade_exit";
  const double threshold = Threshold(rule, 0.925000);

  if (std::fabs(row.expected_amount) > threshold) {
    AddIssue(
        StressScenarioIssueType::kLimitExceeded,
        StressScenarioSeverity::kError,
        row,
        rule,
        "CrowdedTradeExit breached configured validation rule",
        "absolute metric below threshold",
        NumberText(row.expected_amount),
        report);
    return false;
  }

  return true;
}


bool StressScenarioEngine::CheckIndexRebalanceShock(
    const StressScenarioInputRow& row,
    StressScenarioReport* report) const {
  const std::string rule = "index_rebalance_shock";
  const double threshold = Threshold(rule, 1.000000);

  if (row.available_liquidity < threshold) {
    AddIssue(
        StressScenarioIssueType::kFloorBreached,
        StressScenarioSeverity::kWarning,
        row,
        rule,
        "IndexRebalanceShock breached configured validation rule",
        "metric above configured floor",
        NumberText(row.available_liquidity),
        report);
    return false;
  }

  return true;
}


bool StressScenarioEngine::CheckCorporateActionShock(
    const StressScenarioInputRow& row,
    StressScenarioReport* report) const {
  const std::string rule = "corporate_action_shock";
  const double threshold = Threshold(rule, 0.250000);

  if (SafeRatio(row.correlation, row.fee_rate) > threshold) {
    AddIssue(
        StressScenarioIssueType::kRatioViolation,
        StressScenarioSeverity::kError,
        row,
        rule,
        "CorporateActionShock breached configured validation rule",
        "ratio below configured threshold",
        NumberText(SafeRatio(row.correlation, row.fee_rate)),
        report);
    return false;
  }

  return true;
}


bool StressScenarioEngine::CheckDividendCutShock(
    const StressScenarioInputRow& row,
    StressScenarioReport* report) const {
  const std::string rule = "dividend_cut_shock";
  const double threshold = Threshold(rule, 0.325000);

  if (row.restricted && !allow_manual_overrides_) {
    AddIssue(
        StressScenarioIssueType::kPolicyViolation,
        StressScenarioSeverity::kCritical,
        row,
        rule,
        "DividendCutShock breached configured validation rule",
        "unrestricted row or approved override",
        "restricted row without override",
        report);
    return false;
  }

  return true;
}


bool StressScenarioEngine::CheckEarningsGapScenario(
    const StressScenarioInputRow& row,
    StressScenarioReport* report) const {
  const std::string rule = "earnings_gap_scenario";
  const double threshold = Threshold(rule, 0.400000);

  if (strict_mode_ && std::fabs(row.beta - row.turnover_rate) > threshold) {
    AddIssue(
        StressScenarioIssueType::kScenarioFailure,
        StressScenarioSeverity::kWarning,
        row,
        rule,
        "EarningsGapScenario breached configured validation rule",
        "strict-mode metric difference inside threshold",
        NumberText(row.beta - row.turnover_rate),
        report);
    return false;
  }

  return true;
}


bool StressScenarioEngine::CheckMacroEventScenario(
    const StressScenarioInputRow& row,
    StressScenarioReport* report) const {
  const std::string rule = "macro_event_scenario";
  const double threshold = Threshold(rule, 0.475000);

  if (std::fabs(row.var_1d) > threshold) {
    AddIssue(
        StressScenarioIssueType::kLimitExceeded,
        StressScenarioSeverity::kError,
        row,
        rule,
        "MacroEventScenario breached configured validation rule",
        "absolute metric below threshold",
        NumberText(row.var_1d),
        report);
    return false;
  }

  return true;
}


bool StressScenarioEngine::CheckDefaultScenario(
    const StressScenarioInputRow& row,
    StressScenarioReport* report) const {
  const std::string rule = "default_scenario";
  const double threshold = Threshold(rule, 0.550000);

  if (row.var_10d < threshold) {
    AddIssue(
        StressScenarioIssueType::kFloorBreached,
        StressScenarioSeverity::kWarning,
        row,
        rule,
        "DefaultScenario breached configured validation rule",
        "metric above configured floor",
        NumberText(row.var_10d),
        report);
    return false;
  }

  return true;
}


bool StressScenarioEngine::CheckCounterpartyFailure(
    const StressScenarioInputRow& row,
    StressScenarioReport* report) const {
  const std::string rule = "counterparty_failure";
  const double threshold = Threshold(rule, 0.625000);

  if (SafeRatio(row.haircut, row.quantity) > threshold) {
    AddIssue(
        StressScenarioIssueType::kRatioViolation,
        StressScenarioSeverity::kError,
        row,
        rule,
        "CounterpartyFailure breached configured validation rule",
        "ratio below configured threshold",
        NumberText(SafeRatio(row.haircut, row.quantity)),
        report);
    return false;
  }

  return true;
}


bool StressScenarioEngine::CheckVenueHaltScenario(
    const StressScenarioInputRow& row,
    StressScenarioReport* report) const {
  const std::string rule = "venue_halt_scenario";
  const double threshold = Threshold(rule, 0.700000);

  if (row.restricted && !allow_manual_overrides_) {
    AddIssue(
        StressScenarioIssueType::kPolicyViolation,
        StressScenarioSeverity::kCritical,
        row,
        rule,
        "VenueHaltScenario breached configured validation rule",
        "unrestricted row or approved override",
        "restricted row without override",
        report);
    return false;
  }

  return true;
}


bool StressScenarioEngine::CheckCircuitBreakerScenario(
    const StressScenarioInputRow& row,
    StressScenarioReport* report) const {
  const std::string rule = "circuit_breaker_scenario";
  const double threshold = Threshold(rule, 0.775000);

  if (strict_mode_ && std::fabs(row.fee_rate - row.volatility) > threshold) {
    AddIssue(
        StressScenarioIssueType::kScenarioFailure,
        StressScenarioSeverity::kWarning,
        row,
        rule,
        "CircuitBreakerScenario breached configured validation rule",
        "strict-mode metric difference inside threshold",
        NumberText(row.fee_rate - row.volatility),
        report);
    return false;
  }

  return true;
}


bool StressScenarioEngine::CheckSlippageWidening(
    const StressScenarioInputRow& row,
    StressScenarioReport* report) const {
  const std::string rule = "slippage_widening";
  const double threshold = Threshold(rule, 0.850000);

  if (std::fabs(row.slippage_bps) > threshold) {
    AddIssue(
        StressScenarioIssueType::kLimitExceeded,
        StressScenarioSeverity::kError,
        row,
        rule,
        "SlippageWidening breached configured validation rule",
        "absolute metric below threshold",
        NumberText(row.slippage_bps),
        report);
    return false;
  }

  return true;
}


bool StressScenarioEngine::CheckSpreadExplosion(
    const StressScenarioInputRow& row,
    StressScenarioReport* report) const {
  const std::string rule = "spread_explosion";
  const double threshold = Threshold(rule, 0.925000);

  if (row.turnover_rate < threshold) {
    AddIssue(
        StressScenarioIssueType::kFloorBreached,
        StressScenarioSeverity::kWarning,
        row,
        rule,
        "SpreadExplosion breached configured validation rule",
        "metric above configured floor",
        NumberText(row.turnover_rate),
        report);
    return false;
  }

  return true;
}


bool StressScenarioEngine::CheckVolumeCollapse(
    const StressScenarioInputRow& row,
    StressScenarioReport* report) const {
  const std::string rule = "volume_collapse";
  const double threshold = Threshold(rule, 1.000000);

  if (SafeRatio(row.notional, row.margin_rate) > threshold) {
    AddIssue(
        StressScenarioIssueType::kRatioViolation,
        StressScenarioSeverity::kError,
        row,
        rule,
        "VolumeCollapse breached configured validation rule",
        "ratio below configured threshold",
        NumberText(SafeRatio(row.notional, row.margin_rate)),
        report);
    return false;
  }

  return true;
}


bool StressScenarioEngine::CheckBetaExpansion(
    const StressScenarioInputRow& row,
    StressScenarioReport* report) const {
  const std::string rule = "beta_expansion";
  const double threshold = Threshold(rule, 0.250000);

  if (row.restricted && !allow_manual_overrides_) {
    AddIssue(
        StressScenarioIssueType::kPolicyViolation,
        StressScenarioSeverity::kCritical,
        row,
        rule,
        "BetaExpansion breached configured validation rule",
        "unrestricted row or approved override",
        "restricted row without override",
        report);
    return false;
  }

  return true;
}


bool StressScenarioEngine::CheckBasisRiskExpansion(
    const StressScenarioInputRow& row,
    StressScenarioReport* report) const {
  const std::string rule = "basis_risk_expansion";
  const double threshold = Threshold(rule, 0.325000);

  if (strict_mode_ && std::fabs(row.quantity - row.unrealized_pnl) > threshold) {
    AddIssue(
        StressScenarioIssueType::kScenarioFailure,
        StressScenarioSeverity::kWarning,
        row,
        rule,
        "BasisRiskExpansion breached configured validation rule",
        "strict-mode metric difference inside threshold",
        NumberText(row.quantity - row.unrealized_pnl),
        report);
    return false;
  }

  return true;
}


bool StressScenarioEngine::CheckCrossAssetContagion(
    const StressScenarioInputRow& row,
    StressScenarioReport* report) const {
  const std::string rule = "cross_asset_contagion";
  const double threshold = Threshold(rule, 0.400000);

  if (std::fabs(row.price) > threshold) {
    AddIssue(
        StressScenarioIssueType::kLimitExceeded,
        StressScenarioSeverity::kError,
        row,
        rule,
        "CrossAssetContagion breached configured validation rule",
        "absolute metric below threshold",
        NumberText(row.price),
        report);
    return false;
  }

  return true;
}


bool StressScenarioEngine::CheckVolSurfaceTwist(
    const StressScenarioInputRow& row,
    StressScenarioReport* report) const {
  const std::string rule = "vol_surface_twist";
  const double threshold = Threshold(rule, 0.475000);

  if (row.volatility < threshold) {
    AddIssue(
        StressScenarioIssueType::kFloorBreached,
        StressScenarioSeverity::kWarning,
        row,
        rule,
        "VolSurfaceTwist breached configured validation rule",
        "metric above configured floor",
        NumberText(row.volatility),
        report);
    return false;
  }

  return true;
}


bool StressScenarioEngine::CheckCurveConvexityShock(
    const StressScenarioInputRow& row,
    StressScenarioReport* report) const {
  const std::string rule = "curve_convexity_shock";
  const double threshold = Threshold(rule, 0.550000);

  if (SafeRatio(row.average_daily_volume, row.net_exposure) > threshold) {
    AddIssue(
        StressScenarioIssueType::kRatioViolation,
        StressScenarioSeverity::kError,
        row,
        rule,
        "CurveConvexityShock breached configured validation rule",
        "ratio below configured threshold",
        NumberText(SafeRatio(row.average_daily_volume, row.net_exposure)),
        report);
    return false;
  }

  return true;
}


bool StressScenarioEngine::CheckTailLossScenario(
    const StressScenarioInputRow& row,
    StressScenarioReport* report) const {
  const std::string rule = "tail_loss_scenario";
  const double threshold = Threshold(rule, 0.625000);

  if (row.restricted && !allow_manual_overrides_) {
    AddIssue(
        StressScenarioIssueType::kPolicyViolation,
        StressScenarioSeverity::kCritical,
        row,
        rule,
        "TailLossScenario breached configured validation rule",
        "unrestricted row or approved override",
        "restricted row without override",
        report);
    return false;
  }

  return true;
}


bool StressScenarioEngine::CheckRecoveryDelayScenario(
    const StressScenarioInputRow& row,
    StressScenarioReport* report) const {
  const std::string rule = "recovery_delay_scenario";
  const double threshold = Threshold(rule, 0.700000);

  if (strict_mode_ && std::fabs(row.margin_rate - row.confidence) > threshold) {
    AddIssue(
        StressScenarioIssueType::kScenarioFailure,
        StressScenarioSeverity::kWarning,
        row,
        rule,
        "RecoveryDelayScenario breached configured validation rule",
        "strict-mode metric difference inside threshold",
        NumberText(row.margin_rate - row.confidence),
        report);
    return false;
  }

  return true;
}


std::string StressScenarioSeverityName(StressScenarioSeverity severity) {
  switch (severity) {
    case StressScenarioSeverity::kInfo:
      return "info";
    case StressScenarioSeverity::kWarning:
      return "warning";
    case StressScenarioSeverity::kError:
      return "error";
    case StressScenarioSeverity::kCritical:
      return "critical";
  }
  return "unknown";
}

std::string StressScenarioIssueTypeName(StressScenarioIssueType type) {
  switch (type) {
    case StressScenarioIssueType::kInvalidInput:
      return "invalid_input";
    case StressScenarioIssueType::kMissingIdentifier:
      return "missing_identifier";
    case StressScenarioIssueType::kNonFiniteMetric:
      return "non_finite_metric";
    case StressScenarioIssueType::kThresholdBreach:
      return "threshold_breach";
    case StressScenarioIssueType::kLimitExceeded:
      return "limit_exceeded";
    case StressScenarioIssueType::kFloorBreached:
      return "floor_breached";
    case StressScenarioIssueType::kRatioViolation:
      return "ratio_violation";
    case StressScenarioIssueType::kPolicyViolation:
      return "policy_violation";
    case StressScenarioIssueType::kScenarioFailure:
      return "scenario_failure";
    case StressScenarioIssueType::kAggregationFailure:
      return "aggregation_failure";
  }
  return "unknown";
}

}  // namespace marketdata
