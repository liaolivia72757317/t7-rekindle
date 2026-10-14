#pragma once
#include "../core/WorldSnapshot.h"

namespace t7 {
// Retain a bounded pair of poses to catch a strike crossing a held guard blade.
class BladeParryContacts final {
public:
    void append(WorldSnapshot& world);
    void clear() { previous_ = {}; }
private:
    WorldSnapshot previous_;
};
}
