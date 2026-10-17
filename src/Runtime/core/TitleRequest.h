#pragma once
#include <cstdint>

namespace t7r {
struct TitleRequest { uint64_t requestId = 0, epoch = 0, connection = 0; uint32_t titleId = 0; };
}
