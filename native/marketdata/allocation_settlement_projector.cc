#include "allocation_settlement_projector.h"

#include <cmath>
#include <sstream>
#include <stdexcept>

namespace marketdata {
namespace {

bool Finite(double value) {
  return std::isfinite(value);
}

}  // namespace

bool SettlementProjectionReport::ok() const {
  return warnings.empty();
}

AllocationSettlementProjector::AllocationSettlementProjector()
    : default_cycle_days_(2),
      maximum_forward_days_(30),
      allow_same_day_settlement_(true) {}

void AllocationSettlementProjector::SetDefaultCycleDays(std::uint64_t value) {
  if (value > 30) {
    throw std::invalid_argument("default settlement cycle too large");
  }
  default_cycle_days_ = value;
}

void AllocationSettlementProjector::SetMaximumForwardDays(std::uint64_t value) {
  if (value == 0 || value > 365) {
    throw std::invalid_argument("maximum forward days outside supported range");
  }
  maximum_forward_days_ = value;
}

void AllocationSettlementProjector::SetAllowSameDaySettlement(bool value) {
  allow_same_day_settlement_ = value;
}

SettlementProjectionReport AllocationSettlementProjector::Project(
    const std::vector<SettlementInstruction>& instructions) const {
  SettlementProjectionReport report;
  std::map<std::string, SettlementProjectionBucket> buckets;

  for (const SettlementInstruction& instruction : instructions) {
    if (!ValidateInstruction(instruction, &report.warnings)) {
      continue;
    }

    SettlementProjectionBucket& bucket = buckets[BucketKey(instruction)];
    bucket.account_id = instruction.account_id;
    bucket.currency = instruction.currency;
    bucket.settle_day = instruction.settle_day;

    ApplyInstruction(instruction, &bucket);
  }

  for (auto& item : buckets) {
    SettlementProjectionBucket& bucket = item.second;
    bucket.net_cash = bucket.cash_credit - bucket.cash_debit - bucket.fees;
    report.total_debits += bucket.cash_debit;
    report.total_credits += bucket.cash_credit;
    report.total_fees += bucket.fees;
    report.buckets.push_back(bucket);
  }

  return report;
}

std::string AllocationSettlementProjector::BucketKey(
    const SettlementInstruction& instruction) const {
  std::ostringstream out;
  out << instruction.account_id << "|"
      << instruction.currency << "|"
      << instruction.settle_day;
  return out.str();
}

bool AllocationSettlementProjector::ValidateInstruction(
    const SettlementInstruction& instruction,
    std::vector<std::string>* warnings) const {
  if (instruction.account_id.empty() || instruction.currency.empty()) {
    warnings->push_back("settlement instruction missing account or currency");
    return false;
  }

  if (!Finite(instruction.amount) || !Finite(instruction.quantity)) {
    warnings->push_back("settlement instruction has non-finite amount or quantity");
    return false;
  }

  if (!allow_same_day_settlement_ &&
      instruction.trade_day != 0 &&
      instruction.trade_day == instruction.settle_day) {
    warnings->push_back("same-day settlement is disabled");
    return false;
  }

  if (instruction.trade_day != 0 &&
      instruction.settle_day < instruction.trade_day) {
    warnings->push_back("settlement day is before trade day");
    return false;
  }

  if (instruction.trade_day != 0 &&
      instruction.settle_day > instruction.trade_day + maximum_forward_days_) {
    warnings->push_back("settlement day exceeds forward projection window");
    return false;
  }

  return true;
}

void AllocationSettlementProjector::ApplyInstruction(
    const SettlementInstruction& instruction,
    SettlementProjectionBucket* bucket) const {
  switch (instruction.type) {
    case SettlementFlowType::kCashDebit:
      bucket->cash_debit += instruction.amount;
      break;
    case SettlementFlowType::kCashCredit:
      bucket->cash_credit += instruction.amount;
      break;
    case SettlementFlowType::kFee:
      bucket->fees += instruction.amount;
      break;
    case SettlementFlowType::kSecurityReceive:
    case SettlementFlowType::kSecurityDeliver:
      break;
  }
}

std::string SettlementFlowTypeName(SettlementFlowType type) {
  switch (type) {
    case SettlementFlowType::kCashDebit:
      return "cash_debit";
    case SettlementFlowType::kCashCredit:
      return "cash_credit";
    case SettlementFlowType::kSecurityReceive:
      return "security_receive";
    case SettlementFlowType::kSecurityDeliver:
      return "security_deliver";
    case SettlementFlowType::kFee:
      return "fee";
  }

  return "unknown";
}

}  // namespace marketdata
