#pragma once
#include "EndpointLayout.h"

namespace t7 {
struct EndpointAllocator {
    uint32_t object, vectorAllocate, vectorFree, stringAllocate, stringFree;
};

EndpointAllocator clientEndpointAllocator(HANDLE process, uint32_t imageBase);
// The client must be running: suspending it could leave its heap lock held.
// Bootstrap terminates the owned client if a worker fails to finish.
EndpointAddresses allocateEndpointStorage(HANDLE process, const EndpointAllocator& allocator,
                                         const Config& config, unsigned selectorParity = 0);
// Only unpublished storage belongs to the launcher; published storage belongs to the client.
void freeEndpointStorage(HANDLE process, const EndpointAllocator& allocator, const EndpointAddresses& addresses);
}
