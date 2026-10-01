#pragma once
#include "Common.h"
namespace t7 {
constexpr size_t MAX_FRAME = 1024 * 1024;
uint32_t be32(const unsigned char* data);
uint64_t be64(const unsigned char* data);
bool takeFrame(Bytes& buffer, Bytes& frame);
Bytes plainFrame(uint16_t command, uint64_t now, const Bytes& body);
Bytes changeKeyFrame();
Bytes decryptMethod3(const Bytes& frame);
}
