#include "allocation_trace_builder.h"

#include <sstream>
#include <stdexcept>

namespace marketdata {

void AllocationTrace::Add(AllocationTraceLevel level,
                          const std::string& component,
                          const std::string& symbol,
                          const std::string& message,
                          const std::string& detail) {
  AllocationTraceEvent event;
  event.sequence = static_cast<std::uint64_t>(events.size() + 1);
  event.level = level;
  event.component = component;
  event.symbol = symbol;
  event.message = message;
  event.detail = detail;

  if (level == AllocationTraceLevel::kWarning) {
    ++warning_count;
  } else if (level == AllocationTraceLevel::kError) {
    ++error_count;
  }

  events.push_back(event);
}

std::string AllocationTrace::Summary() const {
  std::ostringstream out;
  out << "events=" << events.size()
      << " warnings=" << warning_count
      << " errors=" << error_count;
  return out.str();
}

AllocationTraceBuilder::AllocationTraceBuilder()
    : include_debug_(false),
      max_events_(1000) {}

void AllocationTraceBuilder::SetIncludeDebug(bool value) {
  include_debug_ = value;
}

void AllocationTraceBuilder::SetMaxEvents(std::uint64_t value) {
  if (value == 0) {
    throw std::invalid_argument("max events must be positive");
  }
  max_events_ = value;
}

AllocationTrace AllocationTraceBuilder::BuildFromDiagnostics(
    const std::vector<std::string>& diagnostics,
    const std::string& component) const {
  AllocationTrace trace;

  for (const std::string& diagnostic : diagnostics) {
    if (trace.events.size() >= max_events_) {
      trace.Add(AllocationTraceLevel::kWarning, component, "",
                "trace truncated", "maximum event count reached");
      break;
    }

    AllocationTraceLevel level = AllocationTraceLevel::kInfo;
    if (diagnostic.find("fatal") != std::string::npos ||
        diagnostic.find("failed") != std::string::npos ||
        diagnostic.find("invalid") != std::string::npos) {
      level = AllocationTraceLevel::kError;
    } else if (diagnostic.find("warning") != std::string::npos ||
               diagnostic.find("outside") != std::string::npos ||
               diagnostic.find("exceed") != std::string::npos) {
      level = AllocationTraceLevel::kWarning;
    } else if (diagnostic.find("debug") != std::string::npos) {
      level = AllocationTraceLevel::kDebug;
    }

    if (level == AllocationTraceLevel::kDebug && !include_debug_) {
      continue;
    }

    trace.Add(level, component, "", diagnostic, "");
  }

  return trace;
}

AllocationTrace AllocationTraceBuilder::Merge(
    const std::vector<AllocationTrace>& traces) const {
  AllocationTrace merged;

  for (const AllocationTrace& trace : traces) {
    for (const AllocationTraceEvent& event : trace.events) {
      if (merged.events.size() >= max_events_) {
        merged.Add(AllocationTraceLevel::kWarning, "trace_builder", "",
                   "trace merge truncated", "maximum event count reached");
        return merged;
      }

      if (event.level == AllocationTraceLevel::kDebug && !include_debug_) {
        continue;
      }

      merged.Add(event.level, event.component, event.symbol,
                 event.message, event.detail);
    }
  }

  return merged;
}

std::string AllocationTraceLevelName(AllocationTraceLevel level) {
  switch (level) {
    case AllocationTraceLevel::kDebug:
      return "debug";
    case AllocationTraceLevel::kInfo:
      return "info";
    case AllocationTraceLevel::kWarning:
      return "warning";
    case AllocationTraceLevel::kError:
      return "error";
  }

  return "unknown";
}

}  // namespace marketdata
