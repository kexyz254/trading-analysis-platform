#include "native/marketdata/binary_feed_decoder.h"

#include "native/marketdata/numeric_parser.h"
#include "native/marketdata/string_utils.h"

#include <cstring>
#include <utility>

namespace trading_assistant::native_marketdata {
namespace {

class ByteReader {
 public:
  explicit ByteReader(std::string_view input) : input_(input) {}

  bool ReadU8(std::uint8_t* out) {
    if (remaining() < 1) return false;
    *out = static_cast<std::uint8_t>(input_[offset_]);
    offset_ += 1;
    return true;
  }

  bool ReadU16(std::uint16_t* out) {
    if (remaining() < 2) return false;
    const auto b0 = static_cast<std::uint8_t>(input_[offset_]);
    const auto b1 = static_cast<std::uint8_t>(input_[offset_ + 1]);
    *out = static_cast<std::uint16_t>(b0 | (b1 << 8));
    offset_ += 2;
    return true;
  }

  bool ReadU32(std::uint32_t* out) {
    if (remaining() < 4) return false;
    std::uint32_t value = 0;
    for (int i = 0; i < 4; ++i) {
      value |= static_cast<std::uint32_t>(
                   static_cast<std::uint8_t>(input_[offset_ + i])) << (8 * i);
    }
    offset_ += 4;
    *out = value;
    return true;
  }

  bool ReadU64(std::uint64_t* out) {
    if (remaining() < 8) return false;
    std::uint64_t value = 0;
    for (int i = 0; i < 8; ++i) {
      value |= static_cast<std::uint64_t>(
                   static_cast<std::uint8_t>(input_[offset_ + i])) << (8 * i);
    }
    offset_ += 8;
    *out = value;
    return true;
  }

  bool ReadDouble(double* out) {
    std::uint64_t bits = 0;
    if (!ReadU64(&bits)) return false;
    static_assert(sizeof(double) == sizeof(std::uint64_t), "double must be 64-bit");
    std::memcpy(out, &bits, sizeof(double));
    return true;
  }

  bool ReadString(std::size_t length, std::string* out) {
    if (length > 64 || remaining() < length) return false;
    *out = std::string(input_.substr(offset_, length));
    offset_ += length;
    return true;
  }

  std::size_t remaining() const {
    return input_.size() - offset_;
  }

 private:
  std::string_view input_;
  std::size_t offset_ = 0;
};

bool DecodeOne(ByteReader* reader, FeedMessage* message) {
  std::uint8_t raw_type = 0;
  std::uint8_t symbol_length = 0;

  if (!reader->ReadU8(&raw_type)) return false;
  if (!reader->ReadU8(&symbol_length)) return false;

  if (raw_type < 1 || raw_type > 4) {
    return false;
  }

  message->type = static_cast<FeedMessageType>(raw_type);

  if (!reader->ReadString(symbol_length, &message->symbol)) return false;
  if (!IsAsciiSymbol(message->symbol, 24)) return false;
  if (!reader->ReadU64(&message->sequence)) return false;

  if (message->type == FeedMessageType::kTrade || message->type == FeedMessageType::kQuote) {
    if (!reader->ReadDouble(&message->price)) return false;
    if (!reader->ReadU64(&message->quantity)) return false;
    if (!IsValidPrice(message->price)) return false;
    if (!IsValidQuantity(message->quantity)) return false;
  }

  return true;
}

}  // namespace

std::vector<FeedMessage> DecodeBinaryFeedOrEmpty(std::string_view input) {
  std::vector<FeedMessage> messages;

  if (input.size() < 8 || input.size() > 262144) {
    return messages;
  }

  if (input.substr(0, 4) != "TAF1") {
    return messages;
  }

  ByteReader reader(input.substr(4));
  std::uint16_t version = 0;
  std::uint16_t count = 0;

  if (!reader.ReadU16(&version)) return {};
  if (!reader.ReadU16(&count)) return {};

  if (version == 0 || version > 4 || count > 2048) {
    return {};
  }

  messages.reserve(count);

  std::uint64_t previous_sequence = 0;
  bool have_previous = false;

  for (std::uint16_t i = 0; i < count; ++i) {
    FeedMessage message;
    if (!DecodeOne(&reader, &message)) {
      return {};
    }

    if (have_previous && message.sequence <= previous_sequence) {
      return {};
    }

    previous_sequence = message.sequence;
    have_previous = true;
    messages.push_back(std::move(message));
  }

  return messages;
}

bool ValidateBinaryFeed(std::string_view input) {
  const auto messages = DecodeBinaryFeedOrEmpty(input);
  return !messages.empty();
}

}  // namespace trading_assistant::native_marketdata
