#include "Ap32.h"
#include <array>
#include <utility>

namespace t7 {
namespace {
constexpr std::array<uint32_t, 256> makeCrcTable() {
    std::array<uint32_t, 256> table{};
    for (uint32_t i = 0; i < table.size(); ++i) {
        uint32_t value = i;
        for (unsigned bit = 0; bit < 8; ++bit)
            value = (value >> 1) ^ ((value & 1) ? 0xEDB88320u : 0);
        table[i] = value;
    }
    return table;
}
constexpr auto CRC_TABLE = makeCrcTable();
uint32_t crc32(const unsigned char* data, size_t size) {
    uint32_t value = UINT32_MAX;
    for (size_t i = 0; i < size; ++i) value = (value >> 8) ^ CRC_TABLE[(value ^ data[i]) & 255];
    return ~value;
}
uint32_t read32(const unsigned char* data) {
    return static_cast<uint32_t>(data[0]) | (static_cast<uint32_t>(data[1]) << 8)
        | (static_cast<uint32_t>(data[2]) << 16) | (static_cast<uint32_t>(data[3]) << 24);
}
class BlockReader {
    const unsigned char* source_;
    size_t sourceSize_, limit_, cursor_ = 0;
    unsigned tag_ = 0, mask_ = 0;
    Bytes output_;

    unsigned byte() {
        if (cursor_ == sourceSize_) throw std::runtime_error("AP32 truncated bitstream");
        return source_[cursor_++];
    }
    unsigned bit() {
        if (!mask_) { tag_ = byte(); mask_ = 128; }
        const auto value = (tag_ & mask_) != 0;
        mask_ >>= 1;
        return value ? 1u : 0u;
    }
    uint32_t gamma() {
        uint32_t value = 1;
        do {
            if (value > (UINT32_MAX >> 1)) throw std::runtime_error("AP32 gamma overflow");
            value = (value << 1) | bit();
        } while (bit());
        return value;
    }
    void append(unsigned value) {
        if (output_.size() == limit_) throw std::runtime_error("AP32 output overflow");
        output_.push_back(static_cast<unsigned char>(value));
    }
    void copy(size_t distance, size_t length) {
        if (!distance || distance > output_.size()) throw std::runtime_error("AP32 invalid match distance");
        if (length > limit_ - output_.size()) throw std::runtime_error("AP32 match output overflow");
        for (size_t i = 0; i < length; ++i) output_.push_back(output_[output_.size() - distance]);
    }
public:
    BlockReader(const unsigned char* source, size_t size, size_t limit)
        : source_(source), sourceSize_(size), limit_(limit) { output_.reserve(limit); }

    Bytes decode() {
        size_t lastDistance = 0;
        bool afterMatch = false;
        append(byte());
        for (;;) {
            if (!bit()) {
                append(byte());
                afterMatch = false;
            } else if (!bit()) {
                const auto high = gamma();
                size_t distance = 0, length = 0;
                if (!afterMatch && high == 2) {
                    distance = lastDistance; length = gamma();
                } else {
                    const auto adjusted = high - (afterMatch ? 2u : 3u);
                    if (adjusted > (UINT32_MAX >> 8)) throw std::runtime_error("AP32 distance overflow");
                    distance = (static_cast<size_t>(adjusted) << 8) | byte();
                    length = gamma();
                    if (distance >= 32000) ++length;
                    if (distance >= 1280) ++length;
                    if (distance < 128) length += 2;
                    lastDistance = distance;
                }
                copy(distance, length);
                afterMatch = true;
            } else if (!bit()) {
                const auto pair = byte();
                const auto distance = pair >> 1;
                if (!distance) {
                    if (output_.size() != limit_ || cursor_ != sourceSize_)
                        throw std::runtime_error("AP32 end marker size mismatch");
                    return std::move(output_);
                }
                copy(distance, 2 + (pair & 1));
                lastDistance = distance; afterMatch = true;
            } else {
                unsigned distance = 0;
                for (unsigned i = 0; i < 4; ++i) distance = (distance << 1) | bit();
                if (distance) copy(distance, 1);
                else append(0);
                afterMatch = false;
            }
        }
    }
};
}

Ap32Block decodeAp32(const Bytes& input, size_t offset, size_t maximumOutput) {
    constexpr size_t HEADER_SIZE = 24;
    if (offset > input.size() || input.size() - offset < HEADER_SIZE)
        throw std::runtime_error("AP32 truncated header");
    const auto header = input.data() + offset;
    if (read32(header) != 0x32335041u || read32(header + 4) != HEADER_SIZE)
        throw std::runtime_error("AP32 unsupported header");
    const auto packedSize = read32(header + 8), originalSize = read32(header + 16);
    if (!packedSize || packedSize > input.size() - offset - HEADER_SIZE)
        throw std::runtime_error("AP32 packed size outside input");
    if (!originalSize || originalSize > maximumOutput)
        throw std::runtime_error("AP32 original size outside output limit");
    const auto packed = header + HEADER_SIZE;
    if (crc32(packed, packedSize) != read32(header + 12))
        throw std::runtime_error("AP32 packed CRC mismatch");
    auto decoded = BlockReader(packed, packedSize, originalSize).decode();
    if (crc32(decoded.data(), decoded.size()) != read32(header + 20))
        throw std::runtime_error("AP32 original CRC mismatch");
    return {std::move(decoded), HEADER_SIZE + packedSize};
}
}
