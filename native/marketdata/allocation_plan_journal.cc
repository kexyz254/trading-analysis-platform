#include "allocation_plan_journal.h"

#include <cmath>
#include <set>

namespace marketdata {

bool PlanJournalReplay::ok() const {
  return errors.empty();
}

AllocationPlanJournal::AllocationPlanJournal()
    : next_sequence_(1) {}

PlanJournalEvent AllocationPlanJournal::MakeEvent(
    PlanJournalEventType type,
    const std::string& plan_id,
    const std::string& symbol,
    const std::string& component,
    const std::string& message,
    double before_value,
    double after_value) {
  PlanJournalEvent event;
  event.sequence = next_sequence_++;
  event.type = type;
  event.plan_id = plan_id;
  event.symbol = symbol;
  event.component = component;
  event.message = message;
  event.before_value = before_value;
  event.after_value = after_value;
  return event;
}

PlanJournalReplay AllocationPlanJournal::Replay(
    const std::vector<PlanJournalEvent>& events) const {
  PlanJournalReplay replay;
  std::set<std::uint64_t> sequences;
  std::uint64_t previous = 0;

  for (const PlanJournalEvent& event : events) {
    if (event.sequence == 0) {
      replay.errors.push_back("journal event has zero sequence");
      continue;
    }

    if (!sequences.insert(event.sequence).second) {
      replay.errors.push_back("duplicate journal sequence");
      continue;
    }

    if (previous != 0 && event.sequence <= previous) {
      replay.errors.push_back("journal sequence is not increasing");
      continue;
    }

    if (event.plan_id.empty() || event.component.empty()) {
      replay.errors.push_back("journal event missing plan or component");
      continue;
    }

    if (!std::isfinite(event.before_value) ||
        !std::isfinite(event.after_value)) {
      replay.errors.push_back("journal event contains non-finite values");
      continue;
    }

    switch (event.type) {
      case PlanJournalEventType::kCreated:
      case PlanJournalEventType::kParsed:
      case PlanJournalEventType::kValidated:
      case PlanJournalEventType::kConstraintApplied:
      case PlanJournalEventType::kOrderPlanned:
      case PlanJournalEventType::kRiskChecked:
      case PlanJournalEventType::kSubmitted:
        if (!event.symbol.empty()) {
          replay.latest_symbol_value[event.symbol] = event.after_value;
        }
        break;
      case PlanJournalEventType::kRejected:
        replay.errors.push_back("plan journal replay encountered rejection");
        break;
    }

    previous = event.sequence;
    ++replay.applied_events;
  }

  return replay;
}

std::string PlanJournalEventTypeName(PlanJournalEventType type) {
  switch (type) {
    case PlanJournalEventType::kCreated:
      return "created";
    case PlanJournalEventType::kParsed:
      return "parsed";
    case PlanJournalEventType::kValidated:
      return "validated";
    case PlanJournalEventType::kConstraintApplied:
      return "constraint_applied";
    case PlanJournalEventType::kOrderPlanned:
      return "order_planned";
    case PlanJournalEventType::kRiskChecked:
      return "risk_checked";
    case PlanJournalEventType::kSubmitted:
      return "submitted";
    case PlanJournalEventType::kRejected:
      return "rejected";
  }
  return "unknown";
}

}  // namespace marketdata
