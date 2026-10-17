#include "ClientWorld.h"
#include "WorldHookCode.h"
#include "WeaponTrailHookCode.h"

namespace t7r {
namespace {
constexpr uint32_t COLLECTOR_OFFSET = 0x1800, RAY_OFFSET = 0x1400, CONSTANTS_OFFSET = 0x2400;
constexpr uint32_t BO = offsetof(ActorMailbox, blade), CONTACTS = offsetof(BladeMailbox, contacts);
constexpr uint32_t STRIDE = sizeof(ActorMailbox), HIT_STRIDE = sizeof(BladeContactMailbox);
}

Bytes buildBladeSweepHook(uint32_t address, uint32_t imageBase) {
    const auto code = address + BLADE_SWEEP_CODE_OFFSET, data = address + BLADE_DATA_OFFSET;
    const auto world = data + 4, sceneRoot = data + 8, sceneShape = data + 12, source = data + 16;
    const auto mode = data + 20, steps = data + 24, index = data + 28, along = data + 32;
    const auto stepFloat = data + 36, candidateTime = data + 40, candidateAlong = data + 44;
    const auto cdBody = data + 48, rayCount = data + 52;
    const auto candidateBody = data + 56, candidateRegion = data + 60;
    const auto candidateDefence = data + 64;
    const auto collector = data + 0x100, input = data + 0x200, material = data + 0x240;
    const auto constants = code + CONSTANTS_OFFSET, one = constants, epsilon = constants + 4;
    const auto minLength = constants + 8, maxLength = constants + 12, density = constants + 16;
    const auto maxTravel = constants + 20, maxDistance = constants + 24, table = constants + 36;
    const auto groundNormalZ = constants + 28, groundPlaneTolerance = constants + 32;
    const auto batch = address + LIVE_ACTORS_OFFSET, end = batch + static_cast<uint32_t>(MAX_WORLD_ACTORS) * STRIDE;
    WorldHookCode c;
    auto load = [&](unsigned char reg, uint32_t offset) { c.emit({0x8B,static_cast<unsigned char>(0x85 | reg << 3)}); c.word(offset); };
    auto store = [&](uint32_t offset, uint32_t value) { c.emit({0xC7,0x85}); c.word(offset); c.word(value); };
    auto save = [&](uint32_t offset) { c.emit({0x89,0x85}); c.word(offset); };
    auto copy = [&](uint32_t from, uint32_t to) { c.emit({0xA1}); c.word(from); c.emit({0xA3}); c.word(to); };
    auto scalar = [&](unsigned char instruction, unsigned char reg, uint32_t offset) {
        c.emit({0xF3,0x0F,instruction,static_cast<unsigned char>(0x85 | reg << 3)}); c.word(offset);
    };
    auto compare = [&](uint32_t value) { c.emit({0x0F,0x2F,0x05}); c.word(value); };
    auto squaredDistance = [&](uint32_t first, uint32_t second, bool actor = false) {
        c.emit({0x0F,0x57,0xC0});
        for (uint32_t axis = 0; axis < 3; ++axis) {
            scalar(0x10, 1, first + axis * 4);
            c.emit({0xF3,0x0F,0x5C,static_cast<unsigned char>(actor ? 0x8F : 0x8D)}); c.word(second + axis * 4);
            c.emit({0xF3,0x0F,0x59,0xC9,0xF3,0x0F,0x58,0xC1});
        }
    };
    c.emit({0x60,0xBF}); c.word(batch);
    c.store(rayCount, 0);
    const auto clearLoop = c.bytes.size();
    c.emit({0x8D,0xAF}); c.word(BO);
    store(offsetof(BladeMailbox, traceReady), 0); store(offsetof(BladeMailbox, spanMs), 0);
    for (uint32_t i = 0; i < MAX_BLADE_CONTACTS; ++i) store(CONTACTS + i * HIT_STRIDE, 0);
    c.emit({0x83,0x3F,1}); const auto alive = c.branch(0x84);
    store(offsetof(BladeMailbox, poseReady), 0); store(offsetof(BladeMailbox, previousWeapon), 0);
    c.bind(alive); c.emit({0x81,0xC7}); c.word(STRIDE); c.emit({0x81,0xFF}); c.word(end);
    c.bind(c.branch(0x82), clearLoop);

    std::vector<size_t> unavailable;
    auto nonzero = [&](unsigned char modrm) { c.emit({0x85,modrm}); unavailable.push_back(c.branch(0x84)); };
    auto pointer = [&](unsigned char offset) { c.emit({0x8B,0x40,offset}); nonzero(0xC0); };
    auto vtable = [&](uint32_t rva) {
        c.emit({0x81,0x38}); c.word(imageBase + rva); unavailable.push_back(c.branch(0x85));
    };
    c.emit({0xA1}); c.word(imageBase + 0x2E24AF8); nonzero(0xC0); pointer(0x1C);
    c.emit({0x83,0x78,0x7C,0}); unavailable.push_back(c.branch(0x84));
    c.emit({0x8B,0xC8,0x68}); c.word(imageBase + 0x177E884); c.call(imageBase + 0x26A1C0);
    nonzero(0xC0); vtable(0x177E7EC); pointer(0x1C); vtable(0x177C1DC);
    pointer(0x38); vtable(0x1764C58); pointer(0x1C); vtable(0x1764B68);
    c.emit({0x83,0x78,0x20,14}); unavailable.push_back(c.branch(0x85)); pointer(0x3C);
    c.emit({0x8B,0x70,8}); nonzero(0xF6); c.emit({0x89,0x35}); c.word(world);
    c.emit({0x8D,0x50,0x10,0x89,0x15}); c.word(sceneRoot);
    pointer(0x10); c.emit({0x80,0x78,8,16}); unavailable.push_back(c.branch(0x85));
    c.emit({0xA3}); c.word(sceneShape);
    c.emit({0xA1}); c.word(imageBase + 0x2D379A8); nonzero(0xC0); pointer(0x10); pointer(0x10);
    c.emit({0x3B,0xC6}); unavailable.push_back(c.branch(0x85)); vtable(0x1ACFF4C);
    c.emit({0x83,0x78,0x74,0}); unavailable.push_back(c.branch(0x84));
    c.store(collector, table); c.store(collector + 4, 0x3F800000);
    for (uint32_t offset : {12u,28u,32u,36u,40u}) c.store(input + offset, 0);
    // Query layer zero admits scene, hurt and equipped weapon bodies; only exact roots are retained.
    c.emit({0xBF}); c.word(batch);
    const auto actorLoop = c.bytes.size();
    c.emit({0x8D,0xAF}); c.word(BO);
    std::vector<size_t> invalid;
    c.emit({0x83,0x3F,1}); invalid.push_back(c.branch(0x85));
    c.emit({0x83,0xBD}); c.word(offsetof(BladeMailbox, poseReady)); c.emit({1}); invalid.push_back(c.branch(0x85));
    load(0, offsetof(BladeMailbox, state));
    std::vector<size_t> striking;
    for (uint32_t state : {238u, 244u, 250u, 256u, 439u}) {
        c.emit({0x3D}); c.word(state); striking.push_back(c.branch(0x84));
    }
    invalid.push_back(c.jump());
    for (auto branch : striking) c.bind(branch);
    squaredDistance(offsetof(BladeMailbox, base), offsetof(ActorMailbox, position), true);
    compare(maxDistance); invalid.push_back(c.branch(0x87)); invalid.push_back(c.branch(0x8A));
    squaredDistance(offsetof(BladeMailbox, base), offsetof(BladeMailbox, tip));
    c.emit({0xF3,0x0F,0x51,0xC0}); compare(minLength); invalid.push_back(c.branch(0x82));
    compare(maxLength); invalid.push_back(c.branch(0x87));
    c.emit({0xF3,0x0F,0x59,0x05}); c.word(density);
    c.emit({0xF3,0x0F,0x2C,0xD8,0x43,0x89,0x1D}); c.word(steps);
    c.emit({0xF3,0x0F,0x2A,0xC3,0xF3,0x0F,0x11,0x05}); c.word(stepFloat);
    c.emit({0x89,0x3D}); c.word(source);
    std::vector<size_t> reset;
    load(0, offsetof(BladeMailbox, weaponEntity));
    c.emit({0x3B,0x85}); c.word(offsetof(BladeMailbox, previousWeapon)); reset.push_back(c.branch(0x85));
    for (const auto& fields : std::array<std::array<uint32_t, 2>, 2>{{
        {offsetof(BladeMailbox, sequence), offsetof(BladeMailbox, previousSequence)},
        {offsetof(BladeMailbox, actorEntity), offsetof(BladeMailbox, previousEntity)}
    }}) {
        load(0, fields[0]); c.emit({0x3B,0x85}); c.word(fields[1]); reset.push_back(c.branch(0x85));
    }
    for (uint32_t offset : {0u,4u}) {
        c.emit({0xA1}); c.word(address + FRAME_DATA_OFFSET + 16 + offset);
        c.emit({0x3B,0x85}); c.word(offsetof(BladeMailbox, previousEpoch) + offset); reset.push_back(c.branch(0x85));
    }
    c.emit({0xA1}); c.word(data); c.emit({0x2B,0x85}); c.word(offsetof(BladeMailbox, previousTick));
    // Multiple rendered frames can share one GetTickCount tick; their geometry still sweeps continuously.
    c.emit({0x83,0xF8,100}); reset.push_back(c.branch(0x87));
    save(offsetof(BladeMailbox, spanMs));
    for (const auto& points : std::array<std::array<uint32_t,2>,2>{{
        {offsetof(BladeMailbox, base), offsetof(BladeMailbox, previousBase)},
        {offsetof(BladeMailbox, tip), offsetof(BladeMailbox, previousTip)}
    }}) {
        squaredDistance(points[0], points[1]); compare(maxTravel);
        reset.push_back(c.branch(0x87)); reset.push_back(c.branch(0x8A));
    }
    load(0, offsetof(BladeMailbox, spanMs)); c.emit({0x85,0xC0}); const auto zeroSpan = c.branch(0x84);
    c.emit({0xF3,0x0F,0x2A,0xD0,0xF3,0x0F,0x10,0x1D}); c.word(constants + 40);
    c.emit({0xF3,0x0F,0x5E,0xDA});
    for (uint32_t i = 0; i < 6; ++i) {
        scalar(0x10, 0, offsetof(BladeMailbox, base) + i * 4);
        scalar(0x5C, 0, offsetof(BladeMailbox, previousBase) + i * 4);
        c.emit({0xF3,0x0F,0x59,0xC3}); scalar(0x11, 0, offsetof(BladeMailbox, baseVelocity) + i * 4);
    }
    c.bind(zeroSpan);
    auto section = [&](uint32_t first, uint32_t second, uint32_t rayMode) {
        c.store(mode, rayMode);
        for (uint32_t axis = 0; axis < 3; ++axis) {
            load(0, first + axis * 4); c.emit({0xA3}); c.word(input + axis * 4);
            load(0, second + axis * 4); c.emit({0xA3}); c.word(input + 16 + axis * 4);
        }
        c.call(code + RAY_OFFSET);
    };
    section(offsetof(BladeMailbox, previousBase), offsetof(BladeMailbox, previousTip), 0);
    c.store(mode, 1); c.store(index, 0);
    const auto strandLoop = c.bytes.size();
    c.emit({0xF3,0x0F,0x2A,0x05}); c.word(index);
    c.emit({0xF3,0x0F,0x5E,0x05}); c.word(stepFloat);
    c.emit({0xF3,0x0F,0x11,0x05}); c.word(along);
    for (const auto& points : std::array<std::array<uint32_t,3>,2>{{
        {offsetof(BladeMailbox, previousBase), offsetof(BladeMailbox, previousTip), input},
        {offsetof(BladeMailbox, base), offsetof(BladeMailbox, tip), input + 16}
    }}) {
        for (uint32_t axis = 0; axis < 3; ++axis) {
            scalar(0x10, 0, points[1] + axis * 4); scalar(0x5C, 0, points[0] + axis * 4);
            c.emit({0xF3,0x0F,0x59,0x05}); c.word(along); scalar(0x58, 0, points[0] + axis * 4);
            c.emit({0xF3,0x0F,0x11,0x05}); c.word(points[2] + axis * 4);
        }
    }
    c.call(code + RAY_OFFSET);
    c.emit({0xFF,0x05}); c.word(index); c.emit({0xA1}); c.word(index);
    c.emit({0x3B,0x05}); c.word(steps); c.bind(c.branch(0x86), strandLoop);
    const auto continuous = c.jump();
    for (auto branch : reset) c.bind(branch);
    store(offsetof(BladeMailbox, spanMs), 0);
    c.bind(continuous);
    section(offsetof(BladeMailbox, base), offsetof(BladeMailbox, tip), 2);
    store(offsetof(BladeMailbox, traceReady), 1);
    load(0, offsetof(BladeMailbox, weaponEntity)); save(offsetof(BladeMailbox, previousWeapon));
    load(0, offsetof(BladeMailbox, sequence)); save(offsetof(BladeMailbox, previousSequence));
    load(0, offsetof(BladeMailbox, actorEntity)); save(offsetof(BladeMailbox, previousEntity));
    c.emit({0xA1}); c.word(data); save(offsetof(BladeMailbox, previousTick));
    for (uint32_t offset : {0u,4u}) {
        c.emit({0xA1}); c.word(address + FRAME_DATA_OFFSET + 16 + offset); save(offsetof(BladeMailbox, previousEpoch) + offset);
    }
    for (uint32_t i = 0; i < 6; ++i) {
        load(0, offsetof(BladeMailbox, base) + i * 4); save(offsetof(BladeMailbox, previousBase) + i * 4);
    }
    const auto next = c.jump();
    for (auto branch : invalid) c.bind(branch);
    store(offsetof(BladeMailbox, previousWeapon), 0);
    c.bind(next); c.emit({0x81,0xC7}); c.word(STRIDE); c.emit({0x81,0xFF}); c.word(end);
    c.bind(c.branch(0x82), actorLoop);
    const auto finished = c.jump();
    for (auto branch : unavailable) c.bind(branch);
    for (uint32_t i = 0; i < MAX_WORLD_ACTORS; ++i)
        c.store(batch + i * STRIDE + BO + offsetof(BladeMailbox, previousWeapon), 0);
    c.bind(finished); c.emit({0x61,0xC3});
    if (c.bytes.size() > RAY_OFFSET) throw std::runtime_error("blade sweep loop overflow");

    c.bytes.resize(RAY_OFFSET, 0xCC);
    c.emit({0x60,0x0F,0x57,0xC0});
    for (uint32_t axis = 0; axis < 3; ++axis) {
        c.emit({0xF3,0x0F,0x10,0x0D}); c.word(input + axis * 4);
        c.emit({0xF3,0x0F,0x5C,0x0D}); c.word(input + 16 + axis * 4);
        c.emit({0xF3,0x0F,0x59,0xC9,0xF3,0x0F,0x58,0xC1});
    }
    compare(epsilon); const auto degenerate = c.branch(0x86), notFinite = c.branch(0x8A);
    c.emit({0xFF,0x05}); c.word(rayCount);
    c.emit({0x68}); c.word(collector); c.emit({0x68}); c.word(input);
    c.emit({0x8B,0x0D}); c.word(world); c.call(imageBase + 0x10FA3B0);
    c.call(address + WEAPON_GUARD_CODE_OFFSET);
    c.bind(degenerate); c.bind(notFinite); c.emit({0x61,0xC3});
    if (c.bytes.size() > COLLECTOR_OFFSET) throw std::runtime_error("blade ray hook overflow");

    c.bytes.resize(COLLECTOR_OFFSET, 0xCC);
    c.emit({0x9C,0x60,0x8B,0x54,0x24,0x28,0x89,0x15}); c.word(cdBody);
    c.emit({0xB9}); c.word(32);
    const auto parents = c.bytes.size();
    c.emit({0x8B,0x42,0x0C,0x85,0xC0}); const auto root = c.branch(0x84);
    c.emit({0x8B,0xD0,0x49}); c.bind(c.branch(0x85), parents);
    std::vector<size_t> ignored{c.jump()};
    c.bind(root); c.store(candidateDefence, 0);
    c.emit({0x33,0xC0,0x3B,0x15}); c.word(sceneRoot); const auto scene = c.branch(0x84);
    c.emit({0xBF}); c.word(batch); c.emit({0xB8}); c.word(1);
    const auto targetLoop = c.bytes.size();
    std::vector<size_t> skipTarget;
    c.emit({0x83,0x3F,1}); skipTarget.push_back(c.branch(0x85));
    c.emit({0x3B,0x3D}); c.word(source); skipTarget.push_back(c.branch(0x84));
    c.emit({0x8B,0x9F}); c.word(BO + offsetof(BladeMailbox, defenceCount));
    c.emit({0x85,0xDB}); const auto noDefence = c.branch(0x84);
    c.emit({0x8D,0x8F}); c.word(BO + offsetof(BladeMailbox, defenceRoots));
    const auto defenceLoop = c.bytes.size();
    c.emit({0x3B,0x11}); const auto otherRoot = c.branch(0x85);
    c.emit({0x56,0x8B,0xB1}); c.word(offsetof(BladeMailbox, defenceWorlds) - offsetof(BladeMailbox, defenceRoots));
    c.emit({0x3B,0x35}); c.word(world); c.emit({0x5E}); const auto defenceRoot = c.branch(0x84);
    c.bind(otherRoot); c.emit({0x83,0xC1,4,0x4B}); c.bind(c.branch(0x85), defenceLoop);
    c.bind(noDefence);
    c.emit({0x8B,0x8F}); c.word(BO + offsetof(BladeMailbox, bodyWorld));
    c.emit({0x3B,0x0D}); c.word(world); skipTarget.push_back(c.branch(0x85));
    c.emit({0x8B,0x9F}); c.word(BO + offsetof(BladeMailbox, bodyCount));
    c.emit({0x85,0xDB}); skipTarget.push_back(c.branch(0x84));
    c.emit({0x8D,0x8F}); c.word(BO + offsetof(BladeMailbox, roots));
    const auto rootLoop = c.bytes.size();
    c.emit({0x3B,0x11}); const auto actorRoot = c.branch(0x84);
    c.emit({0x83,0xC1,4,0x4B}); c.bind(c.branch(0x85), rootLoop);
    for (auto branch : skipTarget) c.bind(branch);
    c.emit({0x40,0x81,0xC7}); c.word(STRIDE); c.emit({0x83,0xF8,static_cast<uint8_t>(MAX_WORLD_ACTORS + 1)}); c.bind(c.branch(0x82), targetLoop);
    ignored.push_back(c.jump());
    c.bind(actorRoot);
    c.emit({0x8B,0x99}); c.word(offsetof(BladeMailbox, bodyIndices) - offsetof(BladeMailbox, roots));
    c.emit({0x89,0x1D}); c.word(candidateBody);
    c.emit({0x8B,0x99}); c.word(offsetof(BladeMailbox, regions) - offsetof(BladeMailbox, roots));
    c.emit({0x89,0x1D}); c.word(candidateRegion);
    const auto classified = c.jump();
    c.bind(defenceRoot);
    c.emit({0x8B,0x99}); c.word(offsetof(BladeMailbox, defenceWeapons) - offsetof(BladeMailbox, defenceRoots));
    c.emit({0x89,0x1D}); c.word(candidateDefence);
    c.emit({0x83,0xC0,static_cast<unsigned char>(DEFENCE_CONTACT_OFFSET)});
    c.store(candidateBody, UINT32_MAX); c.store(candidateRegion, 0);
    const auto defenceClassified = c.jump();
    c.bind(scene); c.store(candidateBody, UINT32_MAX); c.store(candidateRegion, 0);
    c.bind(classified); c.bind(defenceClassified);
    c.emit({0x8B,0x3D}); c.word(source);
    c.emit({0x69,0xC0}); c.word(HIT_STRIDE); c.emit({0x8D,0xBC,0x07}); c.word(BO + CONTACTS);
    c.emit({0x8B,0x74,0x24,0x2C,0xF3,0x0F,0x10,0x46,0x10,0x0F,0x57,0xC9,0x0F,0x2F,0xC1});
    ignored.push_back(c.branch(0x82)); compare(one); ignored.push_back(c.branch(0x87));
    c.emit({0x0F,0x28,0xC8,0x83,0x3D}); c.word(mode); c.emit({1}); const auto moving = c.branch(0x84);
    c.emit({0x0F,0x57,0xC0,0x83,0x3D}); c.word(mode); c.emit({0}); const auto ordered = c.branch(0x84);
    c.emit({0xF3,0x0F,0x10,0x05}); c.word(one); const auto sectionOrder = c.jump();
    c.bind(moving); c.emit({0xF3,0x0F,0x10,0x0D}); c.word(along);
    c.bind(ordered); c.bind(sectionOrder);
    c.emit({0xF3,0x0F,0x11,0x05}); c.word(candidateTime);
    c.emit({0xF3,0x0F,0x11,0x0D}); c.word(candidateAlong);
    c.store(collector + 0x20, 0x3F800000); c.store(collector + 0x60, 0);
    for (uint32_t i = 0; i < 8; ++i) c.store(collector + 0x30 + i * 4, UINT32_MAX);
    c.emit({0x56,0xFF,0x35}); c.word(cdBody); c.emit({0xB9}); c.word(collector);
    c.call(imageBase + 0x108BFA0);
    c.store(collector + 4, 0x3F800000); // Do not let one actor truncate queries for other bodies or walls.
    c.emit({0x83,0x3D}); c.word(collector + 0x60); c.emit({0}); ignored.push_back(c.branch(0x84));
    // Ray-facing normals can flip when a blade exits the ground. Classify the plane, not its facing.
    c.emit({0xA1}); c.word(collector + 0x60); c.emit({0x3B,0x05}); c.word(sceneRoot);
    const auto bodyContact = c.branch(0x85);
    c.emit({0xF3,0x0F,0x10,0x15}); c.word(collector + 0x18);
    c.emit({0x66,0x0F,0x7E,0xD0,0x25}); c.word(0x7FFFFFFF); c.emit({0x66,0x0F,0x6E,0xD0});
    c.emit({0x0F,0x57,0xC0,0x0F,0x2F,0xD0}); const auto degenerateNormal = c.branch(0x86);
    for (uint32_t axis = 0; axis < 3; ++axis) {
        c.emit({0xF3,0x0F,0x10,0x0D}); c.word(collector + 0x10 + axis * 4);
        c.emit({0xF3,0x0F,0x59,0xC9,0xF3,0x0F,0x58,0xC1});
    }
    c.emit({0xF3,0x0F,0x51,0xC0,0xF3,0x0F,0x59,0x05}); c.word(groundNormalZ);
    c.emit({0x0F,0x2F,0xD0}); const auto steep = c.branch(0x82);
    c.emit({0x8B,0x35}); c.word(source);
    c.emit({0xF3,0x0F,0x10,0x15}); c.word(collector + 0x20);
    c.emit({0x0F,0x57,0xC0});
    for (uint32_t axis = 0; axis < 3; ++axis) {
        c.emit({0xF3,0x0F,0x10,0x0D}); c.word(input + 16 + axis * 4);
        c.emit({0xF3,0x0F,0x5C,0x0D}); c.word(input + axis * 4);
        c.emit({0xF3,0x0F,0x59,0xCA,0xF3,0x0F,0x58,0x0D}); c.word(input + axis * 4);
        c.emit({0xF3,0x0F,0x5C,0x8E}); c.word(offsetof(ActorMailbox, position) + axis * 4);
        c.emit({0xF3,0x0F,0x59,0x0D}); c.word(collector + 0x10 + axis * 4);
        c.emit({0xF3,0x0F,0x58,0xC1});
    }
    c.emit({0xF3,0x0F,0x5E,0x05}); c.word(collector + 0x18);
    compare(groundPlaneTolerance); const auto invalidHeight = c.branch(0x8A);
    const auto overhead = c.branch(0x87);
    // Keep ground feedback separate so it neither hides nor replaces a blocking scene contact.
    c.emit({0x81,0xC7}); c.word(GROUND_CONTACT_SLOT * HIT_STRIDE);
    c.bind(overhead); c.bind(invalidHeight); c.bind(steep); c.bind(degenerateNormal); c.bind(bodyContact);
    c.emit({0xF3,0x0F,0x10,0x05}); c.word(candidateTime);
    c.emit({0xF3,0x0F,0x10,0x0D}); c.word(candidateAlong);
    c.emit({0x83,0x3F,2}); const auto empty = c.branch(0x85);
    c.emit({0x0F,0x2F,0x47,48}); ignored.push_back(c.branch(0x87)); const auto earlier = c.branch(0x82);
    c.emit({0x0F,0x2F,0x4F,52}); ignored.push_back(c.branch(0x83));
    c.bind(earlier); c.bind(empty);
    auto resultCopy = [&](uint32_t from, unsigned char offset) {
        c.emit({0xA1}); c.word(from); c.emit({0x89,0x47,offset});
    };
    for (unsigned char axis = 0; axis < 3; ++axis) {
        resultCopy(input + axis * 4, 8 + axis * 4);
        resultCopy(input + 16 + axis * 4, 20 + axis * 4);
        resultCopy(collector + 0x10 + axis * 4, 32 + axis * 4);
    }
    resultCopy(collector + 0x20, 44); resultCopy(candidateTime, 48); resultCopy(candidateAlong, 52);
    resultCopy(candidateBody, 56); resultCopy(candidateRegion, 60);
    resultCopy(candidateDefence, 64);
    c.emit({0xC7,0x47,4,0,0,0,0});
    c.emit({0xA1}); c.word(collector + 0x60); c.emit({0x3B,0x05}); c.word(sceneRoot);
    const auto bodyMaterial = c.branch(0x85);
    c.emit({0xC7,0x47,4,0xFF,0xFF,0xFF,0xFF}); copy(sceneShape, material + 8);
    c.emit({0xBA}); c.word(collector + 0x30); c.emit({0xB9}); c.word(8);
    const auto keys = c.bytes.size();
    c.emit({0x83,0x3A,0xFF}); const auto terminated = c.branch(0x84);
    c.emit({0x83,0xC2,4,0x49}); c.bind(c.branch(0x85), keys); const auto unterminated = c.jump();
    c.bind(terminated); c.emit({0x81,0xFA}); c.word(collector + 0x30); const auto noKey = c.branch(0x84);
    c.emit({0x83,0xEA,4,0x89,0x15}); c.word(material + 12);
    c.emit({0xB9}); c.word(material); c.call(imageBase + 0x29D4B0);
    c.emit({0x83,0xF8,7}); const auto unknown = c.branch(0x87);
    c.emit({0x89,0x47,4});
    c.bind(unknown); c.bind(noKey); c.bind(unterminated); c.bind(bodyMaterial);
    c.emit({0xC7,0x07,2,0,0,0});
    for (auto branch : ignored) c.bind(branch);
    c.emit({0x61,0x9D,0xC2,8,0});
    if (c.bytes.size() > CONSTANTS_OFFSET) throw std::runtime_error("blade collector overflow");
    c.bytes.resize(CONSTANTS_OFFSET, 0);
    for (float value : {1.f, .00000001f, .01f, 8.f, 25.f, 16.f, 256.f, .5f, .1f}) {
        uint32_t bits; std::memcpy(&bits, &value, 4); c.word(bits);
    }
    c.word(code + COLLECTOR_OFFSET);
    c.word(0x447A0000); // 1000 milliseconds per second.
    if (c.bytes.size() > WEAPON_TRAIL_CODE_OFFSET - BLADE_SWEEP_CODE_OFFSET)
        throw std::runtime_error("blade code overlaps weapon trail hook");
    return c.bytes;
}
}
