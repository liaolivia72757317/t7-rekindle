#include "ClientWorld.h"
#include "WorldHookCode.h"

namespace t7r {
namespace {
void motionSamples(WorldHookCode& c, uint32_t batch, uint32_t frame) {
    c.emit({0xBE}); c.word(batch); c.emit({0xB9}); c.word(MAX_WORLD_ACTORS);
    const auto loop = c.bytes.size();
    c.emit({0x8D,0xBE}); c.word(offsetof(ActorMailbox, blade));
    auto load = [&](uint32_t field) { c.emit({0x8B,0x87}); c.word(field); };
    auto save = [&](uint32_t field) { c.emit({0x89,0x87}); c.word(field); };
    c.emit({0xC7,0x87}); c.word(offsetof(BladeMailbox, motionReady)); c.word(0);
    c.emit({0x83,0x3E,1}); const auto absent = c.branch(0x85);
    std::vector<size_t> reset;
    load(offsetof(BladeMailbox, actorEntity)); c.emit({0x3B,0x87}); c.word(offsetof(BladeMailbox, previousMotionEntity));
    reset.push_back(c.branch(0x85));
    for (uint32_t i : {0u,4u}) {
        c.emit({0xA1}); c.word(frame + offsetof(ContactFrameMailbox, epoch) + i);
        c.emit({0x3B,0x87}); c.word(offsetof(BladeMailbox, previousMotionEpoch) + i); reset.push_back(c.branch(0x85));
    }
    c.emit({0xA1}); c.word(frame); c.emit({0x2B,0x87}); c.word(offsetof(BladeMailbox, previousMotionTick));
    reset.push_back(c.branch(0x84)); c.emit({0x83,0xF8,100}); reset.push_back(c.branch(0x87));
    c.emit({0xF3,0x0F,0x2A,0xD0,0xB8}); c.word(0x447A0000);
    c.emit({0x66,0x0F,0x6E,0xD8,0xF3,0x0F,0x5E,0xDA,0x0F,0x57,0xD2});
    for (uint32_t i = 0; i < 3; ++i) {
        c.emit({0xF3,0x0F,0x10,0x86}); c.word(offsetof(ActorMailbox, position) + i * 4);
        c.emit({0xF3,0x0F,0x5C,0x87}); c.word(offsetof(BladeMailbox, previousPosition) + i * 4);
        c.emit({0x0F,0x28,0xC8,0xF3,0x0F,0x59,0xC9,0xF3,0x0F,0x58,0xD1,0xF3,0x0F,0x59,0xC3});
        c.emit({0xF3,0x0F,0x11,0x87}); c.word(offsetof(BladeMailbox, velocity) + i * 4);
    }
    c.emit({0xB8}); c.word(0x41800000); c.emit({0x66,0x0F,0x6E,0xC0,0x0F,0x2F,0xD0});
    reset.push_back(c.branch(0x87)); reset.push_back(c.branch(0x8A));
    c.emit({0xC7,0x87}); c.word(offsetof(BladeMailbox, motionReady)); c.word(1);
    for (auto branch : reset) c.bind(branch);
    load(offsetof(BladeMailbox, actorEntity)); save(offsetof(BladeMailbox, previousMotionEntity));
    c.emit({0xA1}); c.word(frame); save(offsetof(BladeMailbox, previousMotionTick));
    for (uint32_t i : {0u,4u}) {
        c.emit({0xA1}); c.word(frame + offsetof(ContactFrameMailbox, epoch) + i);
        save(offsetof(BladeMailbox, previousMotionEpoch) + i);
    }
    for (uint32_t i = 0; i < 3; ++i) {
        c.emit({0x8B,0x86}); c.word(offsetof(ActorMailbox, position) + i * 4);
        save(offsetof(BladeMailbox, previousPosition) + i * 4);
    }
    const auto next = c.jump(); c.bind(absent);
    c.emit({0xC7,0x87}); c.word(offsetof(BladeMailbox, previousMotionEntity)); c.word(0);
    c.bind(next); c.emit({0x81,0xC6}); c.word(sizeof(ActorMailbox)); c.emit({0x49});
    c.bind(c.branch(0x85), loop);
}
}
Bytes buildContactFrameHook(uint32_t address) {
    const auto frame = address + FRAME_DATA_OFFSET;
    const auto batch = address + LIVE_ACTORS_OFFSET;
    constexpr uint32_t BO = offsetof(ActorMailbox, blade);
    WorldHookCode c;
    c.emit({0x60}); motionSamples(c, batch, frame);
    c.emit({0xBE}); c.word(batch); c.emit({0xBA}); c.word(1);
    c.emit({0xFF,0x05}); c.word(frame + offsetof(ContactFrameMailbox, frames));
    const auto actorLoop = c.bytes.size();
    c.emit({0x83,0x3E,1}); const auto noActor = c.branch(0x85);
    c.emit({0x83,0xBE}); c.word(BO + offsetof(BladeMailbox, traceReady)); c.emit({1});
    const auto noTrace = c.branch(0x85);
    c.emit({0x33,0xDB,0x8D,0xAE}); c.word(BO + offsetof(BladeMailbox, contacts));
    const auto contactLoop = c.bytes.size();
    c.emit({0x83,0x7D,0,2}); const auto noContact = c.branch(0x85);
    c.emit({0xA1}); c.word(frame + offsetof(ContactFrameMailbox, count));
    c.emit({0x3D}); c.word(MAX_FRAME_CONTACTS); const auto full = c.branch(0x83);
    c.emit({0x69,0xF8}); c.word(sizeof(ContactEventMailbox));
    c.emit({0x81,0xC7}); c.word(address + LIVE_CONTACTS_OFFSET);
    c.emit({0x89,0x17,0x8B,0xC3,0x83,0xFB,GROUND_CONTACT_SLOT});
    const auto defenceRid = c.branch(0x87), targetRid = c.branch(0x82);
    c.emit({0x33,0xC0}); // The separate ground slot still denotes scene target zero.
    const auto sceneRid = c.jump();
    c.bind(defenceRid); c.emit({0x83,0xE8,DEFENCE_CONTACT_OFFSET});
    c.bind(sceneRid); c.bind(targetRid); c.emit({0x89,0x47,4});
    auto copy = [&](uint32_t from, uint32_t to, unsigned char source = 0x86) {
        c.emit({0x8B,source}); c.word(from); c.emit({0x89,0x87}); c.word(to);
    };
    for (const auto& field : std::array<std::array<uint32_t, 2>, 5>{{
        {offsetof(BladeMailbox, actorEntity), offsetof(ContactEventMailbox, entity)},
        {offsetof(BladeMailbox, state), offsetof(ContactEventMailbox, state)},
        {offsetof(BladeMailbox, sequence), offsetof(ContactEventMailbox, sequence)},
        {offsetof(BladeMailbox, phaseTick), offsetof(ContactEventMailbox, phaseTick)},
        {offsetof(BladeMailbox, spanMs), offsetof(ContactEventMailbox, spanMs)}
    }}) copy(BO + field[0], field[1]);
    for (uint32_t i = 0; i < 3; ++i)
        copy(offsetof(ActorMailbox, position) + i * 4, offsetof(ContactEventMailbox, sourcePosition) + i * 4);
    copy(offsetof(ActorMailbox, yaw), offsetof(ContactEventMailbox, sourceYaw));
    for (uint32_t i = 0; i < 3; ++i) {
        copy(BO + offsetof(BladeMailbox, velocity) + i * 4, offsetof(ContactEventMailbox, sourceVelocity) + i * 4);
        copy(BO + offsetof(BladeMailbox, baseVelocity) + i * 4, offsetof(ContactEventMailbox, baseVelocity) + i * 4);
        copy(BO + offsetof(BladeMailbox, tipVelocity) + i * 4, offsetof(ContactEventMailbox, tipVelocity) + i * 4);
    }
    copy(BO + offsetof(BladeMailbox, motionReady), offsetof(ContactEventMailbox, sourceVelocityReady));
    copy(BO + offsetof(BladeMailbox, airborne), offsetof(ContactEventMailbox, airborne));
    for (const auto& field : std::array<std::array<uint32_t, 2>, 3>{{
        {0, offsetof(ContactEventMailbox, tick)}, {16, offsetof(ContactEventMailbox, epoch)},
        {20, offsetof(ContactEventMailbox, epoch) + 4}
    }}) {
        c.emit({0xA1}); c.word(frame + field[0]); c.emit({0x89,0x87}); c.word(field[1]);
    }
    c.emit({0x83,0x7F,4,0}); const auto scene = c.branch(0x84);
    c.emit({0x8B,0x47,4,0x48,0x69,0xC8}); c.word(sizeof(ActorMailbox));
    c.emit({0x81,0xC1}); c.word(batch);
    for (const auto& field : std::array<std::array<uint32_t, 2>, 4>{{
        {offsetof(BladeMailbox, actorEntity), offsetof(ContactEventMailbox, targetEntity)},
        {offsetof(BladeMailbox, state), offsetof(ContactEventMailbox, targetState)},
        {offsetof(BladeMailbox, sequence), offsetof(ContactEventMailbox, targetSequence)},
        {offsetof(BladeMailbox, phaseTick), offsetof(ContactEventMailbox, targetPhaseTick)}
    }}) copy(BO + field[0], field[1], 0x81);
    for (uint32_t i = 0; i < 3; ++i)
        copy(offsetof(ActorMailbox, position) + i * 4, offsetof(ContactEventMailbox, targetPosition) + i * 4, 0x81);
    copy(offsetof(ActorMailbox, yaw), offsetof(ContactEventMailbox, targetYaw), 0x81);
    for (uint32_t i = 0; i < 3; ++i)
        copy(BO + offsetof(BladeMailbox, velocity) + i * 4, offsetof(ContactEventMailbox, targetVelocity) + i * 4, 0x81);
    copy(BO + offsetof(BladeMailbox, motionReady), offsetof(ContactEventMailbox, targetVelocityReady), 0x81);
    copy(BO + offsetof(BladeMailbox, airborne), offsetof(ContactEventMailbox, targetAirborne), 0x81);
    const auto targetDone = c.jump();
    c.bind(scene);
    for (uint32_t offset : std::array<uint32_t, 13>{offsetof(ContactEventMailbox, targetEntity), offsetof(ContactEventMailbox, targetState),
        offsetof(ContactEventMailbox, targetSequence), offsetof(ContactEventMailbox, targetPhaseTick),
        offsetof(ContactEventMailbox, targetPosition), offsetof(ContactEventMailbox, targetPosition) + 4,
        offsetof(ContactEventMailbox, targetPosition) + 8, offsetof(ContactEventMailbox, targetYaw),
        offsetof(ContactEventMailbox, targetVelocity), offsetof(ContactEventMailbox, targetVelocity) + 4,
        offsetof(ContactEventMailbox, targetVelocity) + 8, offsetof(ContactEventMailbox, targetVelocityReady),
        offsetof(ContactEventMailbox, targetAirborne)}) {
        c.emit({0xC7,0x87}); c.word(offset); c.word(0);
    }
    c.bind(targetDone);
    c.emit({0x56,0x8B,0xF5,0x81,0xC7}); c.word(offsetof(ContactEventMailbox, hit));
    c.emit({0xB9}); c.word(sizeof(BladeContactMailbox) / 4); c.emit({0xF3,0xA5,0x5E});
    c.emit({0xFF,0x05}); c.word(frame + offsetof(ContactFrameMailbox, count));
    const auto stored = c.jump();
    c.bind(full); c.emit({0xFF,0x05}); c.word(frame + offsetof(ContactFrameMailbox, dropped));
    c.bind(stored); c.bind(noContact);
    c.emit({0x83,0xC5,sizeof(BladeContactMailbox),0x43,0x83,0xFB,MAX_BLADE_CONTACTS});
    c.bind(c.branch(0x82), contactLoop);
    c.bind(noTrace); c.bind(noActor);
    c.emit({0x81,0xC6}); c.word(sizeof(ActorMailbox)); c.emit({0x42,0x83,0xFA,static_cast<uint8_t>(MAX_WORLD_ACTORS + 1)});
    c.bind(c.branch(0x82), actorLoop);
    c.emit({0x61,0xC3});
    if (c.bytes.size() > LIVE_ACTORS_OFFSET - CONTACT_CODE_OFFSET)
        throw std::runtime_error("contact journal hook overflow");
    return c.bytes;
}
}
