#include "allocation_diagnostics.h"

#include <algorithm>
#include <cmath>
#include <iomanip>
#include <limits>
#include <sstream>
#include <set>
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

std::string NumberString(double value) {
  std::ostringstream out;
  out << std::fixed << std::setprecision(6) << value;
  return out.str();
}

bool IsFinite(double value) {
  return std::isfinite(value);
}

bool IsError(AllocationIssueSeverity severity) {
  return severity == AllocationIssueSeverity::kError ||
         severity == AllocationIssueSeverity::kCritical;
}

double Abs(double value) {
  return value < 0.0 ? -value : value;
}

bool IsNearMultiple(double value, double lot_size, double tolerance) {
  if (lot_size <= 0.0 || !IsFinite(value) || !IsFinite(lot_size)) {
    return false;
  }

  const double lots = value / lot_size;
  const double rounded = std::round(lots);
  return Abs(lots - rounded) <= tolerance;
}

}  // namespace

bool AllocationTargetKey::operator<(const AllocationTargetKey& other) const {
  if (portfolio_id != other.portfolio_id) return portfolio_id < other.portfolio_id;
  if (account_id != other.account_id) return account_id < other.account_id;
  if (symbol != other.symbol) return symbol < other.symbol;
  if (strategy != other.strategy) return strategy < other.strategy;
  return venue < other.venue;
}

std::string AllocationTargetKey::ToString() const {
  std::string out = portfolio_id + ":" + account_id + ":" + symbol;
  if (!strategy.empty()) {
    out += ":" + strategy;
  }
  if (!venue.empty()) {
    out += ":" + venue;
  }
  return out;
}

AllocationTargetKey AllocationTargetRow::Key() const {
  return AllocationTargetKey{
      NormalizeAllocationText(portfolio_id),
      NormalizeAllocationText(account_id),
      NormalizeAllocationText(symbol),
      NormalizeAllocationText(strategy),
      NormalizeAllocationText(venue),
  };
}

double AllocationTargetRow::Drift() const {
  return target_weight - current_weight;
}

double AllocationTargetRow::TradeQuantity() const {
  return target_quantity - current_quantity;
}

double AllocationTargetRow::TradeNotional() const {
  return TradeQuantity() * price;
}

double AllocationTargetRow::TargetNotional() const {
  return target_quantity * price;
}

bool AllocationDiagnosticReport::Ok() const {
  for (const AllocationDiagnosticIssue& issue : issues) {
    if (IsError(issue.severity)) {
      return false;
    }
  }
  return true;
}

std::uint64_t AllocationDiagnosticReport::ErrorCount() const {
  std::uint64_t total = 0;
  for (const AllocationDiagnosticIssue& issue : issues) {
    if (IsError(issue.severity)) {
      ++total;
    }
  }
  return total;
}

std::uint64_t AllocationDiagnosticReport::WarningCount() const {
  std::uint64_t total = 0;
  for (const AllocationDiagnosticIssue& issue : issues) {
    if (issue.severity == AllocationIssueSeverity::kWarning) {
      ++total;
    }
  }
  return total;
}

AllocationDiagnostics::AllocationDiagnostics()
    : weight_tolerance_(0.0005),
      drift_tolerance_(0.025),
      max_risk_score_(0.85),
      min_liquidity_score_(0.20),
      cash_tolerance_(0.01),
      allow_short_sales_(false),
      allow_restricted_symbols_(false) {}

void AllocationDiagnostics::SetWeightTolerance(double value) {
  if (value < 0.0 || !IsFinite(value)) {
    throw std::invalid_argument("weight tolerance must be finite and non-negative");
  }
  weight_tolerance_ = value;
}

void AllocationDiagnostics::SetDriftTolerance(double value) {
  if (value < 0.0 || !IsFinite(value)) {
    throw std::invalid_argument("drift tolerance must be finite and non-negative");
  }
  drift_tolerance_ = value;
}

void AllocationDiagnostics::SetMaxRiskScore(double value) {
  if (value < 0.0 || value > 1.0 || !IsFinite(value)) {
    throw std::invalid_argument("max risk score must be within [0, 1]");
  }
  max_risk_score_ = value;
}

