#include "ClientWorld.h"
#include "WorldHookCode.h"

namespace t7r {
// Query-only character-relative OBB. Native shield roots remain in the Havok collector.
Bytes buildWeaponGuardHook(uint32_t address) {
    constexpr uint32_t BO = offsetof(ActorMailbox, blade), STRIDE = sizeof(ActorMailbox);
    constexpr uint32_t GO = BO + offsetof(BladeMailbox, guard), CONSTANTS_OFFSET = 0x1F00;
    const auto data = address + BLADE_DATA_OFFSET, input = data + 0x200;
    const auto source = data + 16, mode = data + 20, along = data + 32;
    const auto worldD = data + 0x300, worldO = worldD + 16, d = worldO + 16, o = d + 16;
    const auto n = o + 16, nearT = n + 16, farT = nearT + 4, a = farT + 4, b = a + 4;
    const auto sign = b + 4, norm = sign + 4, time = norm + 4, hitAlong = time + 4;
    const auto constants = address + WEAPON_GUARD_CODE_OFFSET + CONSTANTS_OFFSET;
    const auto zero = constants, one = zero + 4, minusOne = one + 4, epsilon = minusOne + 4;
    const auto half = epsilon + 4;
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
    auto compare = [&](uint32_t right) { c.emit({0x0F,0x2F,0x05}); c.word(right); };
    auto localVector = [&](uint32_t out, uint32_t in) {
        scalar(0x10, in); target(0x59, GO + offsetof(GuardGeometry, cosYaw));
        scalar(0x10, in + 4, 1); target(0x59, GO + offsetof(GuardGeometry, sinYaw), 1);
        c.emit({0xF3,0x0F,0x5C,0xC1}); scalar(0x11, out);
        scalar(0x10, in); target(0x59, GO + offsetof(GuardGeometry, sinYaw));
        scalar(0x10, in + 4, 1); target(0x59, GO + offsetof(GuardGeometry, cosYaw), 1);
        c.emit({0xF3,0x0F,0x58,0xC1}); scalar(0x11, out + 4);
        scalar(0x10, in + 8); scalar(0x11, out + 8);
    };
    c.emit({0x60,0x0F,0x57,0xD2});
    for (uint32_t axis = 0; axis < 3; ++axis) {
        calc(worldD + axis * 4, input + 16 + axis * 4, 0x5C, input + axis * 4);
        c.emit({0xF3,0x0F,0x59,0xC0,0xF3,0x0F,0x58,0xD0});
    }
    c.emit({0x0F,0x28,0xC2}); compare(epsilon);
    const auto degenerate = c.branch(0x86), nonFinite = c.branch(0x8A);
    c.emit({0xF3,0x0F,0x51,0xC0}); scalar(0x11, norm);
    c.emit({0xBF}); c.word(address + LIVE_ACTORS_OFFSET); c.emit({0xBB}); c.word(1);
    const auto loop = c.bytes.size();
    std::vector<size_t> skip;
    c.emit({0x83,0x3F,1}); skip.push_back(c.branch(0x85));
    c.emit({0x3B,0x3D}); c.word(source); skip.push_back(c.branch(0x84));
    c.emit({0x83,0xBF}); c.word(BO + offsetof(BladeMailbox, guardWeaponId)); c.emit({0}); skip.push_back(c.branch(0x84));
    c.emit({0x8B,0x87}); c.word(BO + offsetof(BladeMailbox, bodyWorld));
    c.emit({0x3B,0x05}); c.word(data + 4); skip.push_back(c.branch(0x85));
    c.emit({0x8B,0x87}); c.word(BO + offsetof(BladeMailbox, state));
    std::vector<size_t> enabled;
    for (uint32_t state : GUARD_VOLUME_STATES) {
        c.emit({0x3D}); c.word(state); enabled.push_back(c.branch(0x84));
    }
    skip.push_back(c.jump());
    for (auto branch : enabled) c.bind(branch);
    for (uint32_t axis = 0; axis < 3; ++axis) {
        scalar(0x10, input + axis * 4); target(0x5C, GO + axis * 4); scalar(0x11, worldO + axis * 4);
        calc(n + axis * 4, zero, 0x5C, worldD + axis * 4); scalar(0x5E, norm); scalar(0x11, n + axis * 4);
    }
    localVector(o, worldO); localVector(d, worldD);
    scalar(0x10, zero); scalar(0x11, nearT); scalar(0x10, one); scalar(0x11, farT);
    // Clip the temporal strand at activation; an earlier invalid entry must not hide a valid one.
    c.emit({0x8B,0x35}); c.word(source);
    c.emit({0xA1}); c.word(data); c.emit({0x2B,0x87}); c.word(BO + offsetof(BladeMailbox, phaseTick));
    c.emit({0x85,0xC0}); skip.push_back(c.branch(0x88));
    c.emit({0x8B,0x8E}); c.word(BO + offsetof(BladeMailbox, spanMs));
    c.emit({0x3B,0xC1}); const auto activeThroughout = c.branch(0x83);
    c.emit({0x83,0x3D}); c.word(mode); c.emit({0}); skip.push_back(c.branch(0x84));
    c.emit({0x83,0x3D}); c.word(mode); c.emit({1}); const auto currentSection = c.branch(0x85);
    c.emit({0xF3,0x0F,0x2A,0xC9,0x2B,0xC8,0xF3,0x0F,0x2A,0xC1,0xF3,0x0F,0x5E,0xC1}); scalar(0x11, nearT);
    c.bind(currentSection); c.bind(activeThroughout);
    for (uint32_t axis = 0; axis < 3; ++axis) {
        scalar(0x10, d + axis * 4); scalar(0x59, d + axis * 4); compare(epsilon);
        const auto nonParallel = c.branch(0x87); skip.push_back(c.branch(0x8A));
        scalar(0x10, o + axis * 4); compare(half + axis * 4); skip.push_back(c.branch(0x87));
        scalar(0x10, zero); scalar(0x5C, o + axis * 4); compare(half + axis * 4); skip.push_back(c.branch(0x87));
        const auto parallelDone = c.jump();
        c.bind(nonParallel);
        scalar(0x10, zero); scalar(0x5C, half + axis * 4); scalar(0x5C, o + axis * 4); scalar(0x5E, d + axis * 4); scalar(0x11, a);
        scalar(0x10, half + axis * 4); scalar(0x5C, o + axis * 4); scalar(0x5E, d + axis * 4); scalar(0x11, b);
        scalar(0x10, minusOne); scalar(0x11, sign);
        scalar(0x10, a); compare(b); const auto ordered = c.branch(0x86);
        scalar(0x10, b, 1); scalar(0x11, b); scalar(0x11, a, 1); scalar(0x10, one); scalar(0x11, sign);
        c.bind(ordered);
        scalar(0x10, a); compare(nearT); const auto earlier = c.branch(0x86);
        scalar(0x11, nearT);
        for (uint32_t i = 0; i < 3; ++i) { scalar(0x10, zero); scalar(0x11, n + i * 4); }
        if (axis == 2) { scalar(0x10, sign); scalar(0x11, n + 8); }
        else {
            target(0x10, GO + (axis == 0 ? offsetof(GuardGeometry, cosYaw) : offsetof(GuardGeometry, sinYaw)));
            scalar(0x59, sign); scalar(0x11, n);
            target(0x10, GO + (axis == 0 ? offsetof(GuardGeometry, sinYaw) : offsetof(GuardGeometry, cosYaw)));
            scalar(0x59, sign); if (axis == 0) scalar(0x59, minusOne); scalar(0x11, n + 4);
        }
        c.bind(earlier);
        scalar(0x10, farT); scalar(0x5D, b); scalar(0x11, farT); compare(nearT); skip.push_back(c.branch(0x82));
        c.bind(parallelDone);
    }
    scalar(0x10, nearT); scalar(0x11, hitAlong);
    c.emit({0x83,0x3D}); c.word(mode); c.emit({1}); const auto moving = c.branch(0x84);
    scalar(0x10, zero); c.emit({0x83,0x3D}); c.word(mode); c.emit({0}); const auto ordered = c.branch(0x84);
    scalar(0x10, one); const auto section = c.jump();
    c.bind(moving); scalar(0x10, along, 1); scalar(0x11, hitAlong, 1);
    c.bind(ordered); c.bind(section); scalar(0x11, time);
    c.emit({0x8B,0xC3,0x05}); c.word(DEFENCE_CONTACT_OFFSET);
    c.emit({0x69,0xC0}); c.word(sizeof(BladeContactMailbox));
    c.emit({0x8B,0x35}); c.word(source);
    c.emit({0x8D,0xB4,0x06}); c.word(BO + offsetof(BladeMailbox, contacts));
    c.emit({0x83,0x3E,2}); const auto empty = c.branch(0x85);
    c.emit({0x0F,0x2F,0x46,48}); skip.push_back(c.branch(0x87)); const auto earlier = c.branch(0x82);
    scalar(0x10, hitAlong); c.emit({0x0F,0x2F,0x46,52}); skip.push_back(c.branch(0x83));
    c.bind(earlier); c.bind(empty);
    auto output = [&](uint32_t from, unsigned char offset) { c.emit({0xA1}); c.word(from); c.emit({0x89,0x46,offset}); };
    for (unsigned char axis = 0; axis < 3; ++axis) {
        output(input + axis * 4, 8 + axis * 4); output(input + 16 + axis * 4, 20 + axis * 4); output(n + axis * 4, 32 + axis * 4);
    }
    output(nearT, 44); output(time, 48); output(hitAlong, 52);
    c.emit({0xC7,0x46,4,0,0,0,0,0xC7,0x46,56,0xFF,0xFF,0xFF,0xFF,0xC7,0x46,60,0,0,0,0});
    c.emit({0x8B,0x87}); c.word(BO + offsetof(BladeMailbox, guardWeaponId)); c.emit({0x89,0x46,64});
    c.emit({0xC7,0x46,68,1,0,0,0,0xC7,0x06,2,0,0,0});
    for (auto branch : skip) c.bind(branch);
    c.emit({0x43,0x81,0xC7}); c.word(STRIDE);
    c.emit({0x83,0xFB,static_cast<unsigned char>(MAX_WORLD_ACTORS + 1)}); c.bind(c.branch(0x82), loop);
    c.bind(degenerate); c.bind(nonFinite); c.emit({0x61,0xC3});
    if (c.bytes.size() > CONSTANTS_OFFSET) throw std::runtime_error("guard volume hook overflow");
    c.bytes.resize(CONSTANTS_OFFSET, 0);
    for (float value : {0.f,1.f,-1.f,.00000001f,GUARD_HALF_EXTENTS[0],GUARD_HALF_EXTENTS[1],GUARD_HALF_EXTENTS[2]}) {
        uint32_t bits; std::memcpy(&bits, &value, sizeof(bits)); c.word(bits);
    }
    return c.bytes;
}
}
