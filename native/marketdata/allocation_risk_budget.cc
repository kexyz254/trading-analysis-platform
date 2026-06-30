#include "allocation_risk_budget.h"

#include <algorithm>
#include <cmath>
#include <stdexcept>

namespace marketdata {
namespace {

bool FiniteNonNegative(double value) {
  return std::isfinite(value) && value >= 0.0;
}

double Abs(double value) {
  return value < 0.0 ? -value : value;
}

void AddBreak(std::vector<RiskBudgetBreak>* breaks,
              const std::string& scope,
              const std::string& key,
              const std::string& message,
              double observed,
              double limit,
              bool fatal) {
  RiskBudgetBreak item;
  item.scope = scope;
  item.key = key;
  item.message = message;
  item.observed = observed;
  item.limit = limit;
  item.fatal = fatal;
  breaks->push_back(item);
}

}  // namespace

bool RiskBudgetReport::ok() const {
  for (const RiskBudgetBreak& item : breaks) {
    if (item.fatal) {
      return false;
    }
  }
  return true;
}

AllocationRiskBudget::AllocationRiskBudget()
    : max_gross_weight_(1.25),
      max_single_name_weight_(0.10),
      max_restricted_weight_(0.0) {}

void AllocationRiskBudget::SetMaxGrossWeight(double value) {
  if (!FiniteNonNegative(value)) {
    throw std::invalid_argument("max gross weight must be non-negative");
  }
  max_gross_weight_ = value;
}

void AllocationRiskBudget::SetMaxSingleNameWeight(double value) {
  if (!FiniteNonNegative(value) || value > 1.0) {
    throw std::invalid_argument("single-name weight must be within [0, 1]");
  }
  max_single_name_weight_ = value;
}

void AllocationRiskBudget::SetMaxRestrictedWeight(double value) {
  if (!FiniteNonNegative(value) || value > 1.0) {
    throw std::invalid_argument("restricted weight must be within [0, 1]");
  }
  max_restricted_weight_ = value;
}

void AllocationRiskBudget::SetSectorLimit(const std::string& sector,
                                          const RiskBudgetLimit& limit) {
  if (sector.empty()) {
    throw std::invalid_argument("sector limit requires a sector");
  }
  sector_limits_[sector] = limit;
}

void AllocationRiskBudget::SetCountryLimit(const std::string& country,
                                           const RiskBudgetLimit& limit) {
  if (country.empty()) {
    throw std::invalid_argument("country limit requires a country");
  }
  country_limits_[country] = limit;
}

void AllocationRiskBudget::SetCurrencyLimit(const std::string& currency,
                                            const RiskBudgetLimit& limit) {
  if (currency.empty()) {
    throw std::invalid_argument("currency limit requires a currency");
  }
  currency_limits_[currency] = limit;
}

RiskBudgetReport AllocationRiskBudget::Evaluate(
    const std::vector<RiskBudgetPosition>& positions) const {
  RiskBudgetReport report;
  double restricted_weight = 0.0;

  for (const RiskBudgetPosition& position : positions) {
    if (position.symbol.empty()) {
      AddBreak(&report.breaks, "symbol", "", "missing symbol", 0.0, 0.0, true);
      continue;
    }

    if (!std::isfinite(position.target_weight) ||
        !std::isfinite(position.volatility) ||
        !std::isfinite(position.beta) ||
        !std::isfinite(position.liquidity_score)) {
      AddBreak(&report.breaks, "symbol", position.symbol,
               "non-finite risk metric", 0.0, 0.0, true);
      continue;
    }

    const double abs_weight = Abs(position.target_weight);
    report.gross_weight += abs_weight;
    report.net_weight += position.target_weight;
    report.max_symbol_weight = std::max(report.max_symbol_weight, abs_weight);

    report.sector_weights[position.sector] += abs_weight;
    report.country_weights[position.country] += abs_weight;
    report.currency_weights[position.currency] += abs_weight;

    const double contribution = PositionRiskContribution(position);
    report.symbol_risk_contribution[position.symbol] = contribution;
    report.portfolio_volatility_proxy += contribution;

    if (abs_weight > max_single_name_weight_) {
      AddBreak(&report.breaks, "symbol", position.symbol,
               "single-name allocation exceeds limit",
               abs_weight, max_single_name_weight_, true);
    }

    if (position.restricted) {
      restricted_weight += abs_weight;
    }

    if (position.liquidity_score < 0.0 || position.liquidity_score > 1.0) {
      AddBreak(&report.breaks, "liquidity", position.symbol,
               "liquidity score outside expected range",
               position.liquidity_score, 1.0, true);
    }
  }

  if (report.gross_weight > max_gross_weight_) {
    AddBreak(&report.breaks, "portfolio", "gross",
             "gross target weight exceeds budget",
             report.gross_weight, max_gross_weight_, true);
  }

  if (restricted_weight > max_restricted_weight_) {
    AddBreak(&report.breaks, "portfolio", "restricted",
             "restricted symbols exceed allowed allocation",
             restricted_weight, max_restricted_weight_, true);
  }

  AddGroupBreaks("sector", report.sector_weights, sector_limits_, &report);
  AddGroupBreaks("country", report.country_weights, country_limits_, &report);
  AddGroupBreaks("currency", report.currency_weights, currency_limits_, &report);

  return report;
}

void AllocationRiskBudget::AddGroupBreaks(
    const std::string& scope,
    const std::map<std::string, double>& weights,
    const std::map<std::string, RiskBudgetLimit>& limits,
    RiskBudgetReport* report) const {
  for (const auto& item : weights) {
    const auto limit_it = limits.find(item.first);
    if (limit_it == limits.end()) {
      continue;
    }

    const RiskBudgetLimit& limit = limit_it->second;
    if (item.second > limit.max_weight) {
      AddBreak(&report->breaks, scope, item.first,
               "group allocation exceeds configured limit",
               item.second, limit.max_weight, true);
    }
  }
}

double AllocationRiskBudget::PositionRiskContribution(
    const RiskBudgetPosition& position) const {
  const double weight = Abs(position.target_weight);
  const double volatility = std::max(0.0, position.volatility);
  const double beta_component = 0.50 + Abs(position.beta) * 0.50;
  const double illiquidity = 1.0 + std::max(0.0, 1.0 - position.liquidity_score);
  const double explicit_budget =
      position.risk_budget > 0.0 ? position.risk_budget : 1.0;

  return weight * volatility * beta_component * illiquidity * explicit_budget;
}

}  // namespace marketdata
