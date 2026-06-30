#include "allocation_order_router.h"

#include <algorithm>
#include <cmath>
#include <limits>
#include <stdexcept>

namespace marketdata {
namespace {

double Abs(double value) {
  return value < 0.0 ? -value : value;
}

bool ValidNumber(double value) {
  return std::isfinite(value);
}

}  // namespace

AllocationOrderRouter::AllocationOrderRouter()
    : manual_review_notional_(500000.0) {
  RouterVenueState auto_venue;
  auto_venue.venue = "AUTO";
  venues_[auto_venue.venue] = auto_venue;
}

void AllocationOrderRouter::SetManualReviewNotional(double value) {
  if (!ValidNumber(value) || value < 0.0) {
    throw std::invalid_argument("manual review notional must be non-negative");
  }
  manual_review_notional_ = value;
}

void AllocationOrderRouter::SetVenueState(const RouterVenueState& state) {
  if (state.venue.empty()) {
    throw std::invalid_argument("venue state requires venue");
  }
  if (!ValidNumber(state.fill_rate) ||
      !ValidNumber(state.reject_rate) ||
      !ValidNumber(state.fee_bps)) {
    throw std::invalid_argument("venue state contains invalid metric");
  }
  venues_[state.venue] = state;
}

void AllocationOrderRouter::DisableVenue(const std::string& venue) {
  auto it = venues_.find(venue);
  if (it != venues_.end()) {
    it->second.enabled = false;
  }
}

std::vector<RouterDecision> AllocationOrderRouter::Route(
    const std::vector<RouterOrderRequest>& orders) const {
  std::vector<RouterDecision> decisions;

  for (const RouterOrderRequest& order : orders) {
    RouterDecision decision;
    decision.symbol = order.symbol;

    if (order.symbol.empty() || !ValidNumber(order.signed_quantity) ||
        !ValidNumber(order.limit_price) || order.limit_price <= 0.0) {
      decision.reason = "invalid order request";
      decisions.push_back(decision);
      continue;
    }

    const double notional = Abs(order.signed_quantity) * order.limit_price;
    if (notional >= manual_review_notional_) {
      decision.decision = RouteDecision::kManualReview;
      decision.reason = "order notional requires manual review";
      decisions.push_back(decision);
      continue;
    }

    std::string reason;
    const RouterVenueState* venue = PickVenue(order, &reason);
    if (venue == nullptr) {
      decision.reason = reason.empty() ? "no eligible venue" : reason;
      decisions.push_back(decision);
      continue;
    }

    decision.venue = venue->venue;
    decision.expected_fee = notional * venue->fee_bps / 10000.0;
    decision.expected_score = ScoreVenue(*venue, order);
    decision.reason = "venue selected by routing score";
    decision.decision = order.preferred_venue == venue->venue
                            ? RouteDecision::kPrimary
                            : RouteDecision::kAlternate;
    decisions.push_back(decision);
  }

  return decisions;
}

const RouterVenueState* AllocationOrderRouter::PickVenue(
    const RouterOrderRequest& order,
    std::string* reason) const {
  const RouterVenueState* best = nullptr;
  double best_score = -std::numeric_limits<double>::infinity();

  if (!order.preferred_venue.empty()) {
    auto it = venues_.find(order.preferred_venue);
    if (it != venues_.end()) {
      std::string local_reason;
      if (VenueCanHandle(it->second, order, &local_reason)) {
        return &it->second;
      }
      *reason = local_reason;
    }
  }

  for (const auto& item : venues_) {
    std::string local_reason;
    if (!VenueCanHandle(item.second, order, &local_reason)) {
      continue;
    }

    const double score = ScoreVenue(item.second, order);
    if (score > best_score) {
      best_score = score;
      best = &item.second;
    }
  }

  return best;
}

double AllocationOrderRouter::ScoreVenue(
    const RouterVenueState& venue,
    const RouterOrderRequest& order) const {
  const double liquidity =
      venue.available_liquidity > 0.0
          ? std::min(1.0, Abs(order.signed_quantity) / venue.available_liquidity)
          : 0.5;

  const double fee_penalty = venue.fee_bps * 0.02;
  const double latency_penalty = venue.average_latency_ms * 0.001;
  const double reject_penalty = venue.reject_rate * 4.0;
  const double spread_penalty = order.expected_spread_bps * 0.01;
  const double fill_bonus = venue.fill_rate * 2.0;
  const double liquidity_penalty = liquidity;

  return fill_bonus - fee_penalty - latency_penalty -
         reject_penalty - spread_penalty - liquidity_penalty;
}

bool AllocationOrderRouter::VenueCanHandle(
    const RouterVenueState& venue,
    const RouterOrderRequest& order,
    std::string* reason) const {
  if (!venue.enabled) {
    *reason = "venue disabled";
    return false;
  }

  if (order.short_sale && !venue.supports_short_sale) {
    *reason = "venue does not support short sales";
    return false;
  }

  if (!venue.supports_fractional) {
    const double rounded = std::floor(Abs(order.signed_quantity));
    if (std::fabs(rounded - Abs(order.signed_quantity)) > 0.000001) {
      *reason = "venue does not support fractional quantity";
      return false;
    }
  }

  if (Abs(order.signed_quantity) < venue.min_quantity) {
    *reason = "quantity below venue minimum";
    return false;
  }

  if (venue.available_liquidity > 0.0 &&
      Abs(order.signed_quantity) > venue.available_liquidity) {
    *reason = "quantity exceeds venue liquidity";
    return false;
  }

  return true;
}

std::string RouteDecisionName(RouteDecision decision) {
  switch (decision) {
    case RouteDecision::kReject:
      return "reject";
    case RouteDecision::kPrimary:
      return "primary";
    case RouteDecision::kAlternate:
      return "alternate";
    case RouteDecision::kManualReview:
      return "manual_review";
  }
  return "unknown";
}

}  // namespace marketdata
