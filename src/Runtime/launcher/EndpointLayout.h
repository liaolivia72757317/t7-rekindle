#pragma once
#include "../core/Common.h"
#include <array>
#include <cstddef>

namespace t7 {
struct EndpointString { char buffer[16]; uint32_t length, capacity; };
struct EndpointRecord { EndpointString address; uint32_t port; EndpointString descriptor; };
struct EndpointCandidate { uint32_t score; EndpointString address; };
struct EndpointAddresses {
    uint32_t records = 0, group = 0, candidates = 0;
    uint32_t descriptors[4]{};
};
struct EndpointLayout {
    EndpointRecord records[4];
    uint32_t group[3];
    char descriptors[4][32];
    EndpointCandidate candidates[2];
};
static_assert(sizeof(EndpointString) == 24 && sizeof(EndpointRecord) == 52, "x86 endpoint ABI mismatch");
static_assert(sizeof(EndpointCandidate) == 28 && offsetof(EndpointLayout, candidates) == 348, "candidate ABI mismatch");
static_assert(offsetof(EndpointLayout, group) == 208 && sizeof(EndpointLayout) == 404, "endpoint page layout mismatch");
static_assert(sizeof(EndpointAddresses) == 28, "endpoint allocation result ABI mismatch");
inline constexpr std::array<uint32_t, 7> ENDPOINT_BLOCK_SIZES{208, 12, 56, 32, 32, 32, 32};

inline std::array<uint32_t, 7> endpointAllocationSizes(const Config& config, unsigned selectorParity = 0) {
    validateConfig(config);
    if (selectorParity > 1) throw std::runtime_error("invalid endpoint selector parity");
    auto sizes = ENDPOINT_BLOCK_SIZES;
    const auto descriptor = config.advertisedAddress + ":" + std::to_string(config.ports[1]);
    for (size_t i = 0; i < 4; ++i) {
        if (descriptor.size() <= 15) sizes[i + 3] = 0;
        else if (descriptor.size() >= sizes[i + 3]) throw std::runtime_error("endpoint descriptor exceeds backing storage");
    }
    return sizes;
}

inline EndpointLayout makeEndpointLayout(const Config& config, const EndpointAddresses& addresses, unsigned selectorParity = 0) {
    const auto sizes = endpointAllocationSizes(config, selectorParity);
    std::array<uint32_t, 7> pointers{};
    memcpy(pointers.data(), &addresses, sizeof(addresses));
    for (size_t i = 0; i < sizes.size(); ++i) {
        if (sizes[i] ? (!pointers[i] || pointers[i] > UINT32_MAX - sizes[i]) : pointers[i] != 0)
            throw std::runtime_error("invalid endpoint allocation address");
    }
    EndpointLayout layout{};
    layout.group[0] = addresses.records;
    layout.group[1] = layout.group[2] = addresses.records + sizeof(layout.records);
    for (int i = 0; i < 4; ++i) {
        auto& record = layout.records[i];
        const auto& address = config.advertisedAddress;
        if (address.size() > 15) throw std::runtime_error("IPv4 address exceeds inline storage");
        memcpy(record.address.buffer, address.c_str(), address.size() + 1);
        record.address.length = static_cast<uint32_t>(address.size()); record.address.capacity = 15;
        // Every startup/retry selection opens the logic channel, regardless of selector parity.
        record.port = config.ports[1];
        auto descriptor = address + ":" + std::to_string(record.port);
        record.descriptor.length = static_cast<uint32_t>(descriptor.size());
        if (descriptor.size() <= 15) {
            record.descriptor.capacity = 15;
            memcpy(record.descriptor.buffer, descriptor.c_str(), descriptor.size() + 1);
        } else {
            if (descriptor.size() >= sizeof(layout.descriptors[i])) throw std::runtime_error("endpoint descriptor exceeds backing storage");
            const uint32_t pointer = addresses.descriptors[i];
            memcpy(record.descriptor.buffer, &pointer, sizeof(pointer));
            record.descriptor.capacity = sizeof(layout.descriptors[i]) - 1;
            memcpy(layout.descriptors[i], descriptor.c_str(), descriptor.size() + 1);
        }
    }
    // Fixed candidate order for one configured host, not a measured latency result.
    // The client requires two candidates even though attempts 0/1 use candidates[0].
    for (auto& candidate : layout.candidates) candidate.address = layout.records[0].address;
    return layout;
}
}
