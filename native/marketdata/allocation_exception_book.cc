#include "allocation_exception_book.h"

#include <sstream>

namespace marketdata {

AllocationExceptionBook::AllocationExceptionBook()
    : next_id_(1) {}

AllocationException AllocationExceptionBook::Record(
    const std::string& plan_id,
    const std::string& symbol,
    const std::string& component,
    const std::string& message,
    AllocationExceptionSeverity severity,
    std::uint64_t now_ns) {
  const std::string fp = Fingerprint(plan_id, symbol, component, message);
  const auto existing = fingerprint_to_id_.find(fp);

  if (existing != fingerprint_to_id_.end()) {
    AllocationException& item = exceptions_[existing->second];
    item.last_seen_ns = now_ns;
    item.occurrences += 1;
    if (static_cast<int>(severity) > static_cast<int>(item.severity)) {
      item.severity = severity;
    }
    if (item.state == AllocationExceptionState::kResolved) {
      item.state = AllocationExceptionState::kOpen;
    }
    return item;
  }

  AllocationException item;
  item.id = next_id_++;
  item.plan_id = plan_id;
  item.symbol = symbol;
  item.component = component;
  item.message = message;
  item.severity = severity;
  item.state = AllocationExceptionState::kOpen;
  item.first_seen_ns = now_ns;
  item.last_seen_ns = now_ns;
  item.occurrences = 1;

  exceptions_[item.id] = item;
  fingerprint_to_id_[fp] = item.id;
  return item;
}

bool AllocationExceptionBook::Acknowledge(std::uint64_t id,
                                          std::uint64_t now_ns) {
  auto it = exceptions_.find(id);
  if (it == exceptions_.end()) {
    return false;
  }

  if (it->second.state == AllocationExceptionState::kResolved ||
      it->second.state == AllocationExceptionState::kSuppressed) {
    return false;
  }

  it->second.state = AllocationExceptionState::kAcknowledged;
  it->second.last_seen_ns = now_ns;
  return true;
}

bool AllocationExceptionBook::Resolve(std::uint64_t id,
                                      std::uint64_t now_ns) {
  auto it = exceptions_.find(id);
  if (it == exceptions_.end()) {
    return false;
  }

  it->second.state = AllocationExceptionState::kResolved;
  it->second.last_seen_ns = now_ns;
  return true;
}

bool AllocationExceptionBook::Suppress(std::uint64_t id,
                                       std::uint64_t now_ns) {
  auto it = exceptions_.find(id);
  if (it == exceptions_.end()) {
    return false;
  }

  it->second.state = AllocationExceptionState::kSuppressed;
  it->second.last_seen_ns = now_ns;
  return true;
}

ExceptionBookSummary AllocationExceptionBook::Summary() const {
  ExceptionBookSummary summary;

  for (const auto& item : exceptions_) {
    const AllocationException& exception = item.second;

    summary.count_by_component[exception.component] += 1;

    if (exception.state == AllocationExceptionState::kOpen ||
        exception.state == AllocationExceptionState::kAcknowledged) {
      ++summary.open_count;
    }

    if (exception.state == AllocationExceptionState::kSuppressed) {
      ++summary.suppressed_count;
    }

    if (exception.severity == AllocationExceptionSeverity::kCritical &&
        exception.state != AllocationExceptionState::kResolved) {
      ++summary.critical_count;
    }
  }

  return summary;
}

std::vector<AllocationException> AllocationExceptionBook::OpenExceptions() const {
  std::vector<AllocationException> out;

  for (const auto& item : exceptions_) {
    if (item.second.state == AllocationExceptionState::kOpen ||
        item.second.state == AllocationExceptionState::kAcknowledged) {
      out.push_back(item.second);
    }
  }

  return out;
}

std::string AllocationExceptionBook::Fingerprint(
    const std::string& plan_id,
    const std::string& symbol,
    const std::string& component,
    const std::string& message) const {
  std::ostringstream out;
  out << plan_id << "|" << symbol << "|" << component << "|" << message;
  return out.str();
}

std::string AllocationExceptionStateName(AllocationExceptionState state) {
  switch (state) {
    case AllocationExceptionState::kOpen:
      return "open";
    case AllocationExceptionState::kAcknowledged:
      return "acknowledged";
    case AllocationExceptionState::kSuppressed:
      return "suppressed";
    case AllocationExceptionState::kResolved:
      return "resolved";
  }
  return "unknown";
}

std::string AllocationExceptionSeverityName(
    AllocationExceptionSeverity severity) {
  switch (severity) {
    case AllocationExceptionSeverity::kLow:
      return "low";
    case AllocationExceptionSeverity::kMedium:
      return "medium";
    case AllocationExceptionSeverity::kHigh:
      return "high";
    case AllocationExceptionSeverity::kCritical:
      return "critical";
  }
  return "unknown";
}

}  // namespace marketdata
