#include "native/marketdata/string_utils.h"

#include <algorithm>
#include <cctype>

namespace trading_assistant::native_marketdata {

std::string TrimAscii(std::string_view value) {
  std::size_t begin = 0;
  while (begin < value.size() && std::isspace(static_cast<unsigned char>(value[begin]))) {
    ++begin;
  }

  std::size_t end = value.size();
  while (end > begin && std::isspace(static_cast<unsigned char>(value[end - 1]))) {
    --end;
  }

  return std::string(value.substr(begin, end - begin));
}

std::string ToUpperAscii(std::string_view value) {
  std::string out(value);
  std::transform(out.begin(), out.end(), out.begin(), [](unsigned char c) {
    return static_cast<char>(std::toupper(c));
  });
  return out;
}

std::string ToLowerAscii(std::string_view value) {
  std::string out(value);
  std::transform(out.begin(), out.end(), out.begin(), [](unsigned char c) {
    return static_cast<char>(std::tolower(c));
  });
  return out;
}

bool StartsWith(std::string_view value, std::string_view prefix) {
  return value.size() >= prefix.size() && value.substr(0, prefix.size()) == prefix;
}

bool EndsWith(std::string_view value, std::string_view suffix) {
  return value.size() >= suffix.size() &&
         value.substr(value.size() - suffix.size(), suffix.size()) == suffix;
}

bool IsAsciiSymbol(std::string_view value, std::size_t max_length) {
  if (value.empty() || value.size() > max_length) {
    return false;
  }

  for (unsigned char c : value) {
    if (!(std::isalnum(c) || c == '.' || c == '-' || c == '_' || c == '/')) {
      return false;
    }
  }

  return true;
}

bool IsAsciiInteger(std::string_view value) {
  const std::string trimmed = TrimAscii(value);
  if (trimmed.empty()) {
    return false;
  }

  for (unsigned char c : trimmed) {
    if (!std::isdigit(c)) {
      return false;
    }
  }

  return true;
}

bool IsAsciiDecimal(std::string_view value) {
  const std::string trimmed = TrimAscii(value);
  if (trimmed.empty()) {
    return false;
  }

  bool seen_digit = false;
  bool seen_dot = false;

  for (std::size_t i = 0; i < trimmed.size(); ++i) {
    unsigned char c = static_cast<unsigned char>(trimmed[i]);

    if (i == 0 && (c == '+' || c == '-')) {
      continue;
    }

    if (c == '.') {
      if (seen_dot) {
        return false;
      }
      seen_dot = true;
      continue;
    }

    if (!std::isdigit(c)) {
      return false;
    }

    seen_digit = true;
  }

  return seen_digit;
}

std::vector<std::string> SplitLine(std::string_view line, char delimiter, std::size_t max_fields) {
  std::vector<std::string> fields;
  std::string current;
  bool quoted = false;

  for (char c : line) {
    if (c == '"') {
      quoted = !quoted;
      continue;
    }

    if (!quoted && c == delimiter) {
      fields.push_back(TrimAscii(current));
      current.clear();
      if (max_fields != 0 && fields.size() >= max_fields) {
        return fields;
      }
      continue;
    }

    current.push_back(c);
  }

  fields.push_back(TrimAscii(current));
  return fields;
}

std::uint64_t StableHash64(std::string_view value) {
  std::uint64_t hash = 1469598103934665603ull;
  for (unsigned char c : value) {
    hash ^= c;
    hash *= 1099511628211ull;
  }
  return hash;
}

}  // namespace trading_assistant::native_marketdata
