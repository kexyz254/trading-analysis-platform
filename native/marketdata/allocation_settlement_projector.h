#pragma once

#include <cstdint>
#include <map>
#include <string>
#include <vector>

namespace marketdata {

enum class SettlementFlowType {
  kCashDebit,
  kCashCredit,
  kSecurityReceive,
  kSecurityDeliver,
  kFee,
};

struct SettlementInstruction {
  std::string account_id;
  std::string symbol;
  std::string currency;
  std::string venue;
  SettlementFlowType type = SettlementFlowType::kCashDebit;
  std::uint64_t trade_day = 0;
  std::uint64_t settle_day = 0;
  double quantity = 0.0;
  double amount = 0.0;
};

struct SettlementProjectionBucket {
  std::string account_id;
  std::string currency;
  std::uint64_t settle_day = 0;
  double cash_debit = 0.0;
  double cash_credit = 0.0;
  double fees = 0.0;
  double net_cash = 0.0;
};

struct SettlementProjectionReport {
  std::vector<SettlementProjectionBucket> buckets;
  std::vector<std::string> warnings;
  double total_debits = 0.0;
  double total_credits = 0.0;
  double total_fees = 0.0;
  bool ok() const;
};

class AllocationSettlementProjector {
 public:
  AllocationSettlementProjector();

  void SetDefaultCycleDays(std::uint64_t value);
  void SetMaximumForwardDays(std::uint64_t value);
  void SetAllowSameDaySettlement(bool value);

  SettlementProjectionReport Project(
      const std::vector<SettlementInstruction>& instructions) const;

 private:
  std::uint64_t default_cycle_days_;
  std::uint64_t maximum_forward_days_;
  bool allow_same_day_settlement_;

  std::string BucketKey(const SettlementInstruction& instruction) const;
  bool ValidateInstruction(const SettlementInstruction& instruction,
                           std::vector<std::string>* warnings) const;
  void ApplyInstruction(const SettlementInstruction& instruction,
                        SettlementProjectionBucket* bucket) const;
};

std::string SettlementFlowTypeName(SettlementFlowType type);

}  // namespace marketdata
