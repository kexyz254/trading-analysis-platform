#include "allocation_constraint_solver.h"

#include <algorithm>
#include <cmath>
#include <stdexcept>

namespace marketdata {
namespace {

double Clamp(double value, double low, double high) {
  return std::max(low, std::min(high, value));
}

bool Finite(double value) {
  return std::isfinite(value);
}

}  // namespace

AllocationConstraintSolver::AllocationConstraintSolver()
    : target_total_weight_(1.0),
      risk_penalty_(0.35),
      turnover_penalty_(0.10) {}

void AllocationConstraintSolver::SetTargetTotalWeight(double value) {
  if (!Finite(value) || value <= 0.0 || value > 2.0) {
    throw std::invalid_argument("target total weight must be within (0, 2]");
  }
  target_total_weight_ = value;
}

void AllocationConstraintSolver::SetRiskPenalty(double value) {
  if (!Finite(value) || value < 0.0) {
    throw std::invalid_argument("risk penalty must be non-negative");
  }
  risk_penalty_ = value;
}

void AllocationConstraintSolver::SetTurnoverPenalty(double value) {
  if (!Finite(value) || value < 0.0) {
    throw std::invalid_argument("turnover penalty must be non-negative");
  }
  turnover_penalty_ = value;
}

void AllocationConstraintSolver::SetGroupConstraint(
    const AllocationConstraintGroup& group) {
  if (group.group.empty()) {
    throw std::invalid_argument("group constraint requires a group");
  }
  if (!Finite(group.min_weight) || !Finite(group.max_weight) ||
      group.min_weight < 0.0 || group.max_weight < group.min_weight) {
    throw std::invalid_argument("invalid group constraint bounds");
  }
  group_constraints_[group.group] = group;
}

AllocationConstraintResult AllocationConstraintSolver::Solve(
    const std::vector<AllocationConstraintInput>& inputs) const {
  AllocationConstraintResult result;

  if (inputs.empty()) {
    result.feasible = false;
    result.diagnostics.push_back("no allocation inputs supplied");
    return result;
  }

  for (const AllocationConstraintInput& input : inputs) {
    ApplyPositionBounds(input, &result);
  }

  NormalizeUnlockedWeights(inputs, &result);
  CheckGroupConstraints(inputs, &result);

  for (const AllocationConstraintInput& input : inputs) {
    const auto it = result.solved_weights.find(input.symbol);
    if (it != result.solved_weights.end()) {
      result.objective_score += ScoreWeight(input, it->second);
      result.total_weight += it->second;
    }
  }

  if (std::fabs(result.total_weight - target_total_weight_) > 0.01) {
    result.feasible = false;
    result.diagnostics.push_back("solved total weight is outside tolerance");
  }

  return result;
}

void AllocationConstraintSolver::ApplyPositionBounds(
    const AllocationConstraintInput& input,
    AllocationConstraintResult* result) const {
  if (input.symbol.empty()) {
    result->feasible = false;
    result->diagnostics.push_back("input with missing symbol skipped");
    return;
  }

  if (!Finite(input.desired_weight) ||
      !Finite(input.current_weight) ||
      !Finite(input.alpha_score) ||
      !Finite(input.risk_score) ||
      !Finite(input.min_weight) ||
      !Finite(input.max_weight)) {
    result->feasible = false;
    result->diagnostics.push_back("non-finite allocation metric for " + input.symbol);
    return;
  }

  if (input.max_weight < input.min_weight) {
    result->feasible = false;
    result->diagnostics.push_back("invalid bounds for " + input.symbol);
    return;
  }

  if (input.locked) {
    result->solved_weights[input.symbol] =
        Clamp(input.current_weight, input.min_weight, input.max_weight);
    return;
  }

  const double risk_adjustment = risk_penalty_ * std::max(0.0, input.risk_score);
  const double alpha_adjustment = 0.05 * input.alpha_score;
  const double adjusted = input.desired_weight + alpha_adjustment - risk_adjustment;

  result->solved_weights[input.symbol] =
      Clamp(adjusted, input.min_weight, input.max_weight);
}

void AllocationConstraintSolver::NormalizeUnlockedWeights(
    const std::vector<AllocationConstraintInput>& inputs,
    AllocationConstraintResult* result) const {
  double locked_weight = 0.0;
  double unlocked_weight = 0.0;

  for (const AllocationConstraintInput& input : inputs) {
    const auto it = result->solved_weights.find(input.symbol);
    if (it == result->solved_weights.end()) {
      continue;
    }

    if (input.locked) {
      locked_weight += it->second;
    } else {
      unlocked_weight += it->second;
    }
  }

  const double remaining = target_total_weight_ - locked_weight;
  if (remaining < -0.000001) {
    result->feasible = false;
    result->diagnostics.push_back("locked weights exceed target total weight");
    return;
  }

  if (unlocked_weight <= 0.0) {
    if (remaining > 0.000001) {
      result->feasible = false;
      result->diagnostics.push_back("no unlocked weight available for normalization");
    }
    return;
  }

  const double scale = remaining / unlocked_weight;
  for (const AllocationConstraintInput& input : inputs) {
    if (input.locked) {
      continue;
    }

    auto it = result->solved_weights.find(input.symbol);
    if (it == result->solved_weights.end()) {
      continue;
    }

    it->second = Clamp(it->second * scale, input.min_weight, input.max_weight);
  }
}

void AllocationConstraintSolver::CheckGroupConstraints(
    const std::vector<AllocationConstraintInput>& inputs,
    AllocationConstraintResult* result) const {
  std::map<std::string, double> group_weights;

  for (const AllocationConstraintInput& input : inputs) {
    const auto it = result->solved_weights.find(input.symbol);
    if (it == result->solved_weights.end()) {
      continue;
    }

    group_weights[input.group] += it->second;
  }

  for (const auto& item : group_weights) {
    const auto limit_it = group_constraints_.find(item.first);
    if (limit_it == group_constraints_.end()) {
      continue;
    }

    const AllocationConstraintGroup& limit = limit_it->second;
    if (item.second < limit.min_weight || item.second > limit.max_weight) {
      result->feasible = false;
      result->diagnostics.push_back("group constraint failed for " + item.first);
    }
  }
}

double AllocationConstraintSolver::ScoreWeight(
    const AllocationConstraintInput& input,
    double solved_weight) const {
  const double alpha_value = solved_weight * input.alpha_score;
  const double risk_cost = risk_penalty_ * solved_weight * std::max(0.0, input.risk_score);
  const double turnover_cost =
      turnover_penalty_ * std::fabs(solved_weight - input.current_weight);

  return alpha_value - risk_cost - turnover_cost;
}

}  // namespace marketdata
