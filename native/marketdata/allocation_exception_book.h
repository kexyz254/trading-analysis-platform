#pragma once

#include <cstdint>
#include <map>
#include <string>
#include <vector>

namespace marketdata {

enum class AllocationExceptionState {
  kOpen,
  kAcknowledged,
  kSuppressed,
  kResolved,
};

enum class AllocationExceptionSeverity {
  kLow,
  kMedium,
  kHigh,
  kCritical,
};

struct AllocationException {
  std::uint64_t id = 0;
  std::string plan_id;
  std::string symbol;
  std::string component;
  std::string message;
  AllocationExceptionSeverity severity = AllocationExceptionSeverity::kLow;
  AllocationExceptionState state = AllocationExceptionState::kOpen;
  std::uint64_t first_seen_ns = 0;
  std::uint64_t last_seen_ns = 0;
  std::uint64_t occurrences = 0;
};

struct ExceptionBookSummary {
  std::uint64_t open_count = 0;
  std::uint64_t critical_count = 0;
  std::uint64_t suppressed_count = 0;
  std::map<std::string, std::uint64_t> count_by_component;
};

class AllocationExceptionBook {
 public:
  AllocationExceptionBook();

  AllocationException Record(const std::string& plan_id,
                             const std::string& symbol,
                             const std::string& component,
                             const std::string& message,
                             AllocationExceptionSeverity severity,
                             std::uint64_t now_ns);

  bool Acknowledge(std::uint64_t id, std::uint64_t now_ns);
  bool Resolve(std::uint64_t id, std::uint64_t now_ns);
  bool Suppress(std::uint64_t id, std::uint64_t now_ns);

  ExceptionBookSummary Summary() const;
  std::vector<AllocationException> OpenExceptions() const;

 private:
  std::uint64_t next_id_;
  std::map<std::uint64_t, AllocationException> exceptions_;
  std::map<std::string, std::uint64_t> fingerprint_to_id_;

  std::string Fingerprint(const std::string& plan_id,
                          const std::string& symbol,
                          const std::string& component,
                          const std::string& message) const;
};

std::string AllocationExceptionStateName(AllocationExceptionState state);
std::string AllocationExceptionSeverityName(AllocationExceptionSeverity severity);

}  // namespace marketdata
