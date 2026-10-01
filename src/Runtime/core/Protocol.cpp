#include "Protocol.h"
#include <bcrypt.h>
#include <algorithm>

namespace t7 {
uint32_t be32(const unsigned char* p) { return uint32_t(p[0]) << 24 | uint32_t(p[1]) << 16 | uint32_t(p[2]) << 8 | p[3]; }
uint64_t be64(const unsigned char* p) { return uint64_t(be32(p)) << 32 | be32(p + 4); }
static void append32(Bytes& data, uint32_t n) {
    for (int shift = 24; shift >= 0; shift -= 8) data.push_back(static_cast<unsigned char>(n >> shift));
}
bool takeFrame(Bytes& buffer, Bytes& frame) {
    if (buffer.size() < 12) return false;
    if (buffer[0] != 0x55 || buffer[1] != 0x0e) throw std::runtime_error("invalid TPDU magic/version");
    uint64_t header = be32(buffer.data() + 4), body = be32(buffer.data() + 8);
    if (!header) {
        if (buffer[2] != 3) throw std::runtime_error("unsupported zero-head control command");
        if (buffer.size() < 29) return false;
        if (be32(buffer.data() + 20) != 3) throw std::runtime_error("expected synthetic QQUNIFIED auth");
        header = 29 + buffer[28];
    }
    if (header < 12 || header + body > MAX_FRAME) throw std::runtime_error("invalid TPDU length");
    size_t length = static_cast<size_t>(header + body);
    if (buffer.size() < length) return false;
    frame.assign(buffer.begin(), buffer.begin() + length);
    buffer.erase(buffer.begin(), buffer.begin() + length);
    return true;
}
Bytes plainFrame(uint16_t command, uint64_t now, const Bytes& body) {
    if (body.size() > MAX_FRAME - 23) throw std::runtime_error("body too large");
    Bytes result{0x55, 0x0e, 5, 0}; append32(result, 12); append32(result, static_cast<uint32_t>(body.size() + 11));
    result.push_back(0); result.push_back(static_cast<unsigned char>(command >> 8)); result.push_back(static_cast<unsigned char>(command));
    append32(result, static_cast<uint32_t>(now >> 32)); append32(result, static_cast<uint32_t>(now));
    result.insert(result.end(), body.begin(), body.end()); return result;
}
Bytes changeKeyFrame() {
    const char* hex = "550e01000000003000000000000300207aca0fd9bcd6ec7c9f97466616e6a2826f4220bc6fb86f56960c05cd30dbe3f8";
    Bytes result;
    for (size_t i = 0; hex[i]; i += 2) {
        char value[3]{hex[i], hex[i + 1], 0}; result.push_back(static_cast<unsigned char>(strtoul(value, nullptr, 16)));
    }
    return result;
}
Bytes decryptMethod3(const Bytes& frame) {
    if (frame.size() < 28 || frame[2] != 0 || frame[3] != 4 || be32(frame.data() + 4) != 12)
        throw std::runtime_error("unsupported method-3 frame");
    size_t size = frame.size() - 12;
    if (size % 16) throw std::runtime_error("AES ciphertext is not block aligned");
    // Deterministic local fixture key; no account or deployment secret is used.
    unsigned char keyBytes[16]{};
    unsigned char iv[16]; for (unsigned char i = 0; i < 16; ++i) iv[i] = i;
    BCRYPT_ALG_HANDLE algorithm = nullptr; BCRYPT_KEY_HANDLE key = nullptr;
    Bytes result(size); ULONG written = 0;
    NTSTATUS status = BCryptOpenAlgorithmProvider(&algorithm, BCRYPT_AES_ALGORITHM, nullptr, 0);
    if (status >= 0) status = BCryptSetProperty(algorithm, BCRYPT_CHAINING_MODE,
        reinterpret_cast<PUCHAR>(const_cast<wchar_t*>(BCRYPT_CHAIN_MODE_CBC)), sizeof(BCRYPT_CHAIN_MODE_CBC), 0);
    if (status >= 0) status = BCryptGenerateSymmetricKey(algorithm, &key, nullptr, 0, keyBytes, sizeof(keyBytes), 0);
    if (status >= 0) status = BCryptDecrypt(key, const_cast<PUCHAR>(frame.data() + 12), static_cast<ULONG>(size),
        nullptr, iv, sizeof(iv), result.data(), static_cast<ULONG>(result.size()), &written, 0);
    if (key) BCryptDestroyKey(key);
    if (algorithm) BCryptCloseAlgorithmProvider(algorithm, 0);
    if (status < 0 || written != size || size < 16) throw std::runtime_error("AES decrypt failed");
    unsigned padding = result.back();
    if (padding < 6 || padding >= size || memcmp(result.data() + size - 6, "tsf4g", 5)) throw std::runtime_error("method-3 marker/padding invalid");
    size_t payload = size - padding, remainder = payload % 16;
    if (padding != (remainder <= 10 ? 16 : 32) - remainder) throw std::runtime_error("method-3 padding length mismatch");
    result.resize(payload); return result;
}
}
