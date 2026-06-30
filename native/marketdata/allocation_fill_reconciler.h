#pragma once

#include <cstdint>
#include <map>
#include <string>
#include <vector>

namespace marketdata {

enum class FillSide {
  kUnknown,
  kBuy,
  kSell,
};

struct PlannedAllocationOrder {
  std::string plan_id;
  std::string order_id;
  std::string symbol;
  std::string account_id;
  std::string venue;
  FillSide side = FillSide::kUnknown;
  double planned_quantity = 0.0;
  double planned_limit_price = 0.0;
};

struct AllocationExecutionFill {
  std::string order_id;
  std::string execution_id;
  std::string symbol;
  std::string account_id;
  std::string venue;
  FillSide side = FillSide::kUnknown;
  double fill_quantity = 0.0;
  double fill_price = 0.0;
  double fee_amount = 0.0;
  std::uint64_t execution_time_ns = 0;
};

struct FillReconciliationBreak {
  std::string order_id;
  std::string execution_id;
  std::string symbol;
  std::string message;
  double expected = 0.0;
  double observed = 0.0;
  bool fatal = false;
};

struct FillReconciliationReport {
  std::vector<FillReconciliationBreak> breaks;
  std::map<std::string, double> filled_quantity_by_order;
  std::map<std::string, double> average_price_by_order;

  double gross_notional = 0.0;
  double total_fees = 0.0;
  std::uint64_t matched_fills = 0;
  std::uint64_t unmatched_fills = 0;

  bool ok() const;
};

class AllocationFillReconciler {
 public:
  AllocationFillReconciler();

  void SetQuantityTolerance(double value);
  void SetPriceToleranceBps(double value);
  void SetAllowOverfill(bool value);

  FillReconciliationReport Reconcile(
      const std::vector<PlannedAllocationOrder>& planned,
      const std::vector<AllocationExecutionFill>& fills) const;

 private:
  double quantity_tolerance_;
  double price_tolerance_bps_;
  bool allow_overfill_;

  void ReconcileOrder(
      const PlannedAllocationOrder& order,
      const std::vector<AllocationExecutionFill>& fills,
      FillReconciliationReport* report) const;

  bool FillMatchesOrder(const PlannedAllocationOrder& order,
                        const AllocationExecutionFill& fill) const;

  void AddBreak(FillReconciliationReport* report,
                const std::string& order_id,
                const std::string& execution_id,
                const std::string& symbol,
                const std::string& message,
                double expected,
                double observed,
                bool fatal) const;
};

std::string FillSideName(FillSide side);

}  // namespace marketdata
