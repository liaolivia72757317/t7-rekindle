#pragma once
#include "../core/Common.h"

namespace t7 {
struct Ap32Block {
    Bytes bytes;
    size_t consumed = 0;
};

Ap32Block decodeAp32(const Bytes& input, size_t offset, size_t maximumOutput);
}
