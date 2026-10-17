#pragma once
#include "PythonObjects.h"
#include "../../../src/Runtime/core/WorldSnapshot.h"

namespace t7r {
// Owned and used exclusively by the embedded interpreter's business thread.
class PythonContext {
public:
    explicit PythonContext(const Config& config);
    python::Object make(uint64_t now, const std::vector<uint64_t>& connections,
                        const PublishedWorld* world, bool includeContacts);
    bool rebuilt() const { return rebuilt_; }
    uint64_t version() const { return version_; }
    const std::string& observation() const { return observation_; }
private:
    python::Object config_, world_, worldWithContacts_;
    uint64_t version_ = 0, epoch_ = 0;
    bool rebuilt_ = false;
    std::array<uint64_t, 5> observed_{};
    std::string observation_;
};
}
