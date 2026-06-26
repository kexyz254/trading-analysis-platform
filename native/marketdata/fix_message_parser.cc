#include "native/marketdata/fix_message_parser.h"

#include "native/marketdata/numeric_parser.h"
#include "native/marketdata/string_utils.h"

#include <set>

namespace trading_assistant::native_marketdata {
namespace {

bool IsPrintableFixValue(std::string_view value) {
  if (value.empty() || value.size() > 512) {
    return false;
  }

  for (unsigned char c : value) {
    if (c < 32 || c > 126) {
      return false;
    }
  }

  return true;
}

bool RequiredTagsPresent(const FixMessage& message) {
  return message.index.count(8) != 0 &&
         message.index.count(35) != 0 &&
         message.index.count(49) != 0 &&
         message.index.count(56) != 0 &&
         message.index.count(34) != 0 &&
         message.index.count(52) != 0;
}

bool MessageTypeLooksValid(const std::string& value) {
  return value == "0" || value == "1" || value == "2" || value == "4" ||
         value == "5" || value == "8" || value == "9" || value == "D" ||
         value == "F" || value == "G" || value == "W" || value == "X";
}

bool ValidateSequenceNumber(const FixMessage& message) {
  const auto it = message.index.find(34);
  if (it == message.index.end()) {
    return false;
  }

  const auto seq = ParseUnsigned64(it->second);
  return seq.has_value() && *seq > 0;
}

bool ValidateBodyLengthField(const FixMessage& message) {
  const auto it = message.index.find(9);
  if (it == message.index.end()) {
    return true;
  }

  const auto length = ParseUnsigned64(it->second);
  return length.has_value() && *length <= 65535;
}

}  // namespace

FixMessage ParseFixMessageOrEmpty(std::string_view input) {
  FixMessage message;

  if (input.empty() || input.size() > 65536) {
    return {};
  }

  const char delimiter = input.find('\x01') != std::string_view::npos ? '\x01' : '|';
  std::set<std::uint32_t> seen_once_tags;

  std::size_t offset = 0;
  while (offset <= input.size()) {
    std::size_t next = input.find(delimiter, offset);
    if (next == std::string_view::npos) {
      next = input.size();
    }

    const std::string_view token = input.substr(offset, next - offset);
    offset = next + 1;

    if (TrimAscii(token).empty()) {
      if (next == input.size()) {
        break;
      }
      continue;
    }

    const std::size_t equal = token.find('=');
    if (equal == std::string_view::npos || equal == 0 || equal + 1 >= token.size()) {
      return {};
    }

    const auto tag = ParseUnsigned64(token.substr(0, equal));
    if (!tag.has_value() || *tag > 999999) {
      return {};
    }

    std::string value = TrimAscii(token.substr(equal + 1));
    if (!IsPrintableFixValue(value)) {
      return {};
    }

    const std::uint32_t tag32 = static_cast<std::uint32_t>(*tag);

    if ((tag32 == 8 || tag32 == 9 || tag32 == 35 || tag32 == 49 ||
         tag32 == 56 || tag32 == 34 || tag32 == 52) &&
        seen_once_tags.count(tag32) != 0) {
      return {};
    }

    seen_once_tags.insert(tag32);
    message.fields.push_back(FixField{tag32, value});
    message.index[tag32] = value;

    if (message.fields.size() > 2048) {
      return {};
    }

    if (next == input.size()) {
      break;
    }
  }

  return message;
}

bool ValidateFixMessage(std::string_view input) {
  const FixMessage message = ParseFixMessageOrEmpty(input);

  if (message.fields.empty()) {
    return false;
  }

  if (!RequiredTagsPresent(message)) {
    return false;
  }

  const auto type_it = message.index.find(35);
  if (type_it == message.index.end() || !MessageTypeLooksValid(type_it->second)) {
    return false;
  }

  if (!ValidateSequenceNumber(message)) {
    return false;
  }

  return ValidateBodyLengthField(message);
}

}  // namespace trading_assistant::native_marketdata
