#include "native/marketdata/feature_matrix.h"

#include "native/marketdata/numeric_parser.h"
#include "native/marketdata/string_utils.h"

#include <cmath>
#include <set>
#include <utility>

namespace trading_assistant::native_marketdata {
namespace {

bool IsValidFeatureName(std::string_view value) {
  return IsAsciiSymbol(value, 64);
}

bool RowValuesAreFinite(const std::vector<double>& values) {
  for (double value : values) {
    if (!std::isfinite(value) || std::fabs(value) > 1000000000.0) {
      return false;
    }
  }
  return true;
}

}  // namespace

FeatureMatrix ParseFeatureMatrixOrEmpty(std::string_view input) {
  FeatureMatrix matrix;

  if (input.empty() || input.size() > 524288) {
    return {};
  }

  std::set<std::string> row_keys;
  bool header_seen = false;
  std::size_t expected_columns = 0;
  std::size_t offset = 0;

  while (offset <= input.size()) {
    std::size_t next = input.find('\n', offset);
    if (next == std::string_view::npos) next = input.size();

    const std::string_view line = input.substr(offset, next - offset);
    offset = next + 1;

    if (TrimAscii(line).empty()) {
      if (next == input.size()) break;
      continue;
    }

    const auto fields = SplitLine(line, ',', 256);
    if (fields.size() < 2) {
      return {};
    }

    if (!header_seen) {
      if (ToUpperAscii(fields[0]) != "KEY") {
        return {};
      }

      for (std::size_t i = 1; i < fields.size(); ++i) {
        if (!IsValidFeatureName(fields[i])) {
          return {};
        }
        matrix.columns.push_back(fields[i]);
      }

      expected_columns = matrix.columns.size();
      header_seen = true;

      if (next == input.size()) break;
      continue;
    }

    if (fields.size() != expected_columns + 1) {
      return {};
    }

    FeatureRow row;
    row.key = fields[0];

    if (!IsAsciiSymbol(row.key, 64) || row_keys.count(row.key) != 0) {
      return {};
    }

    row_keys.insert(row.key);

    for (std::size_t i = 1; i < fields.size(); ++i) {
      const auto value = ParseFiniteDouble(fields[i]);
      if (!value.has_value()) {
        return {};
      }
      row.values.push_back(*value);
    }

    if (!RowValuesAreFinite(row.values)) {
      return {};
    }

    matrix.rows.push_back(std::move(row));

    if (matrix.rows.size() > 8192) {
      return {};
    }

    if (next == input.size()) break;
  }

  return matrix;
}

bool ValidateFeatureMatrix(std::string_view input) {
  const FeatureMatrix matrix = ParseFeatureMatrixOrEmpty(input);
  return !matrix.columns.empty() && !matrix.rows.empty();
}

}  // namespace trading_assistant::native_marketdata
