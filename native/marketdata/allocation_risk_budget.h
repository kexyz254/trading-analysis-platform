#pragma once

#include <map>
#include <string>
#include <vector>

namespace marketdata {

struct RiskBudgetPosition {
  std::string portfolio_id;
  std::string symbol;
  std::string sector;
  std::string country;
  std::string currency;

  double target_weight = 0.0;
  double current_weight = 0.0;
  double volatility = 0.0;
  double beta = 0.0;
  double liquidity_score = 1.0;
  double risk_budget = 0.0;
  bool restricted = false;
};

struct RiskBudgetLimit {
  std::string group_name;
  double max_weight = 1.0;
  double max_risk_contribution = 1.0;
  double min_liquidity_score = 0.0;
};

struct RiskBudgetBreak {
  std::string scope;
  std::string key;
  std::string message;
  double observed = 0.0;
  double limit = 0.0;
  bool fatal = false;
};

struct RiskBudgetReport {
  std::vector<RiskBudgetBreak> breaks;
  std::map<std::string, double> sector_weights;
  std::map<std::string, double> country_weights;
  std::map<std::string, double> currency_weights;
  std::map<std::string, double> symbol_risk_contribution;

  double gross_weight = 0.0;
  double net_weight = 0.0;
  double portfolio_volatility_proxy = 0.0;
  double max_symbol_weight = 0.0;

  bool ok() const;
};

class AllocationRiskBudget {
 public:
  AllocationRiskBudget();

  void SetMaxGrossWeight(double value);
  void SetMaxSingleNameWeight(double value);
  void SetMaxRestrictedWeight(double value);
  void SetSectorLimit(const std::string& sector, const RiskBudgetLimit& limit);
  void SetCountryLimit(const std::string& country, const RiskBudgetLimit& limit);
  void SetCurrencyLimit(const std::string& currency, const RiskBudgetLimit& limit);

  RiskBudgetReport Evaluate(
      const std::vector<RiskBudgetPosition>& positions) const;

 private:
  double max_gross_weight_;
  double max_single_name_weight_;
  double max_restricted_weight_;
  std::map<std::string, RiskBudgetLimit> sector_limits_;
  std::map<std::string, RiskBudgetLimit> country_limits_;
  std::map<std::string, RiskBudgetLimit> currency_limits_;

  void AddGroupBreaks(const std::string& scope,
                      const std::map<std::string, double>& weights,
                      const std::map<std::string, RiskBudgetLimit>& limits,
                      RiskBudgetReport* report) const;

  double PositionRiskContribution(const RiskBudgetPosition& position) const;
};

}  // namespace marketdata
