#pragma once
#include "../core/Common.h"
#include <algorithm>

namespace t7::detail {
enum class DrainResult { Empty, Budget, Blocked };
struct SendAttempt { int bytes; int error = 0; };
constexpr size_t SEND_BYTE_BUDGET = 64 * 1024;
constexpr size_t SEND_CALL_BUDGET = 8;

template<class Queue, class Send, class Accepted>
DrainResult drainWrites(Queue& queue, Send send, Accepted accepted) {
    size_t bytes = 0;
    for (size_t calls = 0; calls < SEND_CALL_BUDGET && bytes < SEND_BYTE_BUDGET && !queue.empty(); ++calls) {
        auto& pending = queue.front();
        if (pending.offset >= pending.bytes.size()) throw std::runtime_error("invalid pending write offset");
        size_t length = (std::min)(pending.bytes.size() - pending.offset, SEND_BYTE_BUDGET - bytes);
        auto result = send(pending.bytes.data() + pending.offset, length);
        if (result.bytes < 0 && result.error == WSAEWOULDBLOCK) return DrainResult::Blocked;
        if (result.bytes <= 0 || static_cast<size_t>(result.bytes) > length)
            throw std::runtime_error("socket send made no valid progress WSA=" + std::to_string(result.error));
        auto count = static_cast<size_t>(result.bytes);
        accepted(pending, count);
        pending.offset += count; bytes += count;
        if (pending.offset == pending.bytes.size()) queue.pop_front();
    }
    return queue.empty() ? DrainResult::Empty : DrainResult::Budget;
}
}
