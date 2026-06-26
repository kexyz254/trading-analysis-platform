#ifndef TRADING_ASSISTANT_NATIVE_FEATURE_MATRIX_H_
#define TRADING_ASSISTANT_NATIVE_FEATURE_MATRIX_H_

#include <cstdint>
#include <string>
#include <string_view>
#include <vector>

namespace trading_assistant::native_marketdata {

struct FeatureRow {
  std::string key;
  std::vector<double> values;
};

struct FeatureMatrix {
  std::vector<std::string> columns;
  std::vector<FeatureRow> rows;
};

FeatureMatrix ParseFeatureMatrixOrEmpty(std::string_view input);
bool ValidateFeatureMatrix(std::string_view input);

}  // namespace trading_assistant::native_marketdata

#endif
