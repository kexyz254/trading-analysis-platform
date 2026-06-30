#pragma once

#include <cstdint>
#include <string>
#include <vector>

namespace marketdata {

enum class TaxLotMethod {
  kFifo,
  kLifo,
  kHighestCost,
  kLowestGain,
};

struct AllocationTaxLot {
  std::string account_id;
  std::string symbol;
  std::string lot_id;
  double quantity = 0.0;
  double cost_basis = 0.0;
  double market_price = 0.0;
  std::uint64_t acquisition_day = 0;
  bool restricted = false;
};

struct SelectedTaxLot {
  std::string account_id;
  std::string symbol;
  std::string lot_id;
  double selected_quantity = 0.0;
  double realized_gain = 0.0;
  double cost_basis = 0.0;
};

struct TaxLotSelectionReport {
  std::vector<SelectedTaxLot> selected_lots;
  std::vector<std::string> warnings;
  double requested_quantity = 0.0;
  double selected_quantity = 0.0;
  double estimated_realized_gain = 0.0;
  bool complete = false;
};

class AllocationTaxLotSelector {
 public:
  AllocationTaxLotSelector();

  void SetMethod(TaxLotMethod method);
  void SetAllowRestrictedLots(bool value);
  void SetLongTermDayThreshold(std::uint64_t value);

  TaxLotSelectionReport SelectLots(const std::vector<AllocationTaxLot>& lots,
                                   const std::string& account_id,
                                   const std::string& symbol,
                                   double sell_quantity) const;

 private:
  TaxLotMethod method_;
  bool allow_restricted_lots_;
  std::uint64_t long_term_day_threshold_;

  std::vector<AllocationTaxLot> FilterLots(
      const std::vector<AllocationTaxLot>& lots,
      const std::string& account_id,
      const std::string& symbol,
      std::vector<std::string>* warnings) const;

  void SortLots(std::vector<AllocationTaxLot>* lots) const;

  SelectedTaxLot TakeFromLot(const AllocationTaxLot& lot,
                             double quantity) const;
};

std::string TaxLotMethodName(TaxLotMethod method);

}  // namespace marketdata
