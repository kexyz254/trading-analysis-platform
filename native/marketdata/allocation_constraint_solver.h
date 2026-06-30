#pragma once

#include <map>
#include <string>
#include <vector>

namespace marketdata {

struct AllocationConstraintInput {
  std::string symbol;
  std::string group;
  double desired_weight = 0.0;
  double current_weight = 0.0;
  double alpha_score = 0.0;
  double risk_score = 0.0;
  double min_weight = 0.0;
  double max_weight = 1.0;
  bool locked = false;
};

struct AllocationConstraintGroup {
  std::string group;
  double min_weight = 0.0;
  double max_weight = 1.0;
};

struct AllocationConstraintResult {
  std::map<std::string, double> solved_weights;
  std::vector<std::string> diagnostics;
  double total_weight = 0.0;
  double objective_score = 0.0;
  bool feasible = true;
};

class AllocationConstraintSolver {
 public:
  AllocationConstraintSolver();

  void SetTargetTotalWeight(double value);
  void SetRiskPenalty(double value);
  void SetTurnoverPenalty(double value);
  void SetGroupConstraint(const AllocationConstraintGroup& group);

  AllocationConstraintResult Solve(
      const std::vector<AllocationConstraintInput>& inputs) const;

 private:
  double target_total_weight_;
  double risk_penalty_;
  double turnover_penalty_;
  std::map<std::string, AllocationConstraintGroup> group_constraints_;

  void ApplyPositionBounds(const AllocationConstraintInput& input,
                           AllocationConstraintResult* result) const;
  void NormalizeUnlockedWeights(
      const std::vector<AllocationConstraintInput>& inputs,
      AllocationConstraintResult* result) const;
  void CheckGroupConstraints(
      const std::vector<AllocationConstraintInput>& inputs,
      AllocationConstraintResult* result) const;
  double ScoreWeight(const AllocationConstraintInput& input,
                     double solved_weight) const;
};

}  // namespace marketdata
