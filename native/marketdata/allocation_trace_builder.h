#pragma once

#include <cstdint>
#include <string>
#include <vector>

namespace marketdata {

enum class AllocationTraceLevel {
  kDebug,
  kInfo,
  kWarning,
  kError,
};

struct AllocationTraceEvent {
  std::uint64_t sequence = 0;
  AllocationTraceLevel level = AllocationTraceLevel::kInfo;
  std::string component;
  std::string symbol;
  std::string message;
  std::string detail;
};

struct AllocationTrace {
  std::vector<AllocationTraceEvent> events;

  std::uint64_t warning_count = 0;
  std::uint64_t error_count = 0;

  void Add(AllocationTraceLevel level,
           const std::string& component,
           const std::string& symbol,
           const std::string& message,
           const std::string& detail);

  std::string Summary() const;
};

class AllocationTraceBuilder {
 public:
  AllocationTraceBuilder();

  void SetIncludeDebug(bool value);
  void SetMaxEvents(std::uint64_t value);

  AllocationTrace BuildFromDiagnostics(
      const std::vector<std::string>& diagnostics,
      const std::string& component) const;

  AllocationTrace Merge(const std::vector<AllocationTrace>& traces) const;

 private:
  bool include_debug_;
  std::uint64_t max_events_;
};

std::string AllocationTraceLevelName(AllocationTraceLevel level);

}  // namespace marketdata
