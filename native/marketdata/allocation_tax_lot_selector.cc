#include "allocation_tax_lot_selector.h"

#include <algorithm>
#include <cmath>
#include <stdexcept>

namespace marketdata {
namespace {

bool PositiveFinite(double value) {
  return std::isfinite(value) && value > 0.0;
}

double LotGainPerShare(const AllocationTaxLot& lot) {
  return lot.market_price - lot.cost_basis;
}

}  // namespace

AllocationTaxLotSelector::AllocationTaxLotSelector()
    : method_(TaxLotMethod::kFifo),
      allow_restricted_lots_(false),
      long_term_day_threshold_(365) {}

void AllocationTaxLotSelector::SetMethod(TaxLotMethod method) {
  method_ = method;
}

void AllocationTaxLotSelector::SetAllowRestrictedLots(bool value) {
  allow_restricted_lots_ = value;
}

void AllocationTaxLotSelector::SetLongTermDayThreshold(std::uint64_t value) {
  if (value == 0 || value > 36500) {
    throw std::invalid_argument("long-term day threshold is outside supported range");
  }
  long_term_day_threshold_ = value;
}

TaxLotSelectionReport AllocationTaxLotSelector::SelectLots(
    const std::vector<AllocationTaxLot>& lots,
    const std::string& account_id,
    const std::string& symbol,
    double sell_quantity) const {
  TaxLotSelectionReport report;
  report.requested_quantity = sell_quantity;

  if (account_id.empty() || symbol.empty()) {
    report.warnings.push_back("tax lot selection requires account and symbol");
    return report;
  }

  if (!PositiveFinite(sell_quantity)) {
    report.warnings.push_back("tax lot selection requires positive sell quantity");
    return report;
  }

  std::vector<AllocationTaxLot> candidates =
      FilterLots(lots, account_id, symbol, &report.warnings);
  SortLots(&candidates);

  double remaining = sell_quantity;

  for (const AllocationTaxLot& lot : candidates) {
    if (remaining <= 0.0) {
      break;
    }

    const double take_quantity = std::min(remaining, lot.quantity);
    if (take_quantity <= 0.0) {
      continue;
    }

    SelectedTaxLot selected = TakeFromLot(lot, take_quantity);
    report.estimated_realized_gain += selected.realized_gain;
    report.selected_quantity += selected.selected_quantity;
    report.selected_lots.push_back(selected);

    remaining -= take_quantity;
  }

  report.complete = remaining <= 0.000001;
  if (!report.complete) {
    report.warnings.push_back("not enough eligible tax lots to satisfy sell quantity");
  }

  return report;
}

std::vector<AllocationTaxLot> AllocationTaxLotSelector::FilterLots(
    const std::vector<AllocationTaxLot>& lots,
    const std::string& account_id,
    const std::string& symbol,
    std::vector<std::string>* warnings) const {
  std::vector<AllocationTaxLot> out;

  for (const AllocationTaxLot& lot : lots) {
    if (lot.account_id != account_id || lot.symbol != symbol) {
      continue;
    }

    if (!PositiveFinite(lot.quantity) ||
        !std::isfinite(lot.cost_basis) ||
        !std::isfinite(lot.market_price)) {
      warnings->push_back("tax lot skipped due to invalid numeric fields");
      continue;
    }

    if (lot.restricted && !allow_restricted_lots_) {
      warnings->push_back("restricted tax lot skipped: " + lot.lot_id);
      continue;
    }

    out.push_back(lot);
  }

  return out;
}

void AllocationTaxLotSelector::SortLots(
    std::vector<AllocationTaxLot>* lots) const {
  switch (method_) {
    case TaxLotMethod::kFifo:
      std::sort(lots->begin(), lots->end(),
                [](const AllocationTaxLot& a, const AllocationTaxLot& b) {
                  return a.acquisition_day < b.acquisition_day;
                });
      break;

    case TaxLotMethod::kLifo:
      std::sort(lots->begin(), lots->end(),
                [](const AllocationTaxLot& a, const AllocationTaxLot& b) {
                  return a.acquisition_day > b.acquisition_day;
                });
      break;

    case TaxLotMethod::kHighestCost:
      std::sort(lots->begin(), lots->end(),
                [](const AllocationTaxLot& a, const AllocationTaxLot& b) {
                  return a.cost_basis > b.cost_basis;
                });
      break;

    case TaxLotMethod::kLowestGain:
      std::sort(lots->begin(), lots->end(),
                [](const AllocationTaxLot& a, const AllocationTaxLot& b) {
                  return LotGainPerShare(a) < LotGainPerShare(b);
                });
      break;
  }
}

SelectedTaxLot AllocationTaxLotSelector::TakeFromLot(
    const AllocationTaxLot& lot,
    double quantity) const {
  SelectedTaxLot selected;
  selected.account_id = lot.account_id;
  selected.symbol = lot.symbol;
  selected.lot_id = lot.lot_id;
  selected.selected_quantity = quantity;
  selected.cost_basis = lot.cost_basis;
  selected.realized_gain = quantity * (lot.market_price - lot.cost_basis);
  return selected;
}

std::string TaxLotMethodName(TaxLotMethod method) {
  switch (method) {
    case TaxLotMethod::kFifo:
      return "fifo";
    case TaxLotMethod::kLifo:
      return "lifo";
    case TaxLotMethod::kHighestCost:
      return "highest_cost";
    case TaxLotMethod::kLowestGain:
      return "lowest_gain";
  }

  return "unknown";
}

}  // namespace marketdata
