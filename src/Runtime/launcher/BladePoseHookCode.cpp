#include "ClientWorld.h"
#include "WorldHookCode.h"
#include <cstring>

namespace t7r {
namespace {
// Bounded MSVC map successor. EBX is the node; a malformed tree ends iteration.
void nextNode(WorldHookCode& c) {
    c.emit({0xB9}); c.word(64);
    c.emit({0x8B,0x43,8,0x80,0x78,0x0D,0}); const auto ascend = c.branch(0x85);
    c.emit({0x8B,0xD8});
    const auto descend = c.bytes.size();
    c.emit({0x8B,0x03,0x80,0x78,0x0D,0}); const auto leaf = c.branch(0x85);
    c.emit({0x8B,0xD8,0x49}); c.bind(c.branch(0x85), descend);
    const auto invalid = c.jump();
    c.bind(ascend);
    const auto parent = c.bytes.size();
    c.emit({0x8B,0x43,4,0x80,0x78,0x0D,0}); const auto end = c.branch(0x85);
    c.emit({0x3B,0x58,8}); const auto found = c.branch(0x85);
    c.emit({0x8B,0xD8,0x49}); c.bind(c.branch(0x85), parent);
    c.bind(invalid); c.emit({0x33,0xDB}); const auto done = c.jump();
    c.bind(end); c.bind(found); c.emit({0x8B,0xD8});
    c.bind(leaf); c.bind(done);
}

// Only shields use native defensive roots; main-hand defence uses the query volume.
void defenceRoots(WorldHookCode& c, uint32_t imageBase) {
    c.emit({0x60,0x8B,0xF0}); // ESI fightable; EBP blade mailbox.
    std::vector<size_t> done;
    c.emit({0x8B,0x85}); c.word(offsetof(BladeMailbox, state));
    std::vector<size_t> enabled;
    for (uint32_t state : {309u,111u}) {
        c.emit({0x3D}); c.word(state); enabled.push_back(c.branch(0x84));
    }
    done.push_back(c.jump());
    for (auto branch : enabled) c.bind(branch);
    c.emit({0x8B,0x9E}); c.word(0x9C); c.emit({0x85,0xDB}); done.push_back(c.branch(0x84));
    c.emit({0x8B,0x1B,0xBF}); c.word(64);
    const auto weaponLoop = c.bytes.size();
    c.emit({0x85,0xDB}); done.push_back(c.branch(0x84));
    c.emit({0x80,0x7B,0x0D,0}); done.push_back(c.branch(0x85));
    std::vector<size_t> skipWeapon;
    c.emit({0x8B,0x53,0x10,0x81,0xBD}); c.word(offsetof(BladeMailbox, state)); c.word(309);
    const auto shield = c.branch(0x84);
    c.emit({0x81,0xBD}); c.word(offsetof(BladeMailbox, state)); c.word(111);
    const auto main = c.branch(0x85);
    c.bind(shield);
    c.emit({0x3B,0x96}); c.word(0x98); skipWeapon.push_back(c.branch(0x84));
    const auto selected = c.jump();
    c.bind(main); c.emit({0x3B,0x96}); c.word(0x98); skipWeapon.push_back(c.branch(0x85));
    c.bind(selected);
    c.emit({0x8B,0x43,0x14,0x85,0xC0}); skipWeapon.push_back(c.branch(0x84));
    c.emit({0x81,0x38}); c.word(imageBase + 0x179E16C); skipWeapon.push_back(c.branch(0x85));
    c.emit({0x80,0x78,0x20,1}); skipWeapon.push_back(c.branch(0x85));
    c.emit({0x8B,0x48,8,0x85,0xC9}); skipWeapon.push_back(c.branch(0x84));
    c.emit({0x83,0x79,0x7C,0}); skipWeapon.push_back(c.branch(0x84));
    c.emit({0x60,0x52,0x68}); c.word(imageBase + 0x1777D50); c.call(imageBase + 0x26A1C0);
    c.emit({0x5F}); // EDI weapon ID; outer iteration remains saved.
    std::vector<size_t> noRoots;
    c.emit({0x85,0xC0}); noRoots.push_back(c.branch(0x84));
    c.emit({0x81,0x38}); c.word(imageBase + 0x1777C54); noRoots.push_back(c.branch(0x85));
    c.emit({0x8B,0x40,0x34,0x85,0xC0}); noRoots.push_back(c.branch(0x84));
    c.emit({0x83,0x78,0x0C,MAX_BLADE_BODIES}); noRoots.push_back(c.branch(0x87));
    c.emit({0x8B,0x40,8,0x85,0xC0}); noRoots.push_back(c.branch(0x84));
    c.emit({0x8B,0x18,0xBE}); c.word(MAX_BLADE_BODIES);
    const auto rootLoop = c.bytes.size();
    c.emit({0x85,0xDB}); noRoots.push_back(c.branch(0x84));
    c.emit({0x80,0x7B,0x0D,0}); noRoots.push_back(c.branch(0x85));
    std::vector<size_t> skipRoot;
    c.emit({0x8B,0x83}); c.word(0x84); c.emit({0x85,0xC0}); skipRoot.push_back(c.branch(0x84));
    c.emit({0x8B,0x40,0x3C,0x85,0xC0}); skipRoot.push_back(c.branch(0x84));
    c.emit({0x83,0x78,0x10,0}); skipRoot.push_back(c.branch(0x84));
    c.emit({0x8B,0x50,8,0x85,0xD2}); skipRoot.push_back(c.branch(0x84));
    c.emit({0x8B,0x8D}); c.word(offsetof(BladeMailbox, defenceCount));
    c.emit({0x83,0xF9,MAX_BLADE_BODIES}); noRoots.push_back(c.branch(0x83));
    c.emit({0x83,0xC0,0x10,0x89,0x84,0x8D}); c.word(offsetof(BladeMailbox, defenceRoots));
    c.emit({0x89,0x94,0x8D}); c.word(offsetof(BladeMailbox, defenceWorlds));
    c.emit({0x89,0xBC,0x8D}); c.word(offsetof(BladeMailbox, defenceWeapons));
    c.emit({0xFF,0x85}); c.word(offsetof(BladeMailbox, defenceCount));
    for (auto branch : skipRoot) c.bind(branch);
    nextNode(c); c.emit({0x4E}); c.bind(c.branch(0x85), rootLoop);
    for (auto branch : noRoots) c.bind(branch);
    c.emit({0x61});
    for (auto branch : skipWeapon) c.bind(branch);
    nextNode(c); c.emit({0x4F}); c.bind(c.branch(0x85), weaponLoop);
    for (auto branch : done) c.bind(branch);
    c.emit({0x61});
}
}
Bytes buildBladePoseHook(uint32_t address, uint32_t imageBase) {
    WorldHookCode c;
    c.emit({0x60,0x8B,0xF1,0x8D,0xAF}); c.word(offsetof(ActorMailbox, blade)); // ESI entity, EBP blade.
    auto store = [&](uint32_t offset, uint32_t value) { c.emit({0xC7,0x85}); c.word(offset); c.word(value); };
    auto save = [&](uint32_t offset) { c.emit({0x89,0x85}); c.word(offset); };
    store(offsetof(BladeMailbox, poseReady), 0);
    store(offsetof(BladeMailbox, bodyCount), 0);
    store(offsetof(BladeMailbox, bodyWorld), 0);
    store(offsetof(BladeMailbox, defenceCount), 0);
    store(offsetof(BladeMailbox, guardWeaponId), 0);
    store(offsetof(BladeMailbox, state), 0);
    c.emit({0x8B,0x46,8}); save(offsetof(BladeMailbox, actorEntity));
    c.emit({0x89,0xB5}); c.word(offsetof(BladeMailbox, actorPointer));
    auto component = [&](uint32_t name) {
        c.emit({0x8B,0xCE,0x68}); c.word(imageBase + name); c.call(imageBase + 0x26A1C0);
    };
    std::vector<size_t> noBodies, noBlade;
    auto nonzero = [&](unsigned char modrm, std::vector<size_t>& failures) {
        c.emit({0x85,modrm}); failures.push_back(c.branch(0x84));
    };
    component(0x1777D50); nonzero(0xC0, noBodies); // GeHavokPhysicable.
    c.emit({0x81,0x38}); c.word(imageBase + 0x1777C54); noBodies.push_back(c.branch(0x85));
    c.emit({0x8B,0x40,0x48}); nonzero(0xC0, noBodies); // Bone-linked hurt bodies, not movement triggers.
    c.emit({0x8B,0x58,0x0C,0x83,0xFB}); c.emit({static_cast<unsigned char>(MAX_BLADE_BODIES)});
    noBodies.push_back(c.branch(0x87)); nonzero(0xDB, noBodies);
    c.emit({0x8B,0x50,8}); nonzero(0xD2, noBodies);
    const auto bodyLoop = c.bytes.size();
    std::vector<size_t> skipBody;
    c.emit({0x8B,0x42,0x54}); nonzero(0xC0, skipBody);
    c.emit({0x83,0x78,0x20,2}); skipBody.push_back(c.branch(0x85));
    c.emit({0x8B,0x40,0x3C}); nonzero(0xC0, skipBody);
    c.emit({0x83,0x78,0x10,0}); skipBody.push_back(c.branch(0x84));
    c.emit({0x8B,0x48,8}); nonzero(0xC9, skipBody);
    c.emit({0x89,0x8D}); c.word(offsetof(BladeMailbox, bodyWorld));
    c.emit({0x83,0xC0,0x10,0x8B,0x8D}); c.word(offsetof(BladeMailbox, bodyCount));
    c.emit({0x89,0x84,0x8D}); c.word(offsetof(BladeMailbox, roots));
    c.emit({0x53,0x8B,0x42,0x54,0x8B,0x58,0x30,0x89,0x9C,0x8D});
    c.word(offsetof(BladeMailbox, bodyIndices));
    c.emit({0x8B,0x40,0x3C,0x8B,0x40,0x78,0x83,0xE0,0xFE,0x51}); // hkpWorldObject::m_name (hkStringPtr).
    const auto classifyCall = c.bytes.size(); c.emit({0xE8}); c.word(0);
    c.emit({0x59,0x89,0x84,0x8D}); c.word(offsetof(BladeMailbox, regions)); c.emit({0x5B});
    c.emit({0xFF,0x85}); c.word(offsetof(BladeMailbox, bodyCount));
    for (auto branch : skipBody) c.bind(branch);
    c.emit({0x83,0xC2,0x60,0x4B}); c.bind(c.branch(0x85), bodyLoop);
    for (auto branch : noBodies) c.bind(branch);

    component(0x1783860); nonzero(0xC0, noBlade); // GeFightable's current weapon map.
    c.emit({0x81,0x38}); c.word(imageBase + 0x178380C); noBlade.push_back(c.branch(0x85));
    for (const auto& field : std::array<std::array<uint32_t, 2>, 3>{{
        {0x38, offsetof(BladeMailbox, state)}, {0x40, offsetof(BladeMailbox, sequence)},
        {0x48, offsetof(BladeMailbox, phaseTick)}
    }}) {
        c.emit({0x8B,0x50,static_cast<unsigned char>(field[0]),0x89,0x95}); c.word(field[1]);
    }
    defenceRoots(c, imageBase);
    c.emit({0x8B,0x90}); c.word(0x98); nonzero(0xD2, noBlade);
    c.emit({0x8B,0x80}); c.word(0x9C); nonzero(0xC0, noBlade);
    c.emit({0x8B,0x40,4,0xB9}); c.word(64);
    const auto weaponLoop = c.bytes.size();
    nonzero(0xC0, noBlade);
    c.emit({0x80,0x78,0x0D,0}); noBlade.push_back(c.branch(0x85));
    c.emit({0x3B,0x50,0x10}); const auto found = c.branch(0x84), left = c.branch(0x8C);
    c.emit({0x8B,0x40,8}); const auto advanced = c.jump();
    c.bind(left); c.emit({0x8B,0x00}); c.bind(advanced);
    c.emit({0x49}); c.bind(c.branch(0x85), weaponLoop); noBlade.push_back(c.jump());
    c.bind(found); c.emit({0x8B,0x40,0x14}); nonzero(0xC0, noBlade);
    c.emit({0x81,0x38}); c.word(imageBase + 0x179E16C); noBlade.push_back(c.branch(0x85));
    c.emit({0x80,0x78,0x20,1}); const auto inactive = c.branch(0x85);
    c.emit({0x89,0x95}); c.word(offsetof(BladeMailbox, guardWeaponId));
    c.bind(inactive);
    c.emit({0x8B,0x70,8}); nonzero(0xF6, noBlade);
    c.emit({0x8B,0x46,8}); save(offsetof(BladeMailbox, weaponEntity));
    c.emit({0x83,0x7E,0x7C,0}); noBlade.push_back(c.branch(0x84));
    c.emit({0x81,0xFA}); c.word(100000); const auto fist = c.branch(0x84);
    component(0x17736BC); nonzero(0xC0, noBlade); // GeSwooshable holds the original bound NiAVObjects.
    c.emit({0x81,0x38}); c.word(imageBase + 0x1773684); noBlade.push_back(c.branch(0x85));
    c.emit({0x8B,0xD8});
    // Native ribbon sampling reads XYZ at NiAVObject + 0x88. B is the blade root, A the tip.
    // Read the live transforms, not the smoothed visual trail or its quality/visibility flags.
    for (const auto& point : std::array<std::array<uint32_t, 2>, 2>{{
        {0xB4, offsetof(BladeMailbox, base)}, {0xB0, offsetof(BladeMailbox, tip)}
    }}) {
        c.emit({0x8B,0x93}); c.word(point[0]); nonzero(0xD2, noBlade);
        for (uint32_t axis = 0; axis < 3; ++axis) {
            c.emit({0x8B,0x82}); c.word(0x88 + axis * 4); save(point[1] + axis * 4);
            c.emit({0x25}); c.word(0x7FFFFFFF); c.emit({0x3D}); c.word(0x47C35000);
            noBlade.push_back(c.branch(0x83));
        }
    }
    const auto ribbonReady = c.jump();
    c.bind(fist);
    // The native default fist has no mesh/ribbon. Its entity follows the RHand attachment.
    // Reuse the segment sweep with the resource's 0.15 m half-extent, not the sheathed sword.
    c.emit({0x8B,0x56,0x4C}); nonzero(0xD2, noBlade);
    c.emit({0x8B,0x5E,0x48}); nonzero(0xDB, noBlade);
    std::vector<size_t> radiusReferences;
    for (uint32_t axis = 0; axis < 3; ++axis) {
        c.emit({0xF3,0x0F,0x10,0x42,static_cast<unsigned char>(axis * 4)});
        c.emit({0xF3,0x0F,0x10,0x4B,static_cast<unsigned char>((axis * 3 + 2) * 4)});
        c.emit({0xF3,0x0F,0x59,0x0D}); radiusReferences.push_back(c.bytes.size()); c.word(0);
        c.emit({0x0F,0x28,0xD0,0xF3,0x0F,0x5C,0xC1,0xF3,0x0F,0x58,0xD1});
        c.emit({0x66,0x0F,0x7E,0xC0}); save(offsetof(BladeMailbox, base) + axis * 4);
        c.emit({0x25}); c.word(0x7FFFFFFF); c.emit({0x3D}); c.word(0x47C35000);
        noBlade.push_back(c.branch(0x83));
        c.emit({0x66,0x0F,0x7E,0xD0}); save(offsetof(BladeMailbox, tip) + axis * 4);
        c.emit({0x25}); c.word(0x7FFFFFFF); c.emit({0x3D}); c.word(0x47C35000);
        noBlade.push_back(c.branch(0x83));
    }
    c.bind(ribbonReady);
    store(offsetof(BladeMailbox, poseReady), 1);
    for (auto branch : noBlade) c.bind(branch);
    c.emit({0x61,0xC3});
    const auto radius = address + BLADE_POSE_CODE_OFFSET + static_cast<uint32_t>(c.bytes.size());
    for (const auto offset : radiusReferences) std::memcpy(c.bytes.data() + offset, &radius, sizeof(radius));
    c.word(0x3E19999A); // 0.15f.
    const auto classifier = c.bytes.size();
    const auto displacement = static_cast<int32_t>(classifier - classifyCall - 5);
    std::memcpy(c.bytes.data() + classifyCall + 1, &displacement, 4);
    c.emit({0x52,0x8B,0xD0,0x33,0xC0,0x85,0xD2});
    std::vector<size_t> classified{c.branch(0x84)};
    const std::string prefix = "Ragdoll_Bip01 ";
    for (size_t i = 0; i < prefix.size(); ++i) {
        c.emit({0x80,0x7A,static_cast<unsigned char>(i),static_cast<unsigned char>(prefix[i])});
        classified.push_back(c.branch(0x85));
    }
    // Names are from the original male/female ragdolls; unknown bodies remain explicitly unknown.
    for (const auto& entry : std::initializer_list<std::pair<std::string, uint32_t>>{
        {"Head01",1}, {"Pelvis01",2}, {"Spine01",2}, {"L UpperArm01",2}, {"R UpperArm01",2},
        {"L Thigh01",3}, {"R Thigh01",3}, {"L Calf01",3}, {"R Calf01",3}}) {
        std::vector<size_t> different;
        for (size_t i = 0; i <= entry.first.size(); ++i) {
            c.emit({0x80,0x7A,static_cast<unsigned char>(prefix.size() + i),
                    static_cast<unsigned char>(entry.first.c_str()[i])});
            different.push_back(c.branch(0x85));
        }
        c.emit({0xB8}); c.word(entry.second); classified.push_back(c.jump());
        for (auto branch : different) c.bind(branch);
    }
    for (auto branch : classified) c.bind(branch);
    c.emit({0x5A,0xC3});
    if (c.bytes.size() > BLADE_SWEEP_CODE_OFFSET - BLADE_POSE_CODE_OFFSET)
        throw std::runtime_error("blade pose hook overflow");
    return c.bytes;
}
}