void AllocationDiagnostics::SetMinLiquidityScore(double value) {
  if (value < 0.0 || value > 1.0 || !IsFinite(value)) {
    throw std::invalid_argument("min liquidity score must be within [0, 1]");
  }
  min_liquidity_score_ = value;
}

void AllocationDiagnostics::SetCashTolerance(double value) {
  if (value < 0.0 || !IsFinite(value)) {
    throw std::invalid_argument("cash tolerance must be finite and non-negative");
  }
  cash_tolerance_ = value;
}

void AllocationDiagnostics::SetAllowShortSales(bool value) {
  allow_short_sales_ = value;
}

void AllocationDiagnostics::SetAllowRestrictedSymbols(bool value) {
  allow_restricted_symbols_ = value;
}

AllocationDiagnosticReport AllocationDiagnostics::Analyze(
    const std::vector<AllocationTargetRow>& rows) const {
  AllocationDiagnosticReport report;
  std::map<AllocationTargetKey, AllocationTargetRow> seen_targets;
  std::map<std::string, std::vector<AllocationTargetRow>> by_portfolio;

  for (const AllocationTargetRow& row : rows) {
    ++report.checked_rows;

    if (!ValidateRow(row, &report)) {
      continue;
    }

    const AllocationTargetKey key = row.Key();
    if (seen_targets.find(key) != seen_targets.end()) {
      ++report.duplicate_rows;
      AddIssue(
          AllocationIssueType::kDuplicateTarget,
          AllocationIssueSeverity::kError,
          key.ToString(),
          "duplicate allocation target row",
          "unique portfolio/account/symbol/strategy/venue key",
          key.ToString(),
          &report);
      continue;
    }

    seen_targets[key] = row;
    by_portfolio[key.portfolio_id].push_back(row);

    CheckTradeSizing(row, &report);
    CheckRiskAndLiquidity(row, &report);

    if (Abs(row.Drift()) > drift_tolerance_) {
      AddIssue(
          AllocationIssueType::kDriftExceeded,
          AllocationIssueSeverity::kWarning,
          key.ToString(),
          "allocation drift exceeds configured tolerance",
          NumberString(drift_tolerance_),
          NumberString(row.Drift()),
          &report);
    }
  }

  CheckPortfolioWeightTotals(by_portfolio, &report);

  for (const auto& item : by_portfolio) {
    PortfolioAllocationSummary summary;
    summary.portfolio_id = item.first;
    summary.target_count = static_cast<std::uint64_t>(item.second.size());

    for (const AllocationTargetRow& row : item.second) {
      summary.total_target_weight += row.target_weight;
      summary.total_current_weight += row.current_weight;
      summary.gross_trade_notional += Abs(row.TradeNotional());
      summary.net_trade_notional += row.TradeNotional();
      summary.max_abs_drift = std::max(summary.max_abs_drift, Abs(row.Drift()));
      summary.weighted_risk_score += row.target_weight * row.risk_score;
    }

    report.portfolios.push_back(summary);
  }

  return report;
}

