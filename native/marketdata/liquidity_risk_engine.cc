
#include "liquidity_risk_engine.h"

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

bool IsError(LiquidityRiskSeverity severity) {
  return severity == LiquidityRiskSeverity::kError ||
         severity == LiquidityRiskSeverity::kCritical;
}

double SafeRatio(double numerator, double denominator) {
  if (!IsFinite(numerator) || !IsFinite(denominator) ||
      std::fabs(denominator) < 0.0000001) {
    return std::numeric_limits<double>::infinity();
  }
  return std::fabs(numerator / denominator);
}

}  // namespace

std::string LiquidityRiskInputRow::Key() const {
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

bool LiquidityRiskReport::Ok() const {
  for (const LiquidityRiskIssue& issue : issues) {
    if (IsError(issue.severity)) {
      return false;
    }
  }
  return true;
}

std::uint64_t LiquidityRiskReport::ErrorCount() const {
  std::uint64_t total = 0;
  for (const LiquidityRiskIssue& issue : issues) {
    if (IsError(issue.severity)) {
      ++total;
    }
  }
  return total;
}

std::uint64_t LiquidityRiskReport::WarningCount() const {
  std::uint64_t total = 0;
  for (const LiquidityRiskIssue& issue : issues) {
    if (issue.severity == LiquidityRiskSeverity::kWarning) {
      ++total;
    }
  }
  return total;
}

LiquidityRiskEngine::LiquidityRiskEngine()
    : strict_mode_(false),
      allow_manual_overrides_(false) {
  thresholds_["a_d_v_participation_cap"] = 0.250000;
  thresholds_["spread_bps_cap"] = 0.325000;
  thresholds_["depth_floor"] = 0.400000;
  thresholds_["order_book_imbalance_cap"] = 0.475000;
  thresholds_["auction_dependency_cap"] = 0.550000;
  thresholds_["dark_liquidity_cap"] = 0.625000;
  thresholds_["displayed_liquidity_floor"] = 0.700000;
  thresholds_["hidden_liquidity_decay"] = 0.775000;
  thresholds_["volume_collapse_cap"] = 0.850000;
  thresholds_["turnover_floor"] = 0.925000;
  thresholds_["days_to_liquidate_cap"] = 1.000000;
  thresholds_["liquidation_cost_cap"] = 0.250000;
  thresholds_["slippage_stress_cap"] = 0.325000;
  thresholds_["liquidity_score_floor"] = 0.400000;
  thresholds_["venue_fragmentation_cap"] = 0.475000;
  thresholds_["market_maker_withdrawal_cap"] = 0.550000;
  thresholds_["halt_probability_cap"] = 0.625000;
  thresholds_["gap_risk_cap"] = 0.700000;
  thresholds_["borrow_liquidity_floor"] = 0.775000;
  thresholds_["funding_liquidity_floor"] = 0.850000;
  thresholds_["collateral_liquidity_floor"] = 0.925000;
  thresholds_["redemption_pressure_cap"] = 1.000000;
  thresholds_["cash_buffer_floor"] = 0.250000;
  thresholds_["settlement_liquidity_cap"] = 0.325000;
  thresholds_["intraday_liquidity_cap"] = 0.400000;
  thresholds_["overnight_liquidity_cap"] = 0.475000;
  thresholds_["cross_currency_funding_cap"] = 0.550000;
  thresholds_["repo_funding_cap"] = 0.625000;
  thresholds_["f_x_swap_funding_cap"] = 0.700000;
  thresholds_["concentration_liquidity_cap"] = 0.775000;
  thresholds_["e_t_f_create_redeem_cap"] = 0.850000;
  thresholds_["underlying_basket_liquidity_cap"] = 0.925000;
  thresholds_["index_rebalance_liquidity_cap"] = 1.000000;
  thresholds_["event_liquidity_cap"] = 0.250000;
  thresholds_["news_shock_liquidity_cap"] = 0.325000;
  thresholds_["limit_order_book_thinness_cap"] = 0.400000;
  thresholds_["queue_position_decay_cap"] = 0.475000;
  thresholds_["venue_outage_liquidity_cap"] = 0.550000;
  thresholds_["quote_fade_cap"] = 0.625000;
  thresholds_["trade_size_bucket_cap"] = 0.700000;
}

void LiquidityRiskEngine::SetThreshold(const std::string& rule, double value) {
  if (!IsFinite(value) || value < 0.0) {
    throw std::invalid_argument("threshold must be finite and non-negative");
  }
  thresholds_[rule] = value;
}

void LiquidityRiskEngine::SetStrictMode(bool value) {
  strict_mode_ = value;
}

void LiquidityRiskEngine::SetAllowManualOverrides(bool value) {
  allow_manual_overrides_ = value;
}

LiquidityRiskReport LiquidityRiskEngine::Analyze(
    const std::vector<LiquidityRiskInputRow>& rows) const {
  LiquidityRiskReport report;

  for (const LiquidityRiskInputRow& row : rows) {
    ++report.checked_rows;
    report.gross_notional += std::fabs(row.notional);
    report.net_notional += row.notional;
    report.total_stress_loss += row.stress_loss;
    report.max_abs_exposure =
        std::max(report.max_abs_exposure, std::fabs(row.gross_exposure));

    if (!ValidateCommonFields(row, &report)) {
      continue;
    }

    CheckADVParticipationCap(row, &report);
    CheckSpreadBpsCap(row, &report);
    CheckDepthFloor(row, &report);
    CheckOrderBookImbalanceCap(row, &report);
    CheckAuctionDependencyCap(row, &report);
    CheckDarkLiquidityCap(row, &report);
    CheckDisplayedLiquidityFloor(row, &report);
    CheckHiddenLiquidityDecay(row, &report);
    CheckVolumeCollapseCap(row, &report);
    CheckTurnoverFloor(row, &report);
    CheckDaysToLiquidateCap(row, &report);
    CheckLiquidationCostCap(row, &report);
    CheckSlippageStressCap(row, &report);
    CheckLiquidityScoreFloor(row, &report);
    CheckVenueFragmentationCap(row, &report);
    CheckMarketMakerWithdrawalCap(row, &report);
    CheckHaltProbabilityCap(row, &report);
    CheckGapRiskCap(row, &report);
    CheckBorrowLiquidityFloor(row, &report);
    CheckFundingLiquidityFloor(row, &report);
    CheckCollateralLiquidityFloor(row, &report);
    CheckRedemptionPressureCap(row, &report);
    CheckCashBufferFloor(row, &report);
    CheckSettlementLiquidityCap(row, &report);
    CheckIntradayLiquidityCap(row, &report);
    CheckOvernightLiquidityCap(row, &report);
    CheckCrossCurrencyFundingCap(row, &report);
    CheckRepoFundingCap(row, &report);
    CheckFXSwapFundingCap(row, &report);
    CheckConcentrationLiquidityCap(row, &report);
    CheckETFCreateRedeemCap(row, &report);
    CheckUnderlyingBasketLiquidityCap(row, &report);
    CheckIndexRebalanceLiquidityCap(row, &report);
    CheckEventLiquidityCap(row, &report);
    CheckNewsShockLiquidityCap(row, &report);
    CheckLimitOrderBookThinnessCap(row, &report);
    CheckQueuePositionDecayCap(row, &report);
    CheckVenueOutageLiquidityCap(row, &report);
    CheckQuoteFadeCap(row, &report);
    CheckTradeSizeBucketCap(row, &report);
  }

  report.error_rows = report.ErrorCount();
  report.warning_rows = report.WarningCount();
  return report;
}

double LiquidityRiskEngine::Threshold(
    const std::string& rule,
    double fallback) const {
  const auto it = thresholds_.find(rule);
  if (it == thresholds_.end()) {
    return fallback;
  }
  return it->second;
}

bool LiquidityRiskEngine::ValidateCommonFields(
    const LiquidityRiskInputRow& row,
    LiquidityRiskReport* report) const {
  bool valid = true;

  if (row.portfolio_id.empty() || row.account_id.empty()) {
    ++report->invalid_rows;
    AddIssue(
        LiquidityRiskIssueType::kMissingIdentifier,
        LiquidityRiskSeverity::kError,
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
        LiquidityRiskIssueType::kNonFiniteMetric,
        LiquidityRiskSeverity::kError,
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
        LiquidityRiskIssueType::kPolicyViolation,
        LiquidityRiskSeverity::kWarning,
        row,
        "manual_override",
        "manual override present but overrides disabled",
        "no override",
        "manual override",
        report);
  }

  return valid;
}

void LiquidityRiskEngine::AddIssue(
    LiquidityRiskIssueType type,
    LiquidityRiskSeverity severity,
    const LiquidityRiskInputRow& row,
    const std::string& rule,
    const std::string& message,
    const std::string& expected,
    const std::string& actual,
    LiquidityRiskReport* report) const {
  report->issues.push_back(LiquidityRiskIssue{
      type,
      severity,
      row.Key(),
      rule,
      message,
      expected,
      actual,
  });
}


bool LiquidityRiskEngine::CheckADVParticipationCap(
    const LiquidityRiskInputRow& row,
    LiquidityRiskReport* report) const {
  const std::string rule = "a_d_v_participation_cap";
  const double threshold = Threshold(rule, 0.250000);

  if (std::fabs(row.notional) > threshold) {
    AddIssue(
        LiquidityRiskIssueType::kLimitExceeded,
        LiquidityRiskSeverity::kError,
        row,
        rule,
        "ADVParticipationCap breached configured validation rule",
        "absolute metric below threshold",
        NumberText(row.notional),
        report);
    return false;
  }

  return true;
}


bool LiquidityRiskEngine::CheckSpreadBpsCap(
    const LiquidityRiskInputRow& row,
    LiquidityRiskReport* report) const {
  const std::string rule = "spread_bps_cap";
  const double threshold = Threshold(rule, 0.325000);

  if (row.market_value < threshold) {
    AddIssue(
        LiquidityRiskIssueType::kFloorBreached,
        LiquidityRiskSeverity::kWarning,
        row,
        rule,
        "SpreadBpsCap breached configured validation rule",
        "metric above configured floor",
        NumberText(row.market_value),
        report);
    return false;
  }

  return true;
}


bool LiquidityRiskEngine::CheckDepthFloor(
    const LiquidityRiskInputRow& row,
    LiquidityRiskReport* report) const {
  const std::string rule = "depth_floor";
  const double threshold = Threshold(rule, 0.400000);

  if (SafeRatio(row.quantity, row.unrealized_pnl) > threshold) {
    AddIssue(
        LiquidityRiskIssueType::kRatioViolation,
        LiquidityRiskSeverity::kError,
        row,
        rule,
        "DepthFloor breached configured validation rule",
        "ratio below configured threshold",
        NumberText(SafeRatio(row.quantity, row.unrealized_pnl)),
        report);
    return false;
  }

  return true;
}


bool LiquidityRiskEngine::CheckOrderBookImbalanceCap(
    const LiquidityRiskInputRow& row,
    LiquidityRiskReport* report) const {
  const std::string rule = "order_book_imbalance_cap";
  const double threshold = Threshold(rule, 0.475000);

  if (row.restricted && !allow_manual_overrides_) {
    AddIssue(
        LiquidityRiskIssueType::kPolicyViolation,
        LiquidityRiskSeverity::kCritical,
        row,
        rule,
        "OrderBookImbalanceCap breached configured validation rule",
        "unrestricted row or approved override",
        "restricted row without override",
        report);
    return false;
  }

  return true;
}


bool LiquidityRiskEngine::CheckAuctionDependencyCap(
    const LiquidityRiskInputRow& row,
    LiquidityRiskReport* report) const {
  const std::string rule = "auction_dependency_cap";
  const double threshold = Threshold(rule, 0.550000);

  if (strict_mode_ && std::fabs(row.volatility - row.gross_exposure) > threshold) {
    AddIssue(
        LiquidityRiskIssueType::kScenarioFailure,
        LiquidityRiskSeverity::kWarning,
        row,
        rule,
        "AuctionDependencyCap breached configured validation rule",
        "strict-mode metric difference inside threshold",
        NumberText(row.volatility - row.gross_exposure),
        report);
    return false;
  }

  return true;
}


bool LiquidityRiskEngine::CheckDarkLiquidityCap(
    const LiquidityRiskInputRow& row,
    LiquidityRiskReport* report) const {
  const std::string rule = "dark_liquidity_cap";
  const double threshold = Threshold(rule, 0.625000);

  if (std::fabs(row.average_daily_volume) > threshold) {
    AddIssue(
        LiquidityRiskIssueType::kLimitExceeded,
        LiquidityRiskSeverity::kError,
        row,
        rule,
        "DarkLiquidityCap breached configured validation rule",
        "absolute metric below threshold",
        NumberText(row.average_daily_volume),
        report);
    return false;
  }

  return true;
}


bool LiquidityRiskEngine::CheckDisplayedLiquidityFloor(
    const LiquidityRiskInputRow& row,
    LiquidityRiskReport* report) const {
  const std::string rule = "displayed_liquidity_floor";
  const double threshold = Threshold(rule, 0.700000);

  if (row.spread_bps < threshold) {
    AddIssue(
        LiquidityRiskIssueType::kFloorBreached,
        LiquidityRiskSeverity::kWarning,
        row,
        rule,
        "DisplayedLiquidityFloor breached configured validation rule",
        "metric above configured floor",
        NumberText(row.spread_bps),
        report);
    return false;
  }

  return true;
}


bool LiquidityRiskEngine::CheckHiddenLiquidityDecay(
    const LiquidityRiskInputRow& row,
    LiquidityRiskReport* report) const {
  const std::string rule = "hidden_liquidity_decay";
  const double threshold = Threshold(rule, 0.775000);

  if (SafeRatio(row.margin_rate, row.confidence) > threshold) {
    AddIssue(
        LiquidityRiskIssueType::kRatioViolation,
        LiquidityRiskSeverity::kError,
        row,
        rule,
        "HiddenLiquidityDecay breached configured validation rule",
        "ratio below configured threshold",
        NumberText(SafeRatio(row.margin_rate, row.confidence)),
        report);
    return false;
  }

  return true;
}


bool LiquidityRiskEngine::CheckVolumeCollapseCap(
    const LiquidityRiskInputRow& row,
    LiquidityRiskReport* report) const {
  const std::string rule = "volume_collapse_cap";
  const double threshold = Threshold(rule, 0.850000);

  if (row.restricted && !allow_manual_overrides_) {
    AddIssue(
        LiquidityRiskIssueType::kPolicyViolation,
        LiquidityRiskSeverity::kCritical,
        row,
        rule,
        "VolumeCollapseCap breached configured validation rule",
        "unrestricted row or approved override",
        "restricted row without override",
        report);
    return false;
  }

  return true;
}


bool LiquidityRiskEngine::CheckTurnoverFloor(
    const LiquidityRiskInputRow& row,
    LiquidityRiskReport* report) const {
  const std::string rule = "turnover_floor";
  const double threshold = Threshold(rule, 0.925000);

  if (strict_mode_ && std::fabs(row.unrealized_pnl - row.fill_rate) > threshold) {
    AddIssue(
        LiquidityRiskIssueType::kScenarioFailure,
        LiquidityRiskSeverity::kWarning,
        row,
        rule,
        "TurnoverFloor breached configured validation rule",
        "strict-mode metric difference inside threshold",
        NumberText(row.unrealized_pnl - row.fill_rate),
        report);
    return false;
  }

  return true;
}


bool LiquidityRiskEngine::CheckDaysToLiquidateCap(
    const LiquidityRiskInputRow& row,
    LiquidityRiskReport* report) const {
  const std::string rule = "days_to_liquidate_cap";
  const double threshold = Threshold(rule, 1.000000);

  if (std::fabs(row.cash) > threshold) {
    AddIssue(
        LiquidityRiskIssueType::kLimitExceeded,
        LiquidityRiskSeverity::kError,
        row,
        rule,
        "DaysToLiquidateCap breached configured validation rule",
        "absolute metric below threshold",
        NumberText(row.cash),
        report);
    return false;
  }

  return true;
}


bool LiquidityRiskEngine::CheckLiquidationCostCap(
    const LiquidityRiskInputRow& row,
    LiquidityRiskReport* report) const {
  const std::string rule = "liquidation_cost_cap";
  const double threshold = Threshold(rule, 0.250000);

  if (row.gross_exposure < threshold) {
    AddIssue(
        LiquidityRiskIssueType::kFloorBreached,
        LiquidityRiskSeverity::kWarning,
        row,
        rule,
        "LiquidationCostCap breached configured validation rule",
        "metric above configured floor",
        NumberText(row.gross_exposure),
        report);
    return false;
  }

  return true;
}


bool LiquidityRiskEngine::CheckSlippageStressCap(
    const LiquidityRiskInputRow& row,
    LiquidityRiskReport* report) const {
  const std::string rule = "slippage_stress_cap";
  const double threshold = Threshold(rule, 0.325000);

  if (SafeRatio(row.net_exposure, row.settlement_amount) > threshold) {
    AddIssue(
        LiquidityRiskIssueType::kRatioViolation,
        LiquidityRiskSeverity::kError,
        row,
        rule,
        "SlippageStressCap breached configured validation rule",
        "ratio below configured threshold",
        NumberText(SafeRatio(row.net_exposure, row.settlement_amount)),
        report);
    return false;
  }

  return true;
}


bool LiquidityRiskEngine::CheckLiquidityScoreFloor(
    const LiquidityRiskInputRow& row,
    LiquidityRiskReport* report) const {
  const std::string rule = "liquidity_score_floor";
  const double threshold = Threshold(rule, 0.400000);

  if (row.restricted && !allow_manual_overrides_) {
    AddIssue(
        LiquidityRiskIssueType::kPolicyViolation,
        LiquidityRiskSeverity::kCritical,
        row,
        rule,
        "LiquidityScoreFloor breached configured validation rule",
        "unrestricted row or approved override",
        "restricted row without override",
        report);
    return false;
  }

  return true;
}


bool LiquidityRiskEngine::CheckVenueFragmentationCap(
    const LiquidityRiskInputRow& row,
    LiquidityRiskReport* report) const {
  const std::string rule = "venue_fragmentation_cap";
  const double threshold = Threshold(rule, 0.475000);

  if (strict_mode_ && std::fabs(row.confidence - row.available_liquidity) > threshold) {
    AddIssue(
        LiquidityRiskIssueType::kScenarioFailure,
        LiquidityRiskSeverity::kWarning,
        row,
        rule,
        "VenueFragmentationCap breached configured validation rule",
        "strict-mode metric difference inside threshold",
        NumberText(row.confidence - row.available_liquidity),
        report);
    return false;
  }

  return true;
}


bool LiquidityRiskEngine::CheckMarketMakerWithdrawalCap(
    const LiquidityRiskInputRow& row,
    LiquidityRiskReport* report) const {
  const std::string rule = "market_maker_withdrawal_cap";
  const double threshold = Threshold(rule, 0.550000);

  if (std::fabs(row.latency_ms) > threshold) {
    AddIssue(
        LiquidityRiskIssueType::kLimitExceeded,
        LiquidityRiskSeverity::kError,
        row,
        rule,
        "MarketMakerWithdrawalCap breached configured validation rule",
        "absolute metric below threshold",
        NumberText(row.latency_ms),
        report);
    return false;
  }

  return true;
}


bool LiquidityRiskEngine::CheckHaltProbabilityCap(
    const LiquidityRiskInputRow& row,
    LiquidityRiskReport* report) const {
  const std::string rule = "halt_probability_cap";
  const double threshold = Threshold(rule, 0.625000);

  if (row.fill_rate < threshold) {
    AddIssue(
        LiquidityRiskIssueType::kFloorBreached,
        LiquidityRiskSeverity::kWarning,
        row,
        rule,
        "HaltProbabilityCap breached configured validation rule",
        "metric above configured floor",
        NumberText(row.fill_rate),
        report);
    return false;
  }

  return true;
}


bool LiquidityRiskEngine::CheckGapRiskCap(
    const LiquidityRiskInputRow& row,
    LiquidityRiskReport* report) const {
  const std::string rule = "gap_risk_cap";
  const double threshold = Threshold(rule, 0.700000);

  if (SafeRatio(row.reject_rate, row.beta) > threshold) {
    AddIssue(
        LiquidityRiskIssueType::kRatioViolation,
        LiquidityRiskSeverity::kError,
        row,
        rule,
        "GapRiskCap breached configured validation rule",
        "ratio below configured threshold",
        NumberText(SafeRatio(row.reject_rate, row.beta)),
        report);
    return false;
  }

  return true;
}


bool LiquidityRiskEngine::CheckBorrowLiquidityFloor(
    const LiquidityRiskInputRow& row,
    LiquidityRiskReport* report) const {
  const std::string rule = "borrow_liquidity_floor";
  const double threshold = Threshold(rule, 0.775000);

  if (row.restricted && !allow_manual_overrides_) {
    AddIssue(
        LiquidityRiskIssueType::kPolicyViolation,
        LiquidityRiskSeverity::kCritical,
        row,
        rule,
        "BorrowLiquidityFloor breached configured validation rule",
        "unrestricted row or approved override",
        "restricted row without override",
        report);
    return false;
  }

  return true;
}


bool LiquidityRiskEngine::CheckFundingLiquidityFloor(
    const LiquidityRiskInputRow& row,
    LiquidityRiskReport* report) const {
  const std::string rule = "funding_liquidity_floor";
  const double threshold = Threshold(rule, 0.850000);

  if (strict_mode_ && std::fabs(row.settlement_amount - row.var_10d) > threshold) {
    AddIssue(
        LiquidityRiskIssueType::kScenarioFailure,
        LiquidityRiskSeverity::kWarning,
        row,
        rule,
        "FundingLiquidityFloor breached configured validation rule",
        "strict-mode metric difference inside threshold",
        NumberText(row.settlement_amount - row.var_10d),
        report);
    return false;
  }

  return true;
}


bool LiquidityRiskEngine::CheckCollateralLiquidityFloor(
    const LiquidityRiskInputRow& row,
    LiquidityRiskReport* report) const {
  const std::string rule = "collateral_liquidity_floor";
  const double threshold = Threshold(rule, 0.925000);

  if (std::fabs(row.expected_amount) > threshold) {
    AddIssue(
        LiquidityRiskIssueType::kLimitExceeded,
        LiquidityRiskSeverity::kError,
        row,
        rule,
        "CollateralLiquidityFloor breached configured validation rule",
        "absolute metric below threshold",
        NumberText(row.expected_amount),
        report);
    return false;
  }

  return true;
}


bool LiquidityRiskEngine::CheckRedemptionPressureCap(
    const LiquidityRiskInputRow& row,
    LiquidityRiskReport* report) const {
  const std::string rule = "redemption_pressure_cap";
  const double threshold = Threshold(rule, 1.000000);

  if (row.available_liquidity < threshold) {
    AddIssue(
        LiquidityRiskIssueType::kFloorBreached,
        LiquidityRiskSeverity::kWarning,
        row,
        rule,
        "RedemptionPressureCap breached configured validation rule",
        "metric above configured floor",
        NumberText(row.available_liquidity),
        report);
    return false;
  }

  return true;
}


bool LiquidityRiskEngine::CheckCashBufferFloor(
    const LiquidityRiskInputRow& row,
    LiquidityRiskReport* report) const {
  const std::string rule = "cash_buffer_floor";
  const double threshold = Threshold(rule, 0.250000);

  if (SafeRatio(row.correlation, row.fee_rate) > threshold) {
    AddIssue(
        LiquidityRiskIssueType::kRatioViolation,
        LiquidityRiskSeverity::kError,
        row,
        rule,
        "CashBufferFloor breached configured validation rule",
        "ratio below configured threshold",
        NumberText(SafeRatio(row.correlation, row.fee_rate)),
        report);
    return false;
  }

  return true;
}


bool LiquidityRiskEngine::CheckSettlementLiquidityCap(
    const LiquidityRiskInputRow& row,
    LiquidityRiskReport* report) const {
  const std::string rule = "settlement_liquidity_cap";
  const double threshold = Threshold(rule, 0.325000);

  if (row.restricted && !allow_manual_overrides_) {
    AddIssue(
        LiquidityRiskIssueType::kPolicyViolation,
        LiquidityRiskSeverity::kCritical,
        row,
        rule,
        "SettlementLiquidityCap breached configured validation rule",
        "unrestricted row or approved override",
        "restricted row without override",
        report);
    return false;
  }

  return true;
}


bool LiquidityRiskEngine::CheckIntradayLiquidityCap(
    const LiquidityRiskInputRow& row,
    LiquidityRiskReport* report) const {
  const std::string rule = "intraday_liquidity_cap";
  const double threshold = Threshold(rule, 0.400000);

  if (strict_mode_ && std::fabs(row.beta - row.turnover_rate) > threshold) {
    AddIssue(
        LiquidityRiskIssueType::kScenarioFailure,
        LiquidityRiskSeverity::kWarning,
        row,
        rule,
        "IntradayLiquidityCap breached configured validation rule",
        "strict-mode metric difference inside threshold",
        NumberText(row.beta - row.turnover_rate),
        report);
    return false;
  }

  return true;
}


bool LiquidityRiskEngine::CheckOvernightLiquidityCap(
    const LiquidityRiskInputRow& row,
    LiquidityRiskReport* report) const {
  const std::string rule = "overnight_liquidity_cap";
  const double threshold = Threshold(rule, 0.475000);

  if (std::fabs(row.var_1d) > threshold) {
    AddIssue(
        LiquidityRiskIssueType::kLimitExceeded,
        LiquidityRiskSeverity::kError,
        row,
        rule,
        "OvernightLiquidityCap breached configured validation rule",
        "absolute metric below threshold",
        NumberText(row.var_1d),
        report);
    return false;
  }

  return true;
}


bool LiquidityRiskEngine::CheckCrossCurrencyFundingCap(
    const LiquidityRiskInputRow& row,
    LiquidityRiskReport* report) const {
  const std::string rule = "cross_currency_funding_cap";
  const double threshold = Threshold(rule, 0.550000);

  if (row.var_10d < threshold) {
    AddIssue(
        LiquidityRiskIssueType::kFloorBreached,
        LiquidityRiskSeverity::kWarning,
        row,
        rule,
        "CrossCurrencyFundingCap breached configured validation rule",
        "metric above configured floor",
        NumberText(row.var_10d),
        report);
    return false;
  }

  return true;
}


bool LiquidityRiskEngine::CheckRepoFundingCap(
    const LiquidityRiskInputRow& row,
    LiquidityRiskReport* report) const {
  const std::string rule = "repo_funding_cap";
  const double threshold = Threshold(rule, 0.625000);

  if (SafeRatio(row.haircut, row.quantity) > threshold) {
    AddIssue(
        LiquidityRiskIssueType::kRatioViolation,
        LiquidityRiskSeverity::kError,
        row,
        rule,
        "RepoFundingCap breached configured validation rule",
        "ratio below configured threshold",
        NumberText(SafeRatio(row.haircut, row.quantity)),
        report);
    return false;
  }

  return true;
}


bool LiquidityRiskEngine::CheckFXSwapFundingCap(
    const LiquidityRiskInputRow& row,
    LiquidityRiskReport* report) const {
  const std::string rule = "f_x_swap_funding_cap";
  const double threshold = Threshold(rule, 0.700000);

  if (row.restricted && !allow_manual_overrides_) {
    AddIssue(
        LiquidityRiskIssueType::kPolicyViolation,
        LiquidityRiskSeverity::kCritical,
        row,
        rule,
        "FXSwapFundingCap breached configured validation rule",
        "unrestricted row or approved override",
        "restricted row without override",
        report);
    return false;
  }

  return true;
}


bool LiquidityRiskEngine::CheckConcentrationLiquidityCap(
    const LiquidityRiskInputRow& row,
    LiquidityRiskReport* report) const {
  const std::string rule = "concentration_liquidity_cap";
  const double threshold = Threshold(rule, 0.775000);

  if (strict_mode_ && std::fabs(row.fee_rate - row.volatility) > threshold) {
    AddIssue(
        LiquidityRiskIssueType::kScenarioFailure,
        LiquidityRiskSeverity::kWarning,
        row,
        rule,
        "ConcentrationLiquidityCap breached configured validation rule",
        "strict-mode metric difference inside threshold",
        NumberText(row.fee_rate - row.volatility),
        report);
    return false;
  }

  return true;
}


bool LiquidityRiskEngine::CheckETFCreateRedeemCap(
    const LiquidityRiskInputRow& row,
    LiquidityRiskReport* report) const {
  const std::string rule = "e_t_f_create_redeem_cap";
  const double threshold = Threshold(rule, 0.850000);

  if (std::fabs(row.slippage_bps) > threshold) {
    AddIssue(
        LiquidityRiskIssueType::kLimitExceeded,
        LiquidityRiskSeverity::kError,
        row,
        rule,
        "ETFCreateRedeemCap breached configured validation rule",
        "absolute metric below threshold",
        NumberText(row.slippage_bps),
        report);
    return false;
  }

  return true;
}


bool LiquidityRiskEngine::CheckUnderlyingBasketLiquidityCap(
    const LiquidityRiskInputRow& row,
    LiquidityRiskReport* report) const {
  const std::string rule = "underlying_basket_liquidity_cap";
  const double threshold = Threshold(rule, 0.925000);

  if (row.turnover_rate < threshold) {
    AddIssue(
        LiquidityRiskIssueType::kFloorBreached,
        LiquidityRiskSeverity::kWarning,
        row,
        rule,
        "UnderlyingBasketLiquidityCap breached configured validation rule",
        "metric above configured floor",
        NumberText(row.turnover_rate),
        report);
    return false;
  }

  return true;
}


bool LiquidityRiskEngine::CheckIndexRebalanceLiquidityCap(
    const LiquidityRiskInputRow& row,
    LiquidityRiskReport* report) const {
  const std::string rule = "index_rebalance_liquidity_cap";
  const double threshold = Threshold(rule, 1.000000);

  if (SafeRatio(row.notional, row.margin_rate) > threshold) {
    AddIssue(
        LiquidityRiskIssueType::kRatioViolation,
        LiquidityRiskSeverity::kError,
        row,
        rule,
        "IndexRebalanceLiquidityCap breached configured validation rule",
        "ratio below configured threshold",
        NumberText(SafeRatio(row.notional, row.margin_rate)),
        report);
    return false;
  }

  return true;
}


bool LiquidityRiskEngine::CheckEventLiquidityCap(
    const LiquidityRiskInputRow& row,
    LiquidityRiskReport* report) const {
  const std::string rule = "event_liquidity_cap";
  const double threshold = Threshold(rule, 0.250000);

  if (row.restricted && !allow_manual_overrides_) {
    AddIssue(
        LiquidityRiskIssueType::kPolicyViolation,
        LiquidityRiskSeverity::kCritical,
        row,
        rule,
        "EventLiquidityCap breached configured validation rule",
        "unrestricted row or approved override",
        "restricted row without override",
        report);
    return false;
  }

  return true;
}


bool LiquidityRiskEngine::CheckNewsShockLiquidityCap(
    const LiquidityRiskInputRow& row,
    LiquidityRiskReport* report) const {
  const std::string rule = "news_shock_liquidity_cap";
  const double threshold = Threshold(rule, 0.325000);

  if (strict_mode_ && std::fabs(row.quantity - row.unrealized_pnl) > threshold) {
    AddIssue(
        LiquidityRiskIssueType::kScenarioFailure,
        LiquidityRiskSeverity::kWarning,
        row,
        rule,
        "NewsShockLiquidityCap breached configured validation rule",
        "strict-mode metric difference inside threshold",
        NumberText(row.quantity - row.unrealized_pnl),
        report);
    return false;
  }

  return true;
}


bool LiquidityRiskEngine::CheckLimitOrderBookThinnessCap(
    const LiquidityRiskInputRow& row,
    LiquidityRiskReport* report) const {
  const std::string rule = "limit_order_book_thinness_cap";
  const double threshold = Threshold(rule, 0.400000);

  if (std::fabs(row.price) > threshold) {
    AddIssue(
        LiquidityRiskIssueType::kLimitExceeded,
        LiquidityRiskSeverity::kError,
        row,
        rule,
        "LimitOrderBookThinnessCap breached configured validation rule",
        "absolute metric below threshold",
        NumberText(row.price),
        report);
    return false;
  }

  return true;
}


bool LiquidityRiskEngine::CheckQueuePositionDecayCap(
    const LiquidityRiskInputRow& row,
    LiquidityRiskReport* report) const {
  const std::string rule = "queue_position_decay_cap";
  const double threshold = Threshold(rule, 0.475000);

  if (row.volatility < threshold) {
    AddIssue(
        LiquidityRiskIssueType::kFloorBreached,
        LiquidityRiskSeverity::kWarning,
        row,
        rule,
        "QueuePositionDecayCap breached configured validation rule",
        "metric above configured floor",
        NumberText(row.volatility),
        report);
    return false;
  }

  return true;
}


bool LiquidityRiskEngine::CheckVenueOutageLiquidityCap(
    const LiquidityRiskInputRow& row,
    LiquidityRiskReport* report) const {
  const std::string rule = "venue_outage_liquidity_cap";
  const double threshold = Threshold(rule, 0.550000);

  if (SafeRatio(row.average_daily_volume, row.net_exposure) > threshold) {
    AddIssue(
        LiquidityRiskIssueType::kRatioViolation,
        LiquidityRiskSeverity::kError,
        row,
        rule,
        "VenueOutageLiquidityCap breached configured validation rule",
        "ratio below configured threshold",
        NumberText(SafeRatio(row.average_daily_volume, row.net_exposure)),
        report);
    return false;
  }

  return true;
}


bool LiquidityRiskEngine::CheckQuoteFadeCap(
    const LiquidityRiskInputRow& row,
    LiquidityRiskReport* report) const {
  const std::string rule = "quote_fade_cap";
  const double threshold = Threshold(rule, 0.625000);

  if (row.restricted && !allow_manual_overrides_) {
    AddIssue(
        LiquidityRiskIssueType::kPolicyViolation,
        LiquidityRiskSeverity::kCritical,
        row,
        rule,
        "QuoteFadeCap breached configured validation rule",
        "unrestricted row or approved override",
        "restricted row without override",
        report);
    return false;
  }

  return true;
}


bool LiquidityRiskEngine::CheckTradeSizeBucketCap(
    const LiquidityRiskInputRow& row,
    LiquidityRiskReport* report) const {
  const std::string rule = "trade_size_bucket_cap";
  const double threshold = Threshold(rule, 0.700000);

  if (strict_mode_ && std::fabs(row.margin_rate - row.confidence) > threshold) {
    AddIssue(
        LiquidityRiskIssueType::kScenarioFailure,
        LiquidityRiskSeverity::kWarning,
        row,
        rule,
        "TradeSizeBucketCap breached configured validation rule",
        "strict-mode metric difference inside threshold",
        NumberText(row.margin_rate - row.confidence),
        report);
    return false;
  }

  return true;
}


std::string LiquidityRiskSeverityName(LiquidityRiskSeverity severity) {
  switch (severity) {
    case LiquidityRiskSeverity::kInfo:
      return "info";
    case LiquidityRiskSeverity::kWarning:
      return "warning";
    case LiquidityRiskSeverity::kError:
      return "error";
    case LiquidityRiskSeverity::kCritical:
      return "critical";
  }
  return "unknown";
}

std::string LiquidityRiskIssueTypeName(LiquidityRiskIssueType type) {
  switch (type) {
    case LiquidityRiskIssueType::kInvalidInput:
      return "invalid_input";
    case LiquidityRiskIssueType::kMissingIdentifier:
      return "missing_identifier";
    case LiquidityRiskIssueType::kNonFiniteMetric:
      return "non_finite_metric";
    case LiquidityRiskIssueType::kThresholdBreach:
      return "threshold_breach";
    case LiquidityRiskIssueType::kLimitExceeded:
      return "limit_exceeded";
    case LiquidityRiskIssueType::kFloorBreached:
      return "floor_breached";
    case LiquidityRiskIssueType::kRatioViolation:
      return "ratio_violation";
    case LiquidityRiskIssueType::kPolicyViolation:
      return "policy_violation";
    case LiquidityRiskIssueType::kScenarioFailure:
      return "scenario_failure";
    case LiquidityRiskIssueType::kAggregationFailure:
      return "aggregation_failure";
  }
  return "unknown";
}

}  // namespace marketdata
