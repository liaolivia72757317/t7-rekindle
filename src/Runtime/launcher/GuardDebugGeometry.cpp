#include "GuardDebugDraw.h"
#include <algorithm>
#include <cmath>

namespace t7r {
std::vector<GuardDebugVertex> guardDebugVertices(const WorldSnapshot& world,
                                               const std::deque<BladeContactEvent>& contacts) {
    std::vector<GuardDebugVertex> result;
    if (!world.ready) return result;
    auto line = [&](const std::array<float, 3>& a, const std::array<float, 3>& b, uint32_t color) {
        if (result.size() + 2 > GUARD_DEBUG_MAX_VERTICES) return;
        for (size_t i = 0; i < 3; ++i)
            if (!std::isfinite(a[i]) || !std::isfinite(b[i])) return;
        result.push_back({a, color}); result.push_back({b, color});
    };
    for (const auto& actor : world.actors) {
        if (!actor.ready) continue;
        uint32_t color = 0xFF808080;
        if (actor.guardWeaponId) {
            if (guardPrepareState(actor.state)) color = 0xFFFFD54F;
            else if (guardHoldState(actor.state)) color = 0xFF00D8E8;
        }
        std::array<std::array<float, 3>, 8> corners{};
        for (size_t i = 0; i < corners.size(); ++i) {
            const auto f = (i & 1 ? 1 : -1) * GUARD_HALF_EXTENTS[0];
            const auto l = (i & 2 ? 1 : -1) * GUARD_HALF_EXTENTS[1];
            corners[i] = {actor.guard.center[0] + f * actor.guard.cosYaw + l * actor.guard.sinYaw,
                          actor.guard.center[1] - f * actor.guard.sinYaw + l * actor.guard.cosYaw,
                          actor.guard.center[2] + (i & 4 ? 1 : -1) * GUARD_HALF_EXTENTS[2]};
        }
        for (size_t i = 0; i < corners.size(); ++i)
            for (size_t bit : {1u, 2u, 4u}) if (!(i & bit)) line(corners[i], corners[i | bit], color);
        const auto& blade = actor.blade;
        if (!blade.ready) continue;
        line(blade.base, blade.tip, 0xFFFFFFFF);
        if (!blade.sweepSteps) continue;
        line(blade.sweepBase, blade.sweepTip, 0xFF9A7DFF);
        for (uint32_t step = 0; step <= blade.sweepSteps; ++step) {
            const float along = static_cast<float>(step) / blade.sweepSteps;
            std::array<float, 3> from{}, to{};
            for (size_t axis = 0; axis < 3; ++axis) {
                from[axis] = blade.sweepBase[axis] + along * (blade.sweepTip[axis] - blade.sweepBase[axis]);
                to[axis] = blade.base[axis] + along * (blade.tip[axis] - blade.base[axis]);
            }
            line(from, to, 0xFF9A7DFF);
        }
    }
    for (const auto& event : contacts) {
        if (event.instanceStartedAt != world.instanceStartedAt || event.occurredAt > world.sampledAt
            || world.sampledAt - event.occurredAt > 300) continue;
        const auto point = event.hit.contact.position;
        for (size_t axis = 0; axis < 3; ++axis) {
            auto a = point, b = point;
            a[axis] -= .035f; b[axis] += .035f;
            line(a, b, event.hit.guardVolume ? 0xFF00D8E8 : 0xFFFFFFFF);
        }
    }
    return result;
}
}
