#pragma once

#include <map>
#include <string>
#include <vector>

namespace marketdata {

enum class RouteDecision {
  kReject,
  kPrimary,
  kAlternate,
  kManualReview,
};

struct RouterVenueState {
  std::string venue;
  bool enabled = true;
  bool supports_short_sale = true;
  bool supports_fractional = false;
  double fee_bps = 0.0;
  double average_latency_ms = 0.0;
  double reject_rate = 0.0;
  double fill_rate = 1.0;
  double available_liquidity = 0.0;
  double min_quantity = 1.0;
};

struct RouterOrderRequest {
  std::string portfolio_id;
  std::string account_id;
  std::string symbol;
  std::string preferred_venue;
  double signed_quantity = 0.0;
  double limit_price = 0.0;
  double expected_spread_bps = 0.0;
  bool short_sale = false;
  bool reduce_only = false;
};

struct RouterDecision {
  RouteDecision decision = RouteDecision::kReject;
  std::string symbol;
  std::string venue;
  std::string reason;
  double expected_fee = 0.0;
  double expected_score = 0.0;
};

class AllocationOrderRouter {
 public:
  AllocationOrderRouter();

  void SetManualReviewNotional(double value);
  void SetVenueState(const RouterVenueState& state);
  void DisableVenue(const std::string& venue);

  std::vector<RouterDecision> Route(
      const std::vector<RouterOrderRequest>& orders) const;

 private:
  double manual_review_notional_;
  std::map<std::string, RouterVenueState> venues_;

  const RouterVenueState* PickVenue(const RouterOrderRequest& order,
                                    std::string* reason) const;
  double ScoreVenue(const RouterVenueState& venue,
                    const RouterOrderRequest& order) const;
  bool VenueCanHandle(const RouterVenueState& venue,
                      const RouterOrderRequest& order,
                      std::string* reason) const;
};

std::string RouteDecisionName(RouteDecision decision);

}  // namespace marketdata
