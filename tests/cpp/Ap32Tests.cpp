#include "../../src/Runtime/launcher/Ap32.h"
#include <iostream>

namespace {
using t7::Bytes;
uint32_t fixtureCrc(const Bytes& bytes) {
    uint32_t value = UINT32_MAX;
    for (const auto byte : bytes) {
        value ^= byte;
        for (unsigned bit = 0; bit < 8; ++bit)
            value = (value >> 1) ^ ((value & 1) ? 0xEDB88320u : 0);
    }
    return ~value;
}
void put32(Bytes& bytes, size_t offset, uint32_t value) {
    for (unsigned i = 0; i < 4; ++i) bytes.at(offset + i) = static_cast<unsigned char>(value >> (8 * i));
}
Bytes wrap(const Bytes& packed, const Bytes& original) {
    Bytes result(24);
    put32(result, 0, 0x32335041u); put32(result, 4, 24);
    put32(result, 8, static_cast<uint32_t>(packed.size())); put32(result, 12, fixtureCrc(packed));
    put32(result, 16, static_cast<uint32_t>(original.size())); put32(result, 20, fixtureCrc(original));
    result.insert(result.end(), packed.begin(), packed.end());
    return result;
}
struct Stream {
    Bytes bytes;
    size_t tag = 0;
    unsigned mask = 0;
    explicit Stream(unsigned char first) : bytes{first} {}
    void bit(unsigned value) {
        if (!mask) { tag = bytes.size(); bytes.push_back(0); mask = 128; }
        if (value) bytes[tag] |= static_cast<unsigned char>(mask);
        mask >>= 1;
    }
    void gamma(unsigned value) {
        unsigned shift = 0;
        for (auto remaining = value; remaining > 1; remaining >>= 1) ++shift;
        while (shift) { --shift; bit((value >> shift) & 1); bit(shift != 0); }
    }
    void literal(unsigned char value) { bit(0); bytes.push_back(value); }
    void single(unsigned distance) {
        bit(1); bit(1); bit(1);
        for (int i = 3; i >= 0; --i) bit((distance >> i) & 1);
    }
    void shortMatch(unsigned distance, unsigned length) {
        bit(1); bit(1); bit(0);
        bytes.push_back(static_cast<unsigned char>((distance << 1) | (length - 2)));
    }
    void longMatch(unsigned distance, unsigned encodedLength, bool afterMatch) {
        bit(1); bit(0); gamma((distance >> 8) + (afterMatch ? 2 : 3));
        bytes.push_back(static_cast<unsigned char>(distance)); gamma(encodedLength);
    }
    void repeat(unsigned length) { bit(1); bit(0); gamma(2); gamma(length); }
    void end() { shortMatch(0, 2); }
};
bool rejected(const Bytes& input, size_t maximum = 131072, size_t offset = 0) {
    try { t7::decodeAp32(input, offset, maximum); }
    catch (const std::runtime_error&) { return true; }
    return false;
}
void require(bool condition, const char* message) {
    if (!condition) throw std::runtime_error(message);
}
void verify(Stream& stream, const Bytes& expected) {
    stream.end();
    const auto input = wrap(stream.bytes, expected);
    const auto decoded = t7::decodeAp32(input, 0, expected.size());
    require(decoded.bytes == expected && decoded.consumed == input.size(), "decoded fixture mismatch");
}
}

