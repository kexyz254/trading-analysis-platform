#include "allocation_cash_ladder.h"

#include <cmath>
#include <sstream>
#include <stdexcept>

namespace marketdata {
namespace {

bool Finite(double value) {
  return std::isfinite(value);
}

double NetAvailable(const CashBucket& bucket) {
  return bucket.available + bucket.expected - bucket.reserved -
         bucket.fees - bucket.locked;
}

}  // namespace

bool CashLadderReport::ok() const {
  for (const CashLadderIssue& issue : issues) {
    if (issue.severity == CashSeverity::kError) {
      return false;
    }
  }
  return true;
}

AllocationCashLadder::AllocationCashLadder()
    : minimum_cash_buffer_(0.0),
      maximum_settlement_day_(30),
      allow_negative_available_(false) {}

void AllocationCashLadder::SetMinimumCashBuffer(double value) {
  if (!Finite(value) || value < 0.0) {
    throw std::invalid_argument("minimum cash buffer must be non-negative");
  }
  minimum_cash_buffer_ = value;
}

void AllocationCashLadder::SetMaximumSettlementDay(std::uint64_t day) {
  if (day > 3650) {
    throw std::invalid_argument("maximum settlement day is too far in future");
  }
  maximum_settlement_day_ = day;
}

void AllocationCashLadder::SetAllowNegativeAvailable(bool value) {
  allow_negative_available_ = value;
}

CashLadderReport AllocationCashLadder::Build(
    const std::vector<CashLedgerEntry>& entries) const {
  CashLadderReport report;
  std::map<std::string, CashBucket> buckets;

  for (const CashLedgerEntry& entry : entries) {
    ApplyEntry(entry, &buckets, &report);
  }

  for (const auto& item : buckets) {
    CheckBucket(item.second, &report);
    report.buckets.push_back(item.second);
    report.total_available += item.second.available;
    report.total_reserved += item.second.reserved;
    report.total_expected += item.second.expected;
    report.total_fees += item.second.fees;
  }

  return report;
}

bool AllocationCashLadder::HasFundingForOrder(
    const CashLadderReport& report,
    const std::string& account_id,
    const std::string& currency,
    std::uint64_t settle_day,
    double notional) const {
  if (!Finite(notional) || notional < 0.0) {
    return false;
  }

  double usable = 0.0;
  for (const CashBucket& bucket : report.buckets) {
    if (bucket.account_id != account_id || bucket.currency != currency) {
      continue;
    }

    if (bucket.settle_day <= settle_day) {
      usable += NetAvailable(bucket);
    }
  }

  return usable >= notional + minimum_cash_buffer_;
}

std::string AllocationCashLadder::BucketKey(
    const std::string& account_id,
    const std::string& currency,
    std::uint64_t settle_day) const {
  std::ostringstream out;
  out << account_id << "|" << currency << "|" << settle_day;
  return out.str();
}

void AllocationCashLadder::ApplyEntry(
    const CashLedgerEntry& entry,
    std::map<std::string, CashBucket>* buckets,
    CashLadderReport* report) const {
  CashLadderIssue issue;
  issue.account_id = entry.account_id;
  issue.currency = entry.currency;

  if (entry.account_id.empty() || entry.currency.empty()) {
    issue.severity = CashSeverity::kError;
    issue.message = "cash ledger entry missing account or currency";
    report->issues.push_back(issue);
    return;
  }

  if (!Finite(entry.amount)) {
    issue.severity = CashSeverity::kError;
    issue.message = "cash ledger entry has non-finite amount";
    report->issues.push_back(issue);
    return;
  }

  if (entry.settle_day > maximum_settlement_day_) {
    issue.severity = CashSeverity::kWarning;
    issue.message = "cash ledger entry settles beyond configured horizon";
    issue.observed = static_cast<double>(entry.settle_day);
    issue.limit = static_cast<double>(maximum_settlement_day_);
    report->issues.push_back(issue);
  }

  CashBucket& bucket =
      (*buckets)[BucketKey(entry.account_id, entry.currency, entry.settle_day)];
  bucket.account_id = entry.account_id;
  bucket.currency = entry.currency;
  bucket.settle_day = entry.settle_day;

  if (entry.locked) {
    bucket.locked += entry.amount;
    return;
  }

  switch (entry.intent) {
    case CashIntent::kAvailable:
      bucket.available += entry.amount;
      break;
    case CashIntent::kReservedForBuy:
      bucket.reserved += entry.amount;
      break;
    case CashIntent::kExpectedFromSell:
      bucket.expected += entry.amount;
      break;
    case CashIntent::kSettlementHold:
      bucket.locked += entry.amount;
      break;
    case CashIntent::kFees:
      bucket.fees += entry.amount;
      break;
    case CashIntent::kManualBuffer:
      bucket.reserved += entry.amount;
      break;
    case CashIntent::kUnknown:
      issue.severity = CashSeverity::kWarning;
      issue.message = "cash ledger entry has unknown intent";
      report->issues.push_back(issue);
      break;
  }
}

void AllocationCashLadder::CheckBucket(
    const CashBucket& bucket,
    CashLadderReport* report) const {
  const double net = NetAvailable(bucket);

  if (!allow_negative_available_ && net < 0.0) {
    CashLadderIssue issue;
    issue.severity = CashSeverity::kError;
    issue.account_id = bucket.account_id;
    issue.currency = bucket.currency;
    issue.message = "cash bucket has negative net availability";
    issue.observed = net;
    issue.limit = 0.0;
    report->issues.push_back(issue);
  }

  if (net < minimum_cash_buffer_) {
    CashLadderIssue issue;
    issue.severity = CashSeverity::kWarning;
    issue.account_id = bucket.account_id;
    issue.currency = bucket.currency;
    issue.message = "cash bucket is below configured buffer";
    issue.observed = net;
    issue.limit = minimum_cash_buffer_;
    report->issues.push_back(issue);
  }
}

std::string CashIntentName(CashIntent intent) {
  switch (intent) {
    case CashIntent::kAvailable:
      return "available";
    case CashIntent::kReservedForBuy:
      return "reserved_for_buy";
    case CashIntent::kExpectedFromSell:
      return "expected_from_sell";
    case CashIntent::kSettlementHold:
      return "settlement_hold";
    case CashIntent::kFees:
      return "fees";
    case CashIntent::kManualBuffer:
      return "manual_buffer";
    case CashIntent::kUnknown:
      return "unknown";
  }
  return "unknown";
}

std::string CashSeverityName(CashSeverity severity) {
  switch (severity) {
    case CashSeverity::kInfo:
      return "info";
    case CashSeverity::kWarning:
      return "warning";
    case CashSeverity::kError:
      return "error";
  }
  return "unknown";
}

}  // namespace marketdata