bool AllocationDiagnostics::ValidateRow(
    const AllocationTargetRow& row,
    AllocationDiagnosticReport* report) const {
  const AllocationTargetKey key = row.Key();
  bool valid = true;

  if (key.portfolio_id.empty() ||
      key.account_id.empty() ||
      key.symbol.empty()) {
    ++report->invalid_rows;
    AddIssue(
        AllocationIssueType::kInvalidRow,
        AllocationIssueSeverity::kError,
        key.ToString(),
        "allocation row is missing required identifiers",
        "portfolio_id, account_id and symbol",
        key.ToString(),
        report);
    valid = false;
  }

  if (!IsFinite(row.target_weight) ||
      !IsFinite(row.current_weight) ||
      !IsFinite(row.target_quantity) ||
      !IsFinite(row.current_quantity) ||
      !IsFinite(row.price)) {
    ++report->invalid_rows;
    AddIssue(
        AllocationIssueType::kInvalidRow,
        AllocationIssueSeverity::kError,
        key.ToString(),
        "allocation row contains non-finite numeric values",
        "finite numeric fields",
        "nan or infinity",
        report);
    valid = false;
  }

  if (row.target_weight < -weight_tolerance_ ||
      row.target_weight > 1.0 + weight_tolerance_) {
    AddIssue(
        AllocationIssueType::kTargetWeightOutOfRange,
        AllocationIssueSeverity::kError,
        key.ToString(),
        "target weight is outside supported range",
        "0.0 <= target_weight <= 1.0",
        NumberString(row.target_weight),
        report);
    valid = false;
  }

  if (!allow_short_sales_ &&
      (row.target_quantity < 0.0 || row.current_quantity < 0.0)) {
    AddIssue(
        AllocationIssueType::kQuantityOutOfRange,
        AllocationIssueSeverity::kError,
        key.ToString(),
        "short quantities are disabled",
        "non-negative quantity",
        NumberString(row.target_quantity) + "/" + NumberString(row.current_quantity),
        report);
    valid = false;
  }

  if (row.price < 0.0) {
    AddIssue(
        AllocationIssueType::kInvalidRow,
        AllocationIssueSeverity::kError,
        key.ToString(),
        "price cannot be negative",
        "non-negative price",
        NumberString(row.price),
        report);
    valid = false;
  }

  if (row.lot_size <= 0.0 || !IsFinite(row.lot_size)) {
    AddIssue(
        AllocationIssueType::kInvalidRow,
        AllocationIssueSeverity::kError,
        key.ToString(),
        "lot size must be positive and finite",
        "positive finite lot size",
        NumberString(row.lot_size),
        report);
    valid = false;
  }

  return valid;
}

void AllocationDiagnostics::CheckPortfolioWeightTotals(
    const std::map<std::string, std::vector<AllocationTargetRow>>& by_portfolio,
    AllocationDiagnosticReport* report) const {
  for (const auto& item : by_portfolio) {
    double total_weight = 0.0;

    for (const AllocationTargetRow& row : item.second) {
      total_weight += row.target_weight;
    }

    if (Abs(total_weight - 1.0) > weight_tolerance_) {
      AddIssue(
          AllocationIssueType::kWeightSumMismatch,
          AllocationIssueSeverity::kError,
          item.first,
          "portfolio target weights do not sum to one",
          "1.000000",
          NumberString(total_weight),
          report);
    }
  }
}

void AllocationDiagnostics::CheckTradeSizing(
    const AllocationTargetRow& row,
    AllocationDiagnosticReport* report) const {
  const AllocationTargetKey key = row.Key();
  const double trade_quantity = row.TradeQuantity();
  const double abs_trade_quantity = Abs(trade_quantity);

  if (abs_trade_quantity <= 0.0) {
    return;
  }

  if (row.min_trade_quantity > 0.0 &&
      abs_trade_quantity < row.min_trade_quantity) {
    AddIssue(
        AllocationIssueType::kTradeTooSmall,
        AllocationIssueSeverity::kInfo,
        key.ToString(),
        "trade quantity is smaller than configured minimum",
        NumberString(row.min_trade_quantity),
        NumberString(abs_trade_quantity),
        report);
  }

  if (row.max_trade_quantity > 0.0 &&
      abs_trade_quantity > row.max_trade_quantity) {
    AddIssue(
        AllocationIssueType::kTradeTooLarge,
        AllocationIssueSeverity::kError,
        key.ToString(),
        "trade quantity exceeds configured maximum",
        NumberString(row.max_trade_quantity),
        NumberString(abs_trade_quantity),
        report);
  }

  if (row.max_position_quantity > 0.0 &&
      Abs(row.target_quantity) > row.max_position_quantity) {
    AddIssue(
        AllocationIssueType::kQuantityOutOfRange,
        AllocationIssueSeverity::kError,
        key.ToString(),
        "target quantity exceeds maximum allowed position quantity",
        NumberString(row.max_position_quantity),
        NumberString(row.target_quantity),
        report);
  }

  if (!IsNearMultiple(abs_trade_quantity, row.lot_size, 0.000001)) {
    AddIssue(
        AllocationIssueType::kLotSizeViolation,
        AllocationIssueSeverity::kError,
        key.ToString(),
        "trade quantity is not aligned to lot size",
        "multiple of " + NumberString(row.lot_size),
        NumberString(abs_trade_quantity),
        report);
  }

  if (row.TradeNotional() > row.available_cash + cash_tolerance_) {
    AddIssue(
        AllocationIssueType::kCashShortfall,
        AllocationIssueSeverity::kCritical,
        key.ToString(),
        "buy trade notional exceeds available cash",
        NumberString(row.available_cash),
        NumberString(row.TradeNotional()),
        report);
  }
}

