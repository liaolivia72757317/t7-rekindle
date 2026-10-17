#pragma once
#include "TerrainHookFixture.h"
#include "SprintHookFixture.h"

namespace bladeFixture {
using worldFixture::BASE;
using worldFixture::writeWord;
constexpr uint32_t QUERY = BASE + 0x80000, OBSTACLES = BASE + 0x81000, HIT = BASE + 0x82000;
constexpr uint32_t NORMALS = OBSTACLES + 0x100;
constexpr uint32_t OBJECTS = BASE + 0x90000;
inline uint32_t object(uint32_t index) { return OBJECTS + index * 0x1000; }
inline void vector(HANDLE process, uint32_t address, std::array<float, 3> value) {
    t7r::Bytes bytes(sizeof(value)); std::memcpy(bytes.data(), value.data(), bytes.size());
    t7r::writeClientMemory(process, address, bytes);
}
inline void number(HANDLE process, uint32_t address, float value) {
    uint32_t bits; std::memcpy(&bits, &value, 4); writeWord(process, address, bits);
}
inline void prepare(HANDLE process) {
    for (uint32_t i = 0; i < 3; ++i) {
        const auto p = object(i), entity = worldFixture::ENTITY + i * 0x100;
        for (const auto& field : std::vector<std::array<uint32_t, 2>>{
            {entity+8,i+100}, {p,BASE+0x1777C54}, {p+0x48,p+0x100},
            {p+0x108,p+0x200}, {p+0x10C,1}, {p+0x254,p+0x300},
            {p+0x320,2}, {p+0x324,0}, {p+0x330,i+4}, {p+0x33C,p+0x400}, {p+0x478,p+0xF00},
            {p+0x408,terrainFixture::WORLD}, {p+0x410,p+0x500}, {p+0x60C,p+0x410},
            {p+0x700,BASE+0x178380C}, {p+0x798,1030411}, {p+0x79C,p+0xE00},
            {p+0x738,238}, {p+0x740,17}, {p+0x748,GetTickCount()},
            {p+0xE04,p+0x800}, {p+0x810,1030411}, {p+0x814,p+0x900},
            {p+0x900,BASE+0x179E16C}, {p+0x908,p+0xA00}, {p+0xA08,i+200},
            {p+0xA7C,entity}, {p+0xA90,p+0xB00}, {p+0xB00,BASE+0x1773684},
            {p+0xBB0,p+0xD00}, {p+0xBB4,p+0xC00}
        }) writeWord(process, field[0], field[1]);
        const std::string name = i == 1 ? "Ragdoll_Bip01 Head01" : i == 2 ? "Ragdoll_Bip01 L Calf01" : "Ragdoll_Bip01 Spine01";
        t7r::Bytes nameBytes(name.begin(), name.end()); nameBytes.push_back(0);
        t7r::writeClientMemory(process, p + 0xF00, nameBytes);
        // Swoosh visibility remains zero: geometry must not depend on graphical effects.
        vector(process, p + 0xC88, {270,150,1}); vector(process, p + 0xD88, {271,150,1});
    }
    writeWord(process, HIT, 0xBF800000);
    for (uint32_t i = 0; i < 6; ++i) {
        writeWord(process, OBSTACLES + i * 16, i < 3 ? 1 : 0);
        number(process, OBSTACLES + i * 16 + 4, 270.f + .25f * (i + 1));
        writeWord(process, OBSTACLES + i * 16 + 8,
                  i < 3 ? object(i) + 0x600 : i == 4 ? terrainFixture::ACTOR_CHILD : terrainFixture::CHILD);
        vector(process, NORMALS + i * 16, {-1,0,0});
    }
    t7r::WorldHookCode c;
    c.emit({0x60}); // EDI ray input, ESI collector. Synthetic planes use the native callback ABI.
    for (uint32_t i = 0; i < 6; ++i) {
        std::vector<size_t> skip;
        c.emit({0x83,0x3D}); c.word(OBSTACLES + i * 16); c.emit({0}); skip.push_back(c.branch(0x84));
        c.emit({0xF3,0x0F,0x10,0x47,16,0xF3,0x0F,0x5C,0x07,0x0F,0x57,0xC9,0x0F,0x2F,0xC1});
        skip.push_back(c.branch(0x84));
        c.emit({0xF3,0x0F,0x10,0x15}); c.word(OBSTACLES + i * 16 + 4);
        c.emit({0xF3,0x0F,0x5C,0x17,0xF3,0x0F,0x5E,0xD0,0x0F,0x2F,0xD1});
        skip.push_back(c.branch(0x82));
        c.emit({0x0F,0x2F,0x15}); c.word(OBSTACLES + 0x80); skip.push_back(c.branch(0x87));
        c.emit({0xF3,0x0F,0x11,0x15}); c.word(HIT + 0x10);
        for (uint32_t axis = 0; axis < 3; ++axis) {
            c.emit({0xA1}); c.word(NORMALS + i * 16 + axis * 4);
            c.emit({0xA3}); c.word(HIT + axis * 4);
        }
        c.emit({0x68}); c.word(HIT); c.emit({0xFF,0x35}); c.word(OBSTACLES + i * 16 + 8);
        c.emit({0x8B,0xCE,0x8B,0x06,0xFF,0x10});
        for (auto branch : skip) c.bind(branch);
    }
    c.emit({0x61,0xC3}); t7r::writeClientMemory(process, QUERY, c.bytes);
    number(process, OBSTACLES + 0x80, 1);
}
inline void verify(HANDLE process, t7r::ClientWorld& reader, uint32_t mailbox) {
    auto require = [](bool value, const char* message) { if (!value) throw std::runtime_error(message); };
    writeWord(process, worldFixture::FRAME_GATE, 1); Sleep(30);
    t7r::WorldSnapshot last;
    auto step = [&] {
        Sleep(16);
        require(!reader.poll(last), "native fixture expected a fresh consumer request");
        worldFixture::requestFrame(process);
        last = worldFixture::read(reader);
        return last;
    };
    auto sample = [&] {
        step(); return step();
    };
    auto blade = [&](std::array<float, 3> base, std::array<float, 3> tip) {
        Sleep(10); // Keep the two explicitly requested poses on distinct sample ticks.
        vector(process, object(0) + 0xC88, base); vector(process, object(0) + 0xD88, tip);
        return step().actors[0].blade;
    };
    for (uint32_t i = 0; i < 3; ++i)
        writeWord(process, worldFixture::ENTITY + i * 0x100 + 0x88, object(i));
    writeWord(process, worldFixture::ENTITY + 0x8C, object(0) + 0x700);
    auto value = sample().actors[0].blade;
    require(value.ready && value.length == 1 && value.base == std::array<float,3>{270,150,1}
            && value.tip == std::array<float,3>{271,150,1}, "native ribbon endpoints/visibility mismatch");
    require(!value.contacts[1].contact.hit && value.contacts[2].contact.hit && value.contacts[3].contact.hit,
            "blade collector lost target roots or admitted self");
    require(value.contacts[2].bodyIndex == 5 && value.contacts[2].region == 1
            && value.contacts[3].bodyIndex == 6 && value.contacts[3].region == 3,
            "blade collector lost original rigid-body identity or region");
    require(value.contacts[2].contact.position == std::array<float,3>{270.5f,150,1}
            && value.contacts[2].contact.normal == std::array<float,3>{-1,0,0}
            && value.contacts[2].along == .5f, "blade native point/normal/fraction mismatch");
    require(!sample().actors[1].blade.ready, "actor without a weapon published a blade");
    // A separate native equipped-weapon root must coexist with the same actor's hurt body.
    const auto defender = object(1), guard = object(4);
    for (const auto& field : std::vector<std::array<uint32_t, 2>>{
        {worldFixture::ENTITY + 0x100 + 0x8C, defender + 0x700}, {defender + 0x738,223},
        {defender + 0xE00,defender + 0x800}, {defender + 0xE0D,1},
        {defender + 0x800,defender + 0xE00}, {defender + 0x804,defender + 0xE00},
        {defender + 0x808,defender + 0xE00}, {defender + 0x920,1}, {defender + 0xA88,guard},
        {guard,BASE + 0x1777C54}, {guard + 0x34,guard + 0x100},
        {guard + 0x108,guard + 0x200}, {guard + 0x10C,1},
        {guard + 0x200,guard + 0x300}, {guard + 0x20D,1},
        {guard + 0x300,guard + 0x200}, {guard + 0x304,guard + 0x200}, {guard + 0x308,guard + 0x200},
        {guard + 0x384,guard + 0x400}, {guard + 0x43C,guard + 0x500},
        {guard + 0x508,terrainFixture::WORLD}, {guard + 0x510,guard + 0x700},
        {guard + 0x60C,guard + 0x510}, {OBSTACLES + 3 * 16,1}, {OBSTACLES + 3 * 16 + 8,guard + 0x600}
    }) writeWord(process, field[0], field[1]);
    number(process, OBSTACLES + 3 * 16 + 4, 270.4f);
    const auto defenceSlot = 2 + t7r::DEFENCE_CONTACT_OFFSET;
    const auto controller = worldFixture::CONTROLLER + 0x100;
    const auto oldPosition = last.actors[1].position;
    vector(process, controller + 0x10, {271.15f,150,0}); number(process, controller + 0xAC, 3.141592654f);
    value = sample().actors[0].blade;
    require(value.contacts[2].contact.hit && value.contacts[defenceSlot].contact.hit
        && value.contacts[defenceSlot].guardVolume && value.contacts[defenceSlot].defenceWeaponId == 1030411
        && value.contacts[defenceSlot].region == 0 && value.contacts[defenceSlot].bodyIndex == -1
        && std::abs(value.contacts[defenceSlot].along - .35f) < .001f,
        "front volume must precede the body and replace main-hand native roots");
    require(std::any_of(last.contacts.begin(), last.contacts.end(), [](const auto& event) {
        return event.hit.targetId == 2 && event.hit.guardVolume && event.hit.defenceWeaponId == 1030411;
    }), "guard origin lost in the game-thread journal");
    writeWord(process, guard + 0x34, 0);
    for (uint32_t state : {223u,224u,225u,226u,231u,232u,233u,234u,235u,241u,247u,253u}) {
        writeWord(process, defender + 0x738, state);
        require(sample().actors[0].blade.contacts[defenceSlot].contact.hit, "guard/prepare volume missing");
    }
    for (uint32_t state : {35u,237u,238u,243u,249u,255u,305u,309u,111u}) {
        writeWord(process, defender + 0x738, state);
        require(!sample().actors[0].blade.contacts[defenceSlot].contact.hit, "inactive guard or shield acquired a proxy");
    }
    writeWord(process, defender + 0x738, 223);
    writeWord(process, defender + 0x920, 0);
    require(!sample().actors[0].blade.contacts[defenceSlot].contact.hit, "inactive weapon acquired a volume");
    writeWord(process, defender + 0x920, 1);
    writeWord(process, defender + 0xBB0, 0);
    require(sample().actors[0].blade.contacts[defenceSlot].contact.hit, "volume incorrectly required a ribbon");
    writeWord(process, defender + 0xBB0, defender + 0xC00);
    vector(process, defender + 0xC88, {NAN,150,1});
    require(sample().actors[0].blade.contacts[defenceSlot].contact.hit, "invalid defender blade removed character geometry");
    vector(process, defender + 0xC88, {270.4f,149.5f,1});
    vector(process, defender + 0xD88, {270.4f,150.5f,1});
    vector(process, controller + 0x10, {271.15f,152,0});
    require(!sample().actors[0].blade.contacts[defenceSlot].contact.hit, "out-of-volume attack intercepted");
    vector(process, controller + 0x10, {270.5f,150.65f,0}); number(process, controller + 0xAC, 1.570796327f);
    require(sample().actors[0].blade.contacts[defenceSlot].contact.hit, "rotated volume missed");
    vector(process, controller + 0x10, {271.15f,150,0}); number(process, controller + 0xAC, 3.141592654f);
    blade({270.5f,150,1}, {271,150,1});
    value = sample().actors[0].blade;
    require(value.contacts[defenceSlot].contact.hit && value.contacts[defenceSlot].along == 0,
        "initial volume overlap missed");
    blade({270,149.8f,1}, {270,150.2f,1}); sample();
    value = blade({271,149.8f,1}, {271,150.2f,1});
    require(value.contacts[defenceSlot].contact.hit && value.contacts[defenceSlot].time > .34f
        && value.contacts[defenceSlot].time < .36f && value.contacts[defenceSlot].time < value.contacts[2].time,
        "swept volume contact tunneled or followed body contact");
    require(!sample().actors[0].blade.contacts[defenceSlot].contact.hit, "stationary blade replayed an old contact");
    const auto sourcePhase = static_cast<uint32_t>(last.actors[0].phaseAt);
    const auto targetPhase = static_cast<uint32_t>(last.actors[1].phaseAt);
    const auto clock = GetTickCount() - 50;
    writeWord(process, sprintFixture::CLOCK_MODE, 1);
    writeWord(process, object(0) + 0x748, clock - 500);
    for (uint32_t activation : {50u, 75u}) {
        const auto start = GetTickCount() - 50;
        writeWord(process, sprintFixture::CLOCK, start);
        writeWord(process, defender + 0x748, start);
        blade({270,149.8f,1}, {270,150.2f,1}); sample();
        writeWord(process, defender + 0x748, start + activation);
        writeWord(process, sprintFixture::CLOCK, start + 100);
        value = blade({271,149.8f,1}, {271,150.2f,1});
        require(last.ready && value.ready, "guard activation fixture needs a fresh blade snapshot");
        if (activation == 50) {
            if (!value.contacts[defenceSlot].contact.hit || std::abs(value.contacts[defenceSlot].time - .5f) >= .001f)
                throw std::runtime_error("guard activation did not clip the temporal strand: hit="
                    + std::to_string(value.contacts[defenceSlot].contact.hit) + "; time="
                    + std::to_string(value.contacts[defenceSlot].time) + "; spanMs=" + std::to_string(value.spanMs)
                    + "; sampledAt=" + std::to_string(last.sampledAt) + "; targetPhaseAt=" + std::to_string(last.actors[1].phaseAt));
        }
        else require(!value.contacts[defenceSlot].contact.hit, "guard replayed a crossing before activation");
    }
    writeWord(process, sprintFixture::CLOCK_MODE, 0);
    writeWord(process, object(0) + 0x748, sourcePhase); writeWord(process, defender + 0x748, targetPhase);
    blade({270,150,1}, {271,150,1}); sample();
    vector(process, controller + 0x10, oldPosition); number(process, controller + 0xAC, 2.5f);
    writeWord(process, guard + 0x34, guard + 0x100);
    writeWord(process, defender + 0x738, 309); writeWord(process, defender + 0x810, 1090311);
    value = sample().actors[0].blade;
    require(value.contacts[defenceSlot].defenceWeaponId == 1090311 && value.contacts[defenceSlot].contact.hit,
        "equipped offhand shield root was omitted");
    writeWord(process, defender + 0x738, 111);
    require(sample().actors[0].blade.contacts[defenceSlot].contact.hit, "shield feedback opened an interception gap");
    writeWord(process, defender + 0x810, 1030411);
    require(!sample().actors[0].blade.contacts[defenceSlot].contact.hit, "main weapon substituted for shield geometry");
    writeWord(process, defender + 0x738, 238); writeWord(process, OBSTACLES + 3 * 16, 0);
    writeWord(process, OBSTACLES + 3 * 16 + 8, terrainFixture::CHILD);
    number(process, OBSTACLES + 3 * 16 + 4, 271.f);
    writeWord(process, worldFixture::ENTITY + 0x100 + 0x8C, 0);
    writeWord(process, object(1) + 0x478, 0);
    require(sample().actors[0].blade.contacts[2].region == 0, "unknown body inferred region from contact height");
    writeWord(process, object(1) + 0x478, object(1) + 0xF00);
    const auto sourcePosition = last.actors[0].position, targetPosition = last.actors[1].position;
    auto movedSource = sourcePosition, movedTarget = targetPosition;
    movedSource[0] += .125f; movedTarget[0] -= .125f;
    vector(process, worldFixture::CONTROLLER + 0x10, movedSource);
    vector(process, worldFixture::CONTROLLER + 0x110, movedTarget);
    const auto movingActors = step();
    require(std::any_of(movingActors.contacts.begin(), movingActors.contacts.end(), [](const auto& event) {
        return event.actorId == 1 && event.hit.targetId == 2 && event.velocityReady
            && event.sourceVelocity[0] > 0 && event.targetVelocity[0] < 0
            && std::abs(event.sourceVelocity[0] + event.targetVelocity[0]) < .001f;
    }), "contact journal lost relative actor motion");
    vector(process, worldFixture::CONTROLLER + 0x10, sourcePosition);
    vector(process, worldFixture::CONTROLLER + 0x110, targetPosition); sample();
    blade({270,150,1}, {270,151,1}); sample();
    const auto frozen = t7r::readClientMemory(process, mailbox + t7r::ACTORS_DATA_OFFSET, sizeof(t7r::ActorMailbox));
    const auto publishedBatch = last.contactBatchId;
    auto frameOnly = [&] {
        Sleep(16); worldFixture::requestFrame(process);
        const auto deadline = GetTickCount64() + 3000;
        while (t7r::readClientMemory(process, worldFixture::FRAME_GATE, 4) != t7r::Bytes{1,0,0,0}) {
            require(GetTickCount64() < deadline, "unrequested native frame timeout"); Sleep(1);
        }
    };
    vector(process, object(0) + 0xC88, {271,150,1}); vector(process, object(0) + 0xD88, {271,151,1});
    writeWord(process, worldFixture::MOVABLE + 0x20, 1);
    writeWord(process, worldFixture::MOVABLE + 0x120, 1);
    frameOnly(); // No reader.poll: a complete crossing happens between consumer requests.
    writeWord(process, worldFixture::MOVABLE + 0x20, 0);
    writeWord(process, worldFixture::MOVABLE + 0x120, 0);
    for (int i = 0; i < 8; ++i) frameOnly(); // Keep sampling beyond the old 100 ms request-gap limit.
    require(t7r::readClientMemory(process, mailbox + t7r::ACTORS_DATA_OFFSET, sizeof(t7r::ActorMailbox)) == frozen,
            "native sampling changed the unacknowledged response");
    const auto retained = step();
    require(retained.contactsReady && retained.contactFrames >= 9 && !retained.droppedContacts
            && retained.contactBatchId != publishedBatch, "native sampling waited for a business request");
    require(!retained.actors[0].blade.contacts[2].contact.hit
            && std::any_of(retained.contacts.begin(), retained.contacts.end(), [&](const auto& event) {
                return event.actorId == 1 && event.hit.targetId == 2 && event.entity == 100 && event.targetEntity == 101
                    && event.state == 238 && event.sequence == 17 && event.occurredAt < retained.sampledAt - 100
                    && event.targetObservedAt >= event.occurredAt && event.targetObservedAt >= event.targetPhaseAt
                    && event.sourceObservedAt >= event.occurredAt && event.sourceObservedAt >= event.phaseAt
                    && event.sourceObservedAt == event.targetObservedAt
                    && event.hit.bodyIndex == 5 && event.hit.region == 1 && event.velocityReady && event.airborne && event.targetAirborne
                    && event.weaponVelocity[0] > 0 && event.sourceVelocity == std::array<float,3>{}
                    && event.targetVelocity == std::array<float,3>{}
                    && std::abs(event.hit.contact.position[0] - 270.5f) < .0001f;
            }), "native journal lost the swept contact before mailbox acknowledgement");
    require(step().contacts.empty(), "native journal replayed a consumed batch");
    blade({270,150,1}, {271,150,1}); sample();
    const auto record = t7r::readClientMemory(process, mailbox + t7r::CONTACTS_DATA_OFFSET, sizeof(t7r::ContactEventMailbox));
    t7r::Bytes full(t7r::MAX_FRAME_CONTACTS * sizeof(t7r::ContactEventMailbox));
    for (size_t i = 0; i < t7r::MAX_FRAME_CONTACTS; ++i)
        std::copy(record.begin(), record.end(), full.begin() + i * sizeof(t7r::ContactEventMailbox));
    t7r::writeClientMemory(process, mailbox + t7r::LIVE_CONTACTS_OFFSET, full);
    writeWord(process, mailbox + t7r::FRAME_DATA_OFFSET + offsetof(t7r::ContactFrameMailbox, count), t7r::MAX_FRAME_CONTACTS);
    const auto saturated = step();
    require(saturated.contacts.size() == t7r::MAX_FRAME_CONTACTS && saturated.droppedContacts >= 2,
            "native journal overflow was silent or overwrote retained contacts");
    const auto resumed = step();
    require(resumed.contacts.size() == 2 && resumed.droppedContacts == 0, "native journal failed to resume after acknowledgement");
    writeWord(process, OBSTACLES + 4 * 16, 1);
    value = sample().actors[0].blade;
    require(!value.contacts[0].contact.hit, "unregistered rigid body became scene contact");
    writeWord(process, OBSTACLES + 3 * 16, 1); number(process, OBSTACLES + 3 * 16 + 4, 270.625f);
    value = sample().actors[0].blade;
    require(value.contacts[0].contact.hit && value.contacts[0].contact.material == 4
            && value.contacts[2].along < value.contacts[0].along && value.contacts[3].along > value.contacts[0].along,
            "blade collector lost scene material or independently ordered contacts");
    number(process, OBSTACLES + 3 * 16 + 4, 270.2f);
    vector(process, NORMALS + 16, {0,0,1});
    for (uint32_t state : {238u,244u,250u,256u,439u}) {
        writeWord(process, object(0) + 0x738, state);
        for (const auto& normal : std::array<std::array<float, 3>, 12>{{
            {0,0,1}, {.6f,0,.8f}, {0,-.8f,.6f}, {.8660254f,0,.5f}, {.594f,0,.792f}, {-.6f,0,.8f},
            {0,0,-1}, {-.6f,0,-.8f}, {0,.8f,-.6f}, {-.8660254f,0,-.5f}, {-.594f,0,-.792f}, {.6f,0,-.8f}
        }}) {
            vector(process, NORMALS + 3 * 16, normal);
            const auto height = .25f - .02f - normal[0] * .2f / normal[2];
            blade({270,150,height}, {271,150,height});
            value = sample().actors[0].blade;
            require(value.ready && !value.contacts[0].contact.hit && value.contacts[2].contact.hit
                    && value.contacts[3].contact.hit, "ground or slope interrupted a blade or filtered a body contact");
            const auto& ground = value.contacts[t7r::GROUND_CONTACT_SLOT];
            require(ground.targetId == 0 && ground.contact.hit && ground.contact.material == 4
                    && std::abs(ground.along - .2f) < .0001f && ground.contact.position[2] == height,
                    "ground or slope lost its separate material feedback contact");
            require(std::any_of(last.contacts.begin(), last.contacts.end(), [&](const auto& event) {
                return event.actorId == 1 && event.hit.targetId == 0 && event.state == state
                    && event.targetEntity == 0 && event.targetState == 0
                    && event.hit.contact.position == ground.contact.position
                    && event.hit.contact.normal == ground.contact.normal && event.hit.contact.material == 4;
            }), "native journal lost ground feedback or treated its slot as an actor RID");
        }
    }
    writeWord(process, object(0) + 0x738, 238);
    vector(process, NORMALS + 16, {-1,0,0});
    vector(process, NORMALS + 3 * 16, {0,0,-1});
    blade({270,150,.25f}, {271,150,.25f});
    writeWord(process, OBSTACLES + 5 * 16, 1);
    number(process, OBSTACLES + 5 * 16 + 4, 270.625f);
    value = sample().actors[0].blade;
    require(value.contacts[0].contact.hit && value.contacts[0].contact.material == 4
            && value.contacts[0].contact.normal == std::array<float,3>{-1,0,0}
            && value.contacts[0].along == .625f, "earlier ground contact hid a later wall");
    auto requireGroundAndWall = [&] {
        require(value.contacts[t7r::GROUND_CONTACT_SLOT].contact.hit && value.contacts[t7r::GROUND_CONTACT_SLOT].targetId == 0
                && value.contacts[t7r::GROUND_CONTACT_SLOT].contact.normal == std::array<float,3>{0,0,-1}
                && std::abs(value.contacts[t7r::GROUND_CONTACT_SLOT].along - .2f) < .0001f,
                "wall contact replaced earlier ground feedback");
        require(std::count_if(last.contacts.begin(), last.contacts.end(), [](const auto& event) {
            return event.actorId == 1 && event.hit.targetId == 0;
        }) == 2, "native frame did not retain both ground and wall contacts");
    };
    requireGroundAndWall();
    vector(process, NORMALS + 3 * 16, {-1,0,0});
    number(process, OBSTACLES + 3 * 16 + 4, 270.625f);
    vector(process, NORMALS + 5 * 16, {0,0,-1});
    number(process, OBSTACLES + 5 * 16 + 4, 270.2f);
    value = sample().actors[0].blade;
    require(value.contacts[0].contact.hit && value.contacts[0].along == .625f
            && value.contacts[0].contact.normal == std::array<float,3>{-1,0,0},
            "later ground callback replaced a wall contact");
    requireGroundAndWall();
    vector(process, NORMALS + 3 * 16, {0,0,-1});
    value = sample().actors[0].blade;
    require(!value.contacts[0].contact.hit && value.contacts[t7r::GROUND_CONTACT_SLOT].contact.hit
            && std::abs(value.contacts[t7r::GROUND_CONTACT_SLOT].along - .2f) < .0001f,
            "ground slot did not retain the earliest contact independently");
    writeWord(process, OBSTACLES + 5 * 16, 0);
    blade({270,150,1}, {271,150,1});
    for (const auto& normal : std::array<std::array<float, 3>, 3>{{{.8660831f,0,.4999f}, {0,0,-1}, {0,0,1}}}) {
        vector(process, NORMALS + 3 * 16, normal);
        value = sample().actors[0].blade;
        require(value.contacts[0].contact.hit && !value.contacts[t7r::GROUND_CONTACT_SLOT].contact.hit,
                "steep wall or ceiling was treated as ground or retained stale ground feedback");
    }
    vector(process, NORMALS + 3 * 16, {-1,0,0});
    writeWord(process, OBSTACLES + 3 * 16, 0);
    writeWord(process, worldFixture::PLAYER + 0x134, 0); value = sample().actors[0].blade;
    require(!value.contacts[2].contact.hit && value.contacts[3].contact.hit, "corpse consumed blade contact");
    writeWord(process, worldFixture::PLAYER + 0x134, 1);
    writeWord(process, object(1) + 0x320, 9); value = sample().actors[0].blade;
    require(!value.contacts[2].contact.hit, "movement trigger admitted as hurt geometry");
    writeWord(process, object(1) + 0x320, 2);
    writeWord(process, object(1) + 0x408, terrainFixture::WORLD + 0x100); value = sample().actors[0].blade;
    require(!value.contacts[2].contact.hit, "foreign physics world admitted as hurt geometry");
    writeWord(process, object(1) + 0x408, terrainFixture::WORLD);
    blade({270,150,1}, {270,151,1}); sample();
    value = blade({271,150,1}, {271,151,1}); // Both static sections miss; only the swept interior crosses.
    require(value.ready && value.spanMs > 0 && value.contacts[2].contact.hit
            && std::abs(value.contacts[2].time - .5f) < .0001f
            && std::abs(value.contacts[2].contact.position[0] - 270.5f) < .0001f,
            "moving blade tunneled between frame endpoints");
    require(!sample().actors[0].blade.contacts[2].contact.hit, "old blade sweep replayed while stationary");
    writeWord(process, sprintFixture::CLOCK, GetTickCount()); writeWord(process, sprintFixture::CLOCK_MODE, 1);
    blade({270,150,1}, {270,151,1});
    value = blade({271,150,1}, {271,151,1});
    require(value.ready && value.spanMs == 0 && value.contacts[2].contact.hit,
            "two native frames on one clock tick lost their swept geometry");
    require(std::none_of(last.contacts.begin(), last.contacts.end(), [](const auto& event) { return event.velocityReady; }),
            "zero-time samples fabricated damage velocity");
    writeWord(process, sprintFixture::CLOCK_MODE, 0);
    // Missing nodes reset history; restoring them must not connect across the missing frame.
    writeWord(process, object(0) + 0xBB0, 0);
    require(!sample().actors[0].blade.ready, "missing ribbon node accepted");
    vector(process, object(0) + 0xC88, {270,150,1}); vector(process, object(0) + 0xD88, {270,151,1});
    writeWord(process, object(0) + 0xBB0, object(0) + 0xD00);
    value = sample().actors[0].blade;
    require(value.ready && !value.contacts[2].contact.hit, "missing node bridged stale weapon history");
    writeWord(process, object(0) + 0xA08, 999);
    value = blade({271,150,1}, {271,151,1});
    require(value.ready && value.spanMs == 0 && !value.contacts[2].contact.hit, "weapon replacement retained old sweep");
    writeWord(process, worldFixture::INSTANCE + 0x68, 1235);
    value = blade({270,150,1}, {270,151,1});
    require(value.ready && value.spanMs == 0 && !value.contacts[2].contact.hit, "epoch change retained old sweep");
    writeWord(process, worldFixture::INSTANCE + 0x68, 1234);
    sample(); Sleep(120); value = blade({271,150,1}, {271,151,1});
    require(value.ready && value.spanMs == 0 && !value.contacts[2].contact.hit, "long sampling gap retained old sweep");
    number(process, object(0) + 0xD88, std::numeric_limits<float>::quiet_NaN());
    require(!sample().actors[0].blade.ready, "non-finite ribbon point accepted");
    value = blade({270,150,1}, {270,150,1}); require(!value.ready, "zero-length blade accepted");
    value = blade({270,150,1}, {271,150,1}); require(value.ready, "blade recovery failed");
    writeWord(process, terrainFixture::MAP + 0x1C, 0);
    require(!sample().actors[0].blade.ready, "missing scene world accepted as blade clear");
    writeWord(process, terrainFixture::MAP + 0x1C, terrainFixture::COLLISION);
    // Fists have no ribbon mesh: use their bone-attached entity, never the sheathed blade.
    const auto fist = object(0);
    writeWord(process, fist + 0x798, 100000); writeWord(process, fist + 0x810, 100000);
    writeWord(process, fist + 0xA08, 1000);
    writeWord(process, fist + 0xA4C, fist + 0xF00); writeWord(process, fist + 0xA48, fist + 0xF20);
    vector(process, fist + 0xF00, {270.5f,150,1});
    for (uint32_t i = 0; i < 9; ++i) number(process, fist + 0xF20 + i * 4, i == 2 || i == 4 || i == 6 ? 1.f : 0.f);
    value = step().actors[0].blade;
    require(value.ready && value.spanMs == 0 && std::abs(value.length - .3f) < .0001f
            && std::abs(value.base[0] - 270.35f) < .0001f && std::abs(value.tip[0] - 270.65f) < .0001f
            && value.contacts[2].contact.hit && !value.contacts[3].contact.hit,
            "fist contact reused the sword or lost its native hand transform");
    writeWord(process, fist + 0xA4C, 0);
    require(!sample().actors[0].blade.ready, "missing fist transform fell back to a sword");
    writeWord(process, fist + 0xA4C, fist + 0xF00);
    number(process, fist + 0xF00, std::numeric_limits<float>::quiet_NaN());
    require(!sample().actors[0].blade.ready, "non-finite fist pose accepted");
    writeWord(process, fist + 0x798, 1030411); writeWord(process, fist + 0x810, 1030411);
    writeWord(process, fist + 0xA08, 200);
    value = step().actors[0].blade;
    require(value.ready && value.spanMs == 0 && value.length == 1, "equipping sword retained fist geometry");
    for (uint32_t i = 0; i < 3; ++i) writeWord(process, worldFixture::ENTITY + i * 0x100 + 0x88, 0);
    writeWord(process, worldFixture::ENTITY + 0x8C, 0); sample();
    writeWord(process, worldFixture::FRAME_GATE, 0);
}
}
