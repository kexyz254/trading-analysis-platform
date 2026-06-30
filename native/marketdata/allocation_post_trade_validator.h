#pragma once

#include <cstdint>
#include <string>
#include <vector>

namespace marketdata {

struct PostTradeAllocationRow {
  std::string plan_id;
  std::string account_id;
  std::string symbol;
  std::string currency;
  double target_quantity = 0.0;
  double filled_quantity = 0.0;
  double target_weight = 0.0;
  double resulting_weight = 0.0;
  double average_fill_price = 0.0;
  double reference_price = 0.0;
  double fee_amount = 0.0;
  std::uint64_t trade_date = 0;
  std::uint64_t settle_date = 0;
};

struct PostTradeValidationIssue {
  std::string symbol;
  std::string account_id;
  std::string message;
  double expected = 0.0;
  double observed = 0.0;
  bool fatal = false;
};

struct PostTradeValidationReport {
  std::vector<PostTradeValidationIssue> issues;
  double gross_notional = 0.0;
  double total_fees = 0.0;
  double max_abs_weight_drift = 0.0;
  bool ok() const;
};

class AllocationPostTradeValidator {
 public:
  AllocationPostTradeValidator();

  void SetQuantityTolerance(double value);
  void SetWeightTolerance(double value);
  void SetMaxFeeBps(double value);
  void SetRequireSettlementAfterTradeDate(bool value);

  PostTradeValidationReport Validate(
      const std::vector<PostTradeAllocationRow>& rows) const;

 private:
  double quantity_tolerance_;
  double weight_tolerance_;
  double max_fee_bps_;
  bool require_settlement_after_trade_date_;

  void ValidateRow(const PostTradeAllocationRow& row,
                   PostTradeValidationReport* report) const;

  void AddIssue(PostTradeValidationReport* report,
                const PostTradeAllocationRow& row,
                const std::string& message,
                double expected,
                double observed,
                bool fatal) const;
};

}  // namespace marketdata
