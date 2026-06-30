#include "allocation_post_trade_validator.h"

#include <algorithm>
#include <cmath>
#include <stdexcept>

namespace marketdata {
namespace {

double Abs(double value) {
  return value < 0.0 ? -value : value;
}

bool Finite(double value) {
  return std::isfinite(value);
}

}  // namespace

bool PostTradeValidationReport::ok() const {
  for (const PostTradeValidationIssue& issue : issues) {
    if (issue.fatal) {
      return false;
    }
  }
  return true;
}

AllocationPostTradeValidator::AllocationPostTradeValidator()
    : quantity_tolerance_(0.000001),
      weight_tolerance_(0.0025),
      max_fee_bps_(50.0),
      require_settlement_after_trade_date_(true) {}

void AllocationPostTradeValidator::SetQuantityTolerance(double value) {
  if (!Finite(value) || value < 0.0) {
    throw std::invalid_argument("quantity tolerance must be non-negative");
  }
  quantity_tolerance_ = value;
}

void AllocationPostTradeValidator::SetWeightTolerance(double value) {
  if (!Finite(value) || value < 0.0) {
    throw std::invalid_argument("weight tolerance must be non-negative");
  }
  weight_tolerance_ = value;
}

void AllocationPostTradeValidator::SetMaxFeeBps(double value) {
  if (!Finite(value) || value < 0.0) {
    throw std::invalid_argument("fee bps limit must be non-negative");
  }
  max_fee_bps_ = value;
}

void AllocationPostTradeValidator::SetRequireSettlementAfterTradeDate(bool value) {
  require_settlement_after_trade_date_ = value;
}

PostTradeValidationReport AllocationPostTradeValidator::Validate(
    const std::vector<PostTradeAllocationRow>& rows) const {
  PostTradeValidationReport report;

  for (const PostTradeAllocationRow& row : rows) {
    ValidateRow(row, &report);
  }

  return report;
}

void AllocationPostTradeValidator::ValidateRow(
    const PostTradeAllocationRow& row,
    PostTradeValidationReport* report) const {
  if (row.plan_id.empty() || row.account_id.empty() || row.symbol.empty()) {
    AddIssue(report, row, "post-trade row missing identity", 0.0, 0.0, true);
    return;
  }

  if (!Finite(row.target_quantity) ||
      !Finite(row.filled_quantity) ||
      !Finite(row.target_weight) ||
      !Finite(row.resulting_weight) ||
      !Finite(row.average_fill_price) ||
      !Finite(row.reference_price) ||
      !Finite(row.fee_amount)) {
    AddIssue(report, row, "post-trade row has non-finite metrics", 0.0, 0.0, true);
    return;
  }

  if (row.average_fill_price <= 0.0 || row.reference_price <= 0.0) {
    AddIssue(report, row, "post-trade row has invalid price", 0.0,
             row.average_fill_price, true);
    return;
  }

  const double quantity_gap = Abs(row.target_quantity - row.filled_quantity);
  if (quantity_gap > quantity_tolerance_) {
    AddIssue(report, row, "filled quantity does not match target quantity",
             row.target_quantity, row.filled_quantity, false);
  }

  const double weight_gap = Abs(row.target_weight - row.resulting_weight);
  report->max_abs_weight_drift =
      std::max(report->max_abs_weight_drift, weight_gap);

  if (weight_gap > weight_tolerance_) {
    AddIssue(report, row, "resulting allocation weight drift exceeds tolerance",
             row.target_weight, row.resulting_weight, false);
  }

  const double notional = Abs(row.filled_quantity * row.average_fill_price);
  report->gross_notional += notional;
  report->total_fees += std::max(0.0, row.fee_amount);

  if (notional > 0.0) {
    const double fee_bps = row.fee_amount / notional * 10000.0;
    if (fee_bps > max_fee_bps_) {
      AddIssue(report, row, "post-trade fee exceeds configured bps limit",
               max_fee_bps_, fee_bps, false);
    }
  }

  const double price_slippage_bps =
      Abs(row.average_fill_price - row.reference_price) /
      row.reference_price * 10000.0;

  if (price_slippage_bps > 250.0) {
    AddIssue(report, row, "post-trade price slippage is unusually high",
             250.0, price_slippage_bps, false);
  }

  if (require_settlement_after_trade_date_ &&
      row.trade_date != 0 &&
      row.settle_date != 0 &&
      row.settle_date < row.trade_date) {
    AddIssue(report, row, "settlement date is before trade date",
             static_cast<double>(row.trade_date),
             static_cast<double>(row.settle_date),
             true);
  }
}

void AllocationPostTradeValidator::AddIssue(
    PostTradeValidationReport* report,
    const PostTradeAllocationRow& row,
    const std::string& message,
    double expected,
    double observed,
    bool fatal) const {
  PostTradeValidationIssue issue;
  issue.symbol = row.symbol;
  issue.account_id = row.account_id;
  issue.message = message;
  issue.expected = expected;
  issue.observed = observed;
  issue.fatal = fatal;
  report->issues.push_back(issue);
}

}  // namespace marketdata