void AllocationDiagnostics::CheckRiskAndLiquidity(
    const AllocationTargetRow& row,
    AllocationDiagnosticReport* report) const {
  const AllocationTargetKey key = row.Key();

  if (row.restricted && !allow_restricted_symbols_) {
    AddIssue(
        AllocationIssueType::kRestrictedSymbol,
        AllocationIssueSeverity::kCritical,
        key.ToString(),
        "restricted symbol appears in allocation plan",
        "unrestricted symbol",
        "restricted",
        report);
  }

  if (row.liquidity_score < min_liquidity_score_) {
    AddIssue(
        AllocationIssueType::kLiquidityLimitExceeded,
        AllocationIssueSeverity::kWarning,
        key.ToString(),
        "symbol liquidity score is below configured minimum",
        NumberString(min_liquidity_score_),
        NumberString(row.liquidity_score),
        report);
  }

  if (row.risk_score > max_risk_score_) {
    AddIssue(
        AllocationIssueType::kRiskLimitExceeded,
        AllocationIssueSeverity::kError,
        key.ToString(),
        "symbol risk score exceeds configured maximum",
        NumberString(max_risk_score_),
        NumberString(row.risk_score),
        report);
  }
}

void AllocationDiagnostics::AddIssue(
    AllocationIssueType type,
    AllocationIssueSeverity severity,
    const std::string& key,
    const std::string& message,
    const std::string& expected,
    const std::string& actual,
    AllocationDiagnosticReport* report) const {
  report->issues.push_back(AllocationDiagnosticIssue{
      type,
      severity,
      key,
      message,
      expected,
      actual,
  });
}

std::string NormalizeAllocationText(const std::string& value) {
  return UpperText(value);
}

std::string AllocationSeverityName(AllocationIssueSeverity severity) {
  switch (severity) {
    case AllocationIssueSeverity::kInfo:
      return "info";
    case AllocationIssueSeverity::kWarning:
      return "warning";
    case AllocationIssueSeverity::kError:
      return "error";
    case AllocationIssueSeverity::kCritical:
      return "critical";
  }

  return "unknown";
}

std::string AllocationIssueTypeName(AllocationIssueType type) {
  switch (type) {
    case AllocationIssueType::kInvalidRow:
      return "invalid_row";
    case AllocationIssueType::kDuplicateTarget:
      return "duplicate_target";
    case AllocationIssueType::kWeightSumMismatch:
      return "weight_sum_mismatch";
    case AllocationIssueType::kTargetWeightOutOfRange:
      return "target_weight_out_of_range";
    case AllocationIssueType::kQuantityOutOfRange:
      return "quantity_out_of_range";
    case AllocationIssueType::kLotSizeViolation:
      return "lot_size_violation";
    case AllocationIssueType::kRestrictedSymbol:
      return "restricted_symbol";
    case AllocationIssueType::kLiquidityLimitExceeded:
      return "liquidity_limit_exceeded";
    case AllocationIssueType::kRiskLimitExceeded:
      return "risk_limit_exceeded";
    case AllocationIssueType::kDriftExceeded:
      return "drift_exceeded";
    case AllocationIssueType::kTradeTooSmall:
      return "trade_too_small";
    case AllocationIssueType::kTradeTooLarge:
      return "trade_too_large";
    case AllocationIssueType::kCashShortfall:
      return "cash_shortfall";
  }

  return "unknown";
}

}  // namespace marketdata
