#pragma once

#include <cstddef>
#include <cstdint>
#include <map>
#include <string>
#include <vector>

namespace marketdata {

enum class ExecutionSide {
  kBuy,
  kSell,
  kShort,
  kCover,
  kUnknown,
};

enum class ExecutionOrderType {
  kMarket,
  kLimit,
  kStop,
  kStopLimit,
  kPegged,
  kUnknown,
};

enum class ExecutionTimeInForce {
  kDay,
  kGtc,
  kIoc,
  kFok,
  kAtOpen,
  kAtClose,
  kUnknown,
};

enum class ExecutionEventType {
  kNew,
  kAck,
  kRoute,
  kReplace,
  kPartialFill,
  kFill,
  kCancel,
  kReject,
  kExpire,
  kUnknown,
};

enum class ExecutionLiquidityFlag {
  kAdd,
  kRemove,
  kAuction,
  kUnknown,
};

struct ExecutionLifecycleError {
  std::size_t line = 0;
  std::string field;
  std::string message;
};

struct ExecutionLifecycleEvent {
  std::size_t line = 0;

  std::string order_id;
  std::string parent_order_id;
  std::string client_order_id;
  std::string symbol;
  std::string venue;
  std::string route;
  std::string event_time;
  std::string reject_code;
  std::string notes;

  ExecutionSide side = ExecutionSide::kUnknown;
  ExecutionOrderType order_type = ExecutionOrderType::kUnknown;
  ExecutionTimeInForce time_in_force = ExecutionTimeInForce::kUnknown;
  ExecutionEventType event_type = ExecutionEventType::kUnknown;
  ExecutionLiquidityFlag liquidity_flag = ExecutionLiquidityFlag::kUnknown;

  std::uint64_t quantity = 0;
  std::uint64_t filled_quantity = 0;
  std::uint64_t remaining_quantity = 0;

  double price = 0.0;
  double average_price = 0.0;
  double commission = 0.0;

  bool has_price = false;
  bool has_average_price = false;
  bool has_commission = false;
};

struct ExecutionLifecycleDocument {
  std::vector<ExecutionLifecycleEvent> events;
  std::vector<ExecutionLifecycleError> errors;

  bool ok() const;
  std::size_t FilledEventCount() const;
  bool HasTerminalEvent(const std::string& order_id) const;
};

struct ExecutionLifecycleParserOptions {
  bool require_header = true;
  bool enforce_state_transitions = true;
  std::size_t max_rows = 100000;
  std::size_t max_field_bytes = 4096;
};

class ExecutionLifecycleCsvParser {
 public:
  ExecutionLifecycleCsvParser();
  explicit ExecutionLifecycleCsvParser(
      const ExecutionLifecycleParserOptions& options);

  ExecutionLifecycleDocument Parse(const std::string& csv) const;

  std::vector<std::string> SplitCsvRecord(
      const std::string& line,
      bool* ok) const;

  std::string NormalizeToken(const std::string& value) const;
  std::string EventTypeName(ExecutionEventType type) const;

 private:
  struct ColumnMap {
    std::map<std::string, std::size_t> index;

    bool Has(const std::string& name) const;
    std::size_t At(const std::string& name) const;
  };

  enum class OrderState {
    kNone,
    kPending,
    kLive,
    kPartiallyFilled,
    kFilled,
    kCancelled,
    kRejected,
    kExpired,
  };

  ExecutionLifecycleParserOptions options_;

  ColumnMap BuildColumnMap(
      const std::vector<std::string>& header,
      ExecutionLifecycleDocument* document) const;

  bool ParseEventRow(const std::vector<std::string>& fields,
                     const ColumnMap& columns,
                     std::size_t line_number,
                     ExecutionLifecycleEvent* event,
                     ExecutionLifecycleDocument* document) const;

  bool ValidateEvent(const ExecutionLifecycleEvent& event,
                     ExecutionLifecycleDocument* document) const;

  bool ValidateTransition(OrderState previous,
                          ExecutionEventType next) const;

  OrderState AdvanceState(OrderState previous,
                          ExecutionEventType next) const;

  void AddError(ExecutionLifecycleDocument* document,
                std::size_t line,
                const std::string& field,
                const std::string& message) const;

  std::string Field(const std::vector<std::string>& fields,
                    const ColumnMap& columns,
                    const std::string& name) const;

  bool ParseUnsigned(const std::string& value,
                     std::uint64_t* out) const;

  bool ParseDecimal(const std::string& value,
                    double* out) const;

  bool LooksLikeTimestamp(const std::string& value) const;

  ExecutionSide ParseSide(const std::string& value) const;
  ExecutionOrderType ParseOrderType(const std::string& value) const;
  ExecutionTimeInForce ParseTimeInForce(const std::string& value) const;
  ExecutionEventType ParseEventType(const std::string& value) const;
  ExecutionLiquidityFlag ParseLiquidityFlag(const std::string& value) const;
};

bool ParseExecutionLifecycleCsv(const std::string& csv,
                                ExecutionLifecycleDocument* document);

}  // namespace marketdata
