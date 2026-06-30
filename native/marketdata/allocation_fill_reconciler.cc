#include "allocation_fill_reconciler.h"

#include <algorithm>
#include <cmath>
#include <set>
#include <stdexcept>

namespace marketdata {
namespace {

double Abs(double value) {
  return value < 0.0 ? -value : value;
}

bool FinitePositive(double value) {
  return std::isfinite(value) && value > 0.0;
}

double SignedQuantity(FillSide side, double quantity) {
  if (side == FillSide::kSell) {
    return -quantity;
  }
  return quantity;
}

}  // namespace

bool FillReconciliationReport::ok() const {
  for (const FillReconciliationBreak& item : breaks) {
    if (item.fatal) {
      return false;
    }
  }
  return true;
}

AllocationFillReconciler::AllocationFillReconciler()
    : quantity_tolerance_(0.000001),
      price_tolerance_bps_(100.0),
      allow_overfill_(false) {}

void AllocationFillReconciler::SetQuantityTolerance(double value) {
  if (!std::isfinite(value) || value < 0.0) {
    throw std::invalid_argument("quantity tolerance must be non-negative");
  }
  quantity_tolerance_ = value;
}

void AllocationFillReconciler::SetPriceToleranceBps(double value) {
  if (!std::isfinite(value) || value < 0.0) {
    throw std::invalid_argument("price tolerance must be non-negative");
  }
  price_tolerance_bps_ = value;
}

void AllocationFillReconciler::SetAllowOverfill(bool value) {
  allow_overfill_ = value;
}

FillReconciliationReport AllocationFillReconciler::Reconcile(
    const std::vector<PlannedAllocationOrder>& planned,
    const std::vector<AllocationExecutionFill>& fills) const {
  FillReconciliationReport report;
  std::set<std::string> known_orders;
  std::set<std::string> seen_execution_ids;

  for (const PlannedAllocationOrder& order : planned) {
    if (order.order_id.empty()) {
      AddBreak(&report, "", "", order.symbol,
               "planned allocation order missing order id",
               0.0, 0.0, true);
      continue;
    }
    known_orders.insert(order.order_id);
    ReconcileOrder(order, fills, &report);
  }

  for (const AllocationExecutionFill& fill : fills) {
    if (!fill.execution_id.empty() &&
        !seen_execution_ids.insert(fill.execution_id).second) {
      AddBreak(&report, fill.order_id, fill.execution_id, fill.symbol,
               "duplicate execution id in fill stream",
               1.0, 2.0, true);
    }

    if (known_orders.find(fill.order_id) == known_orders.end()) {
      ++report.unmatched_fills;
      AddBreak(&report, fill.order_id, fill.execution_id, fill.symbol,
               "execution fill does not match any planned allocation order",
               0.0, fill.fill_quantity, true);
    }
  }

  return report;
}

void AllocationFillReconciler::ReconcileOrder(
    const PlannedAllocationOrder& order,
    const std::vector<AllocationExecutionFill>& fills,
    FillReconciliationReport* report) const {
  if (order.symbol.empty() || order.account_id.empty()) {
    AddBreak(report, order.order_id, "", order.symbol,
             "planned allocation order missing symbol or account",
             0.0, 0.0, true);
    return;
  }

  if (!FinitePositive(order.planned_quantity)) {
    AddBreak(report, order.order_id, "", order.symbol,
             "planned allocation order has invalid quantity",
             0.0, order.planned_quantity, true);
    return;
  }

  double total_quantity = 0.0;
  double total_notional = 0.0;

  for (const AllocationExecutionFill& fill : fills) {
    if (!FillMatchesOrder(order, fill)) {
      continue;
    }

    ++report->matched_fills;

    if (!FinitePositive(fill.fill_quantity) || !FinitePositive(fill.fill_price)) {
      AddBreak(report, order.order_id, fill.execution_id, order.symbol,
               "fill contains invalid quantity or price",
               0.0, fill.fill_quantity, true);
      continue;
    }

    total_quantity += fill.fill_quantity;
    total_notional += fill.fill_quantity * fill.fill_price;
    report->gross_notional += fill.fill_quantity * fill.fill_price;
    report->total_fees += std::max(0.0, fill.fee_amount);

    if (order.planned_limit_price > 0.0) {
      const double bps =
          Abs(fill.fill_price - order.planned_limit_price) /
          order.planned_limit_price * 10000.0;

      if (bps > price_tolerance_bps_) {
        AddBreak(report, order.order_id, fill.execution_id, order.symbol,
                 "fill price moved outside configured tolerance",
                 price_tolerance_bps_, bps, false);
      }
    }
  }

  report->filled_quantity_by_order[order.order_id] =
      SignedQuantity(order.side, total_quantity);

  if (total_quantity > 0.0) {
    report->average_price_by_order[order.order_id] =
        total_notional / total_quantity;
  }

  if (!allow_overfill_ &&
      total_quantity - order.planned_quantity > quantity_tolerance_) {
    AddBreak(report, order.order_id, "", order.symbol,
             "filled quantity exceeds planned allocation order",
             order.planned_quantity, total_quantity, true);
  }

  if (order.planned_quantity - total_quantity > quantity_tolerance_) {
    AddBreak(report, order.order_id, "", order.symbol,
             "allocation order is not fully filled",
             order.planned_quantity, total_quantity, false);
  }
}

bool AllocationFillReconciler::FillMatchesOrder(
    const PlannedAllocationOrder& order,
    const AllocationExecutionFill& fill) const {
  if (order.order_id != fill.order_id) {
    return false;
  }

  if (order.symbol != fill.symbol || order.account_id != fill.account_id) {
    return false;
  }

  if (!order.venue.empty() && !fill.venue.empty() && order.venue != fill.venue) {
    return false;
  }

  if (order.side != FillSide::kUnknown &&
      fill.side != FillSide::kUnknown &&
      order.side != fill.side) {
    return false;
  }

  return true;
}

void AllocationFillReconciler::AddBreak(
    FillReconciliationReport* report,
    const std::string& order_id,
    const std::string& execution_id,
    const std::string& symbol,
    const std::string& message,
    double expected,
    double observed,
    bool fatal) const {
  FillReconciliationBreak item;
  item.order_id = order_id;
  item.execution_id = execution_id;
  item.symbol = symbol;
  item.message = message;
  item.expected = expected;
  item.observed = observed;
  item.fatal = fatal;
  report->breaks.push_back(item);
}

std::string FillSideName(FillSide side) {
  switch (side) {
    case FillSide::kBuy:
      return "buy";
    case FillSide::kSell:
      return "sell";
    case FillSide::kUnknown:
      return "unknown";
  }
  return "unknown";
}

}  // namespace marketdata
