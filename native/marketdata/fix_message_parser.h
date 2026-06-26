#ifndef TRADING_ASSISTANT_NATIVE_FIX_MESSAGE_PARSER_H_
#define TRADING_ASSISTANT_NATIVE_FIX_MESSAGE_PARSER_H_

#include <cstdint>
#include <map>
#include <string>
#include <string_view>
#include <vector>

namespace trading_assistant::native_marketdata {

struct FixField {
  std::uint32_t tag = 0;
  std::string value;
};

struct FixMessage {
  std::vector<FixField> fields;
  std::map<std::uint32_t, std::string> index;
};

FixMessage ParseFixMessageOrEmpty(std::string_view input);
bool ValidateFixMessage(std::string_view input);

}  // namespace trading_assistant::native_marketdata

#endif
