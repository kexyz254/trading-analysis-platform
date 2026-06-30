#pragma once

#include <cstdint>
#include <map>
#include <string>
#include <vector>

namespace marketdata {

enum class CashIntent {
  kUnknown,
  kAvailable,
  kReservedForBuy,
  kExpectedFromSell,
  kSettlementHold,
  kFees,
  kManualBuffer,
};

enum class CashSeverity {
  kInfo,
  kWarning,
  kError,
};

struct CashLedgerEntry {
  std::string portfolio_id;
  std::string account_id;
  std::string currency;
  std::string source;
  CashIntent intent = CashIntent::kUnknown;
  std::uint64_t settle_day = 0;
  double amount = 0.0;
  bool locked = false;
};

struct CashBucket {
  std::string account_id;
  std::string currency;
  std::uint64_t settle_day = 0;
  double available = 0.0;
  double reserved = 0.0;
  double expected = 0.0;
  double fees = 0.0;
  double locked = 0.0;
};

struct CashLadderIssue {
  CashSeverity severity = CashSeverity::kWarning;
  std::string account_id;
  std::string currency;
  std::string message;
  double observed = 0.0;
  double limit = 0.0;
};

struct CashLadderReport {
  std::vector<CashBucket> buckets;
  std::vector<CashLadderIssue> issues;
  double total_available = 0.0;
  double total_reserved = 0.0;
  double total_expected = 0.0;
  double total_fees = 0.0;
  bool ok() const;
};

class AllocationCashLadder {
 public:
  AllocationCashLadder();

  void SetMinimumCashBuffer(double value);
  void SetMaximumSettlementDay(std::uint64_t day);
  void SetAllowNegativeAvailable(bool value);

  CashLadderReport Build(
      const std::vector<CashLedgerEntry>& entries) const;

  bool HasFundingForOrder(const CashLadderReport& report,
                          const std::string& account_id,
                          const std::string& currency,
                          std::uint64_t settle_day,
                          double notional) const;

 private:
  double minimum_cash_buffer_;
  std::uint64_t maximum_settlement_day_;
  bool allow_negative_available_;

  std::string BucketKey(const std::string& account_id,
                        const std::string& currency,
                        std::uint64_t settle_day) const;

  void ApplyEntry(const CashLedgerEntry& entry,
                  std::map<std::string, CashBucket>* buckets,
                  CashLadderReport* report) const;

  void CheckBucket(const CashBucket& bucket,
                   CashLadderReport* report) const;
};

std::string CashIntentName(CashIntent intent);
std::string CashSeverityName(CashSeverity severity);

}  // namespace marketdata