bool verifyAp32() {
    try {
        require(fixtureCrc(Bytes{'1','2','3','4','5','6','7','8','9'}) == 0xCBF43926u, "fixture CRC vector");
        Stream literal('A');
        Bytes text{'A'};
        for (unsigned i = 0; i < 40; ++i) { literal.literal(static_cast<unsigned char>(i)); text.push_back(static_cast<unsigned char>(i)); }
        verify(literal, text);
        Stream single('A'); single.single(1); single.single(0); single.literal('B');
        verify(single, Bytes{'A','A',0,'B'});
        Stream shortCopy('A'); shortCopy.shortMatch(1, 3); shortCopy.shortMatch(4, 2);
        verify(shortCopy, Bytes(6, 'A'));
        Stream repeated('A'); repeated.longMatch(1, 2, false); repeated.literal('B'); repeated.repeat(3);
        verify(repeated, Bytes{'A','A','A','A','A','B','B','B','B'});
        Stream consecutive('A'); consecutive.longMatch(1, 2, false); consecutive.longMatch(1, 2, true);
        verify(consecutive, Bytes(9, 'A'));
        for (const unsigned distance : {127u, 128u, 1279u, 1280u, 31999u, 32000u}) {
            Stream stream('A'); Bytes expected{'A'};
            for (unsigned i = 1; i < distance; ++i) {
                const auto value = static_cast<unsigned char>(i % 251);
                stream.literal(value); expected.push_back(value);
            }
            stream.longMatch(distance, 2, false);
            const unsigned length = 2 + (distance >= 32000) + (distance >= 1280) + 2 * (distance < 128);
            for (unsigned i = 0; i < length; ++i) expected.push_back(expected[expected.size() - distance]);
            verify(stream, expected);
        }
        Stream one('A'); one.end();
        const auto valid = wrap(one.bytes, Bytes{'A'});
        for (size_t length = 0; length < valid.size(); ++length)
            require(rejected(Bytes(valid.begin(), valid.begin() + length)), "truncation accepted");
        require(rejected(valid, 0), "output bound ignored");
        require(rejected(valid, 1, SIZE_MAX), "offset overflow accepted");
        for (const size_t field : {size_t{0}, size_t{4}, size_t{8}, size_t{12}, size_t{16}, size_t{20}}) {
            auto damaged = valid; damaged[field] ^= 1;
            require(rejected(damaged), "invalid AP32 header accepted");
        }
        auto excessive = valid; put32(excessive, 8, UINT32_MAX);
        require(rejected(excessive), "packed size overflow accepted");
        excessive = valid; put32(excessive, 16, UINT32_MAX);
        require(rejected(excessive), "original size overflow accepted");
        auto badPacked = one.bytes; badPacked.push_back(0);
        require(rejected(wrap(badPacked, Bytes{'A'})), "unused packed bytes accepted");
        require(rejected(wrap(Bytes{}, Bytes{'A'})), "empty packed stream accepted");
        require(rejected(wrap(one.bytes, Bytes{'A','A'})), "early terminator accepted");
        Stream overflow('A'); overflow.literal('B'); overflow.end();
        require(rejected(wrap(overflow.bytes, Bytes{'A'})), "literal output overflow accepted");
        Stream matchOverflow('A'); matchOverflow.shortMatch(1, 3); matchOverflow.end();
        require(rejected(wrap(matchOverflow.bytes, Bytes{'A'})), "match output overflow accepted");
        Stream beforeStart('A'); beforeStart.shortMatch(2, 2); beforeStart.end();
        require(rejected(wrap(beforeStart.bytes, Bytes(3, 'A'))), "match before output accepted");
        Stream invalidSingle('A'); invalidSingle.single(15); invalidSingle.end();
        require(rejected(wrap(invalidSingle.bytes, Bytes(2, 'A'))), "single before output accepted");
        Stream uninitializedRepeat('A'); uninitializedRepeat.repeat(2); uninitializedRepeat.end();
        require(rejected(wrap(uninitializedRepeat.bytes, Bytes(3, 'A'))), "uninitialized repeat accepted");
        Stream gammaOverflow('A'); gammaOverflow.bit(1); gammaOverflow.bit(0);
        for (unsigned i = 0; i < 70; ++i) gammaOverflow.bit(1);
        require(rejected(wrap(gammaOverflow.bytes, Bytes(10, 'A'))), "gamma overflow accepted");
        Stream distanceOverflow('A'); distanceOverflow.bit(1); distanceOverflow.bit(0); distanceOverflow.gamma(UINT32_MAX);
        require(rejected(wrap(distanceOverflow.bytes, Bytes(10, 'A'))), "distance overflow accepted");
        Bytes framed{5,6,7}; framed.insert(framed.end(), valid.begin(), valid.end()); framed.push_back(8);
        const auto frame = t7::decodeAp32(framed, 3, 1);
        require(frame.bytes == Bytes{'A'} && frame.consumed == valid.size(), "block consumption mismatch");
        std::cout << "AP32 synthetic decoding and rejection cases passed\n";
        return true;
    } catch (const std::exception& error) {
        std::cerr << "AP32 test failure: " << error.what() << '\n';
        return false;
    }
}
