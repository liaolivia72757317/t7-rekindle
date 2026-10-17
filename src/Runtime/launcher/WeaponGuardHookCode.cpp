#include "ClientWorld.h"
#include "WorldHookCode.h"

namespace t7r {
// Query the live main-hand blade when the native physics collection is empty.
// A 2 cm capsule is the project's contact tolerance (half the sweep strand spacing),
// not an inferred original-server shape. No bodies are inserted into the Havok world.
Bytes buildWeaponGuardHook(uint32_t address) {
    constexpr uint32_t BO = offsetof(ActorMailbox, blade), STRIDE = sizeof(ActorMailbox);
    const auto data = address + BLADE_DATA_OFFSET, input = data + 0x200;
    const auto source = data + 16, mode = data + 20, along = data + 32;
    const auto d = data + 0x300, u = d + 16, o = u + 16, v = o + 16, n = v + 16;
    const auto dd = n + 16, length = dd + 4, p = length + 4, q = p + 4;
    const auto a = q + 4, b = a + 4, cc = b + 4, disc = cc + 4, t = disc + 4;
    const auto best = t + 4, projection = best + 4, norm = projection + 4;
    const auto time = norm + 4, hitAlong = time + 4;
    constexpr uint32_t CONSTANTS_OFFSET = 0x1F00;
    const auto constants = address + WEAPON_GUARD_CODE_OFFSET + CONSTANTS_OFFSET;
    const auto zero = constants, one = zero + 4, two = one + 4, radiusSquared = two + 4;
    const auto epsilon = radiusSquared + 4, minLength = epsilon + 4, maxLength = minLength + 4;
    const auto maxDistance = maxLength + 4;
    const auto batch = address + LIVE_ACTORS_OFFSET;
    WorldHookCode c;
    auto scalar = [&](unsigned char op, uint32_t location, unsigned char reg = 0) {
        c.emit({0xF3,0x0F,op,static_cast<unsigned char>(0x05 | reg << 3)}); c.word(location);
    };
    auto target = [&](unsigned char op, uint32_t offset, unsigned char reg = 0) {
        c.emit({0xF3,0x0F,op,static_cast<unsigned char>(0x87 | reg << 3)}); c.word(offset);
    };
    auto calc = [&](uint32_t out, uint32_t left, unsigned char op, uint32_t right) {
        scalar(0x10, left); scalar(op, right); scalar(0x11, out);
    };
    auto dot = [&](uint32_t out, uint32_t left, uint32_t right) {
        c.emit({0x0F,0x57,0xC0});
        for (uint32_t axis = 0; axis < 3; ++axis) {
            scalar(0x10, left + axis * 4, 1); scalar(0x59, right + axis * 4, 1);
            c.emit({0xF3,0x0F,0x58,0xC1});
        }
        scalar(0x11, out);
    };
    auto compare = [&](uint32_t right) { c.emit({0x0F,0x2F,0x05}); c.word(right); };
    auto clampProjection = [&] {
        scalar(0x5F, zero); scalar(0x5D, one); scalar(0x11, projection);
    };
    c.emit({0x60});
    for (uint32_t axis = 0; axis < 3; ++axis)
        calc(d + axis * 4, input + 16 + axis * 4, 0x5C, input + axis * 4);
    dot(dd, d, d);
    compare(epsilon); const auto degenerate = c.branch(0x86), nonFinite = c.branch(0x8A);
    c.emit({0xBF}); c.word(batch); c.emit({0xBB}); c.word(1);
    const auto loop = c.bytes.size();
    std::vector<size_t> skip;
    c.emit({0x83,0x3F,1}); skip.push_back(c.branch(0x85));
    c.emit({0x3B,0x3D}); c.word(source); skip.push_back(c.branch(0x84));
    auto fieldEquals = [&](uint32_t field, uint32_t expected) {
        c.emit({0x81,0xBF}); c.word(BO + field); c.word(expected); skip.push_back(c.branch(0x85));
    };
    fieldEquals(offsetof(BladeMailbox, poseReady), 1);
    fieldEquals(offsetof(BladeMailbox, defenceCount), 0); // Existing native roots take precedence.
    c.emit({0x83,0xBF}); c.word(BO + offsetof(BladeMailbox, guardWeaponId)); c.emit({0});
    skip.push_back(c.branch(0x84));
    c.emit({0x8B,0x87}); c.word(BO + offsetof(BladeMailbox, bodyWorld));
    c.emit({0x3B,0x05}); c.word(data + 4); skip.push_back(c.branch(0x85));
    c.emit({0x8B,0x87}); c.word(BO + offsetof(BladeMailbox, state));
    std::vector<size_t> enabled;
    for (uint32_t state : {223u,224u,225u,226u,231u,232u,233u,234u,235u,241u,247u,253u}) {
        c.emit({0x3D}); c.word(state); enabled.push_back(c.branch(0x84));
    }
    skip.push_back(c.jump());
    for (auto branch : enabled) c.bind(branch);
    for (uint32_t axis = 0; axis < 3; ++axis) {
        target(0x10, BO + offsetof(BladeMailbox, base) + axis * 4);
        target(0x5C, offsetof(ActorMailbox, position) + axis * 4);
        scalar(0x11, n + axis * 4);
    }
    dot(norm, n, n); compare(maxDistance); skip.push_back(c.branch(0x87)); skip.push_back(c.branch(0x8A));
    for (uint32_t axis = 0; axis < 3; ++axis) {
        target(0x10, BO + offsetof(BladeMailbox, tip) + axis * 4);
        target(0x5C, BO + offsetof(BladeMailbox, base) + axis * 4); scalar(0x11, u + axis * 4);
        scalar(0x10, input + axis * 4);
        target(0x5C, BO + offsetof(BladeMailbox, base) + axis * 4); scalar(0x11, o + axis * 4);
    }
    dot(length, u, u); compare(minLength); skip.push_back(c.branch(0x82));
    compare(maxLength); skip.push_back(c.branch(0x87));
    dot(p, o, u); calc(p, p, 0x5E, length);
    dot(q, d, u); calc(q, q, 0x5E, length);
    scalar(0x10, p); clampProjection();
    for (uint32_t axis = 0; axis < 3; ++axis) {
        scalar(0x10, u + axis * 4, 1); scalar(0x59, projection, 1);
        scalar(0x10, o + axis * 4); c.emit({0xF3,0x0F,0x5C,0xC1}); scalar(0x11, n + axis * 4);
    }
    scalar(0x10, zero); scalar(0x11, best);
    dot(norm, n, n); compare(radiusSquared); const auto inside = c.branch(0x86);
    scalar(0x10, two); scalar(0x11, best);
    // Ray against finite cylinder and both end spheres; retain the first entry, not closest approach.
    for (uint32_t axis = 0; axis < 3; ++axis) {
        scalar(0x10, u + axis * 4, 1); scalar(0x59, p, 1);
        scalar(0x10, o + axis * 4); c.emit({0xF3,0x0F,0x5C,0xC1}); scalar(0x11, n + axis * 4);
        scalar(0x10, u + axis * 4, 1); scalar(0x59, q, 1);
        scalar(0x10, d + axis * 4); c.emit({0xF3,0x0F,0x5C,0xC1}); scalar(0x11, v + axis * 4);
    }
    auto root = [&](uint32_t coefficient, bool cylinder) {
        std::vector<size_t> miss;
        scalar(0x10, coefficient); compare(epsilon); miss.push_back(c.branch(0x86));
        scalar(0x10, b); scalar(0x59, b);
        scalar(0x10, coefficient, 1); scalar(0x59, cc, 1); c.emit({0xF3,0x0F,0x5C,0xC1});
        scalar(0x11, disc); compare(zero); miss.push_back(c.branch(0x82));
        c.emit({0xF3,0x0F,0x51,0xC8}); scalar(0x10, zero); scalar(0x5C, b);
        c.emit({0xF3,0x0F,0x5C,0xC1}); scalar(0x5E, coefficient); scalar(0x11, t);
        compare(zero); miss.push_back(c.branch(0x82)); compare(one); miss.push_back(c.branch(0x87));
        if (cylinder) {
            scalar(0x59, q); scalar(0x58, p);
            compare(zero); miss.push_back(c.branch(0x82)); compare(one); miss.push_back(c.branch(0x87));
        }
        scalar(0x10, best); scalar(0x5D, t); scalar(0x11, best);
        for (auto branch : miss) c.bind(branch);
    };
    dot(a, v, v); dot(b, v, n); dot(cc, n, n); calc(cc, cc, 0x5C, radiusSquared); root(a, true);
    dot(b, d, o); dot(cc, o, o); calc(cc, cc, 0x5C, radiusSquared); root(dd, false);
    for (uint32_t axis = 0; axis < 3; ++axis) calc(o + axis * 4, o + axis * 4, 0x5C, u + axis * 4);
    dot(b, d, o); dot(cc, o, o); calc(cc, cc, 0x5C, radiusSquared); root(dd, false);
    c.bind(inside);
    scalar(0x10, best); compare(one); skip.push_back(c.branch(0x87)); skip.push_back(c.branch(0x8A));
    scalar(0x59, q); scalar(0x58, p); clampProjection();
    for (uint32_t axis = 0; axis < 3; ++axis) {
        scalar(0x10, d + axis * 4); scalar(0x59, best); scalar(0x58, input + axis * 4);
        target(0x5C, BO + offsetof(BladeMailbox, base) + axis * 4);
        scalar(0x10, u + axis * 4, 1); scalar(0x59, projection, 1);
        c.emit({0xF3,0x0F,0x5C,0xC1}); scalar(0x11, n + axis * 4);
    }
    dot(norm, n, n); compare(epsilon); const auto hasNormal = c.branch(0x87);
    for (uint32_t axis = 0; axis < 3; ++axis) calc(n + axis * 4, zero, 0x5C, d + axis * 4);
    scalar(0x10, dd);
    c.bind(hasNormal); c.emit({0xF3,0x0F,0x51,0xC0}); scalar(0x11, norm);
    for (uint32_t axis = 0; axis < 3; ++axis) calc(n + axis * 4, n + axis * 4, 0x5E, norm);

    scalar(0x10, best); scalar(0x11, hitAlong);
    c.emit({0x83,0x3D}); c.word(mode); c.emit({1}); const auto moving = c.branch(0x84);
    scalar(0x10, zero); c.emit({0x83,0x3D}); c.word(mode); c.emit({0}); const auto ordered = c.branch(0x84);
    scalar(0x10, one); const auto section = c.jump();
    c.bind(moving); scalar(0x10, along, 1); scalar(0x11, hitAlong, 1);
    c.bind(ordered); c.bind(section); scalar(0x11, time);
    c.emit({0x8B,0xC3,0x05}); c.word(DEFENCE_CONTACT_OFFSET);
    c.emit({0x69,0xC0}); c.word(sizeof(BladeContactMailbox));
    c.emit({0x8B,0x35}); c.word(source);
    c.emit({0x8D,0xB4,0x06}); c.word(BO + offsetof(BladeMailbox, contacts)); // ESI output slot.
    c.emit({0x83,0x3E,2}); const auto empty = c.branch(0x85);
    c.emit({0x0F,0x2F,0x46,48}); skip.push_back(c.branch(0x87)); const auto earlier = c.branch(0x82);
    scalar(0x10, hitAlong); c.emit({0x0F,0x2F,0x46,52}); skip.push_back(c.branch(0x83));
    c.bind(earlier); c.bind(empty);
    auto output = [&](uint32_t from, unsigned char offset) {
        c.emit({0xA1}); c.word(from); c.emit({0x89,0x46,offset});
    };
    for (unsigned char axis = 0; axis < 3; ++axis) {
        output(input + axis * 4, 8 + axis * 4); output(input + 16 + axis * 4, 20 + axis * 4);
        output(n + axis * 4, 32 + axis * 4);
    }
    output(best, 44); output(time, 48); output(hitAlong, 52);
    c.emit({0xC7,0x46,4,0,0,0,0,0xC7,0x46,56,0xFF,0xFF,0xFF,0xFF,0xC7,0x46,60,0,0,0,0});
    c.emit({0x8B,0x87}); c.word(BO + offsetof(BladeMailbox, guardWeaponId)); c.emit({0x89,0x46,64});
    c.emit({0xC7,0x06,2,0,0,0});
    for (auto branch : skip) c.bind(branch);
    c.emit({0x43,0x81,0xC7}); c.word(STRIDE);
    c.emit({0x83,0xFB,static_cast<unsigned char>(MAX_WORLD_ACTORS + 1)}); c.bind(c.branch(0x82), loop);
    c.bind(degenerate); c.bind(nonFinite); c.emit({0x61,0xC3});
    if (c.bytes.size() > CONSTANTS_OFFSET) throw std::runtime_error("weapon guard hook overflow");
    c.bytes.resize(CONSTANTS_OFFSET, 0);
    for (float value : {0.f,1.f,2.f,.0004f,.00000001f,.0001f,64.f,256.f}) {
        uint32_t bits; std::memcpy(&bits, &value, sizeof(bits)); c.word(bits);
    }
    return c.bytes;
}
}
