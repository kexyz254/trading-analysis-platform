#pragma once

#include <cstdint>
#include <map>
#include <string>
#include <vector>

namespace marketdata {

enum class PlanJournalEventType {
  kCreated,
  kParsed,
  kValidated,
  kConstraintApplied,
  kOrderPlanned,
  kRiskChecked,
  kSubmitted,
  kRejected,
};

struct PlanJournalEvent {
  std::uint64_t sequence = 0;
  PlanJournalEventType type = PlanJournalEventType::kCreated;
  std::string plan_id;
  std::string symbol;
  std::string component;
  std::string message;
  double before_value = 0.0;
  double after_value = 0.0;
};

struct PlanJournalReplay {
  std::map<std::string, double> latest_symbol_value;
  std::vector<std::string> errors;
  std::uint64_t applied_events = 0;
  bool ok() const;
};

class AllocationPlanJournal {
 public:
  AllocationPlanJournal();

  PlanJournalEvent MakeEvent(PlanJournalEventType type,
                             const std::string& plan_id,
                             const std::string& symbol,
                             const std::string& component,
                             const std::string& message,
                             double before_value,
                             double after_value);

  PlanJournalReplay Replay(
      const std::vector<PlanJournalEvent>& events) const;

 private:
  std::uint64_t next_sequence_;
};

std::string PlanJournalEventTypeName(PlanJournalEventType type);

}  // namespace marketdata
