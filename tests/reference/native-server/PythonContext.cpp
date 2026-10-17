#include "PythonContext.h"
#include <cmath>

namespace t7r {
using namespace python;
namespace {
PyObject* number(double value) {
    if (!std::isfinite(value)) throw std::runtime_error("non-finite world coordinate");
    return PyFloat_FromDouble(value);
}
PyObject* vector(const std::array<float, 3>& value) {
    for (auto item : value) if (!std::isfinite(item)) throw std::runtime_error("non-finite world vector");
    return Py_BuildValue("[fff]", value[0], value[1], value[2]);
}
Object terrainValue(const TerrainContact& contact) {
    auto value = checked(PyDict_New());
    put(value.get(), "ready", PyBool_FromLong(contact.ready ? 1 : 0));
    put(value.get(), "hit", PyBool_FromLong(contact.hit ? 1 : 0));
    put(value.get(), "material", PyLong_FromLong(contact.material));
    put(value.get(), "position", vector(contact.position));
    put(value.get(), "normal", vector(contact.normal));
    put(value.get(), "direction", vector(contact.direction));
    return value;
}
Object bladeValue(const BladeTrace& blade) {
    auto value = checked(PyDict_New());
    put(value.get(), "ready", PyBool_FromLong(blade.ready ? 1 : 0));
    put(value.get(), "spanMs", PyLong_FromUnsignedLong(blade.spanMs));
    put(value.get(), "length", number(blade.length));
    put(value.get(), "base", vector(blade.base));
    put(value.get(), "tip", vector(blade.tip));
    auto contacts = checked(PyList_New(0));
    for (const auto& hit : blade.contacts) {
        if (!hit.contact.hit) continue;
        auto contact = terrainValue(hit.contact);
        put(contact.get(), "targetId", PyLong_FromUnsignedLong(hit.targetId));
        put(contact.get(), "time", number(hit.time));
        put(contact.get(), "along", number(hit.along));
        put(contact.get(), "bodyIndex", PyLong_FromLong(hit.bodyIndex));
        put(contact.get(), "defenceWeaponId", PyLong_FromUnsignedLong(hit.defenceWeaponId));
        put(contact.get(), "region", PyUnicode_FromString(bodyRegion(hit.region)));
        if (PyList_Append(contacts.get(), contact.get()) < 0) throw std::runtime_error(error());
    }
    put(value.get(), "contacts", contacts.release());
    return value;
}
Object contactJournal(const WorldSnapshot& world, bool includeContacts) {
    auto journal = checked(PyDict_New());
    put(journal.get(), "throughAt", PyLong_FromUnsignedLongLong(world.sampledAt));
    put(journal.get(), "frames", PyLong_FromUnsignedLong(world.contactFrames));
    put(journal.get(), "dropped", PyLong_FromUnsignedLong(world.droppedContacts));
    put(journal.get(), "batchId", PyLong_FromUnsignedLong(world.contactBatchId));
    put(journal.get(), "damageEvidenceVersion", PyLong_FromLong(2));
    auto events = checked(PyList_New(0));
    if (includeContacts) for (const auto& event : world.contacts) {
        auto value = terrainValue(event.hit.contact);
        for (const auto& field : std::initializer_list<std::pair<const char*, uint32_t>>{
            {"actorId", event.actorId}, {"targetId", event.hit.targetId}, {"entity", event.entity},
            {"targetEntity", event.targetEntity}, {"state", event.state}, {"seq", event.sequence},
            {"targetState", event.targetState}, {"targetSeq", event.targetSequence}})
            put(value.get(), field.first, PyLong_FromUnsignedLong(field.second));
        put(value.get(), "phaseAt", PyLong_FromUnsignedLongLong(event.phaseAt));
        put(value.get(), "targetPhaseAt", PyLong_FromUnsignedLongLong(event.targetPhaseAt));
        put(value.get(), "occurredAt", number(event.occurredAt));
        put(value.get(), "along", number(event.hit.along));
        put(value.get(), "sourcePosition", vector(event.sourcePosition));
        put(value.get(), "targetPosition", vector(event.targetPosition));
        put(value.get(), "sourceHeading", number(event.sourceHeading));
        put(value.get(), "targetHeading", number(event.targetHeading));
        put(value.get(), "bodyIndex", PyLong_FromLong(event.hit.bodyIndex));
        put(value.get(), "defenceWeaponId", PyLong_FromUnsignedLong(event.hit.defenceWeaponId));
        put(value.get(), "region", PyUnicode_FromString(bodyRegion(event.hit.region)));
        put(value.get(), "sourceVelocity", vector(event.sourceVelocity));
        put(value.get(), "targetVelocity", vector(event.targetVelocity));
        put(value.get(), "weaponVelocity", vector(event.weaponVelocity));
        put(value.get(), "velocityReady", PyBool_FromLong(event.velocityReady ? 1 : 0));
        put(value.get(), "airborne", PyBool_FromLong(event.airborne ? 1 : 0));
        put(value.get(), "targetAirborne", PyBool_FromLong(event.targetAirborne ? 1 : 0));
        if (PyList_Append(events.get(), value.get()) < 0) throw std::runtime_error(error());
    }
    put(journal.get(), "events", events.release());
    return journal;
}
Object dashValue(const DashAttackProfile& profile) {
    auto value = checked(PyDict_New());
    put(value.get(), "enabled", PyBool_FromLong(profile.enabled ? 1 : 0));
    put(value.get(), "weaponId", PyLong_FromLong(profile.weaponId));
    put(value.get(), "weaponCategory", PyLong_FromLong(profile.weaponCategory));
    for (const auto& entry : std::initializer_list<std::pair<const char*, uint32_t>>{
        {"durationMs", profile.durationMs}, {"moveMs", profile.moveMs}, {"speedMilli", profile.speedMilli},
        {"contactStartMs", profile.contactStartMs}, {"contactEndMs", profile.contactEndMs}, {"reboundMs", profile.reboundMs}})
        put(value.get(), entry.first, PyLong_FromUnsignedLong(entry.second));
    put(value.get(), "durationSource", PyUnicode_FromString(profile.durationSource.c_str()));
    put(value.get(), "animationPath", PyUnicode_FromString(profile.animationPath.c_str()));
    return value;
}
Object snapshotValue(const WorldSnapshot& world) {
    if (world.contacts.size() > 8192) throw std::runtime_error("native contact journal exceeds capacity");
    auto snapshot = checked(PyDict_New());
    put(snapshot.get(), "ready", PyBool_FromLong(world.ready ? 1 : 0));
    put(snapshot.get(), "sampledAt", PyLong_FromUnsignedLongLong(world.sampledAt));
    put(snapshot.get(), "instanceStartedAt", PyLong_FromUnsignedLongLong(world.instanceStartedAt));
    if (world.contactsReady) put(snapshot.get(), "contactJournal", contactJournal(world, false).release());
    put(snapshot.get(), "player", vector(world.player));
    put(snapshot.get(), "bot", vector(world.bot));
    put(snapshot.get(), "playerHeading", number(world.playerHeading));
    put(snapshot.get(), "botHeading", number(world.botHeading));
    auto allActors = checked(PyDict_New());
    auto blades = checked(PyDict_New());
    for (size_t rid = 1; rid <= world.actors.size(); ++rid) {
        const auto& actor = world.actors[rid - 1];
        put(blades.get(), std::to_string(rid).c_str(), bladeValue(actor.blade).release());
        auto entry = checked(PyDict_New());
        put(entry.get(), "ready", PyBool_FromLong(actor.ready ? 1 : 0));
        put(entry.get(), "position", vector(actor.position));
        put(entry.get(), "heading", number(actor.heading));
        put(entry.get(), "targetId", PyLong_FromUnsignedLong(actor.targetId));
        put(entry.get(), "entity", PyLong_FromUnsignedLong(actor.entity));
        put(entry.get(), "state", PyLong_FromUnsignedLong(actor.state));
        put(entry.get(), "seq", PyLong_FromUnsignedLong(actor.sequence));
        put(entry.get(), "phaseAt", PyLong_FromUnsignedLongLong(actor.phaseAt));
        auto probes = checked(PyDict_New());
        for (size_t i = 0; i < actor.terrain.size(); ++i)
            put(probes.get(), i == 0 ? "forward" : "target", terrainValue(actor.terrain[i]).release());
        put(entry.get(), "terrain", probes.release());
        auto targets = checked(PyDict_New());
        for (size_t target = 0; target < actor.targetTerrain.size(); ++target)
            if (actor.targetTerrain[target].ready)
                put(targets.get(), std::to_string(target + 1).c_str(), terrainValue(actor.targetTerrain[target]).release());
        put(entry.get(), "targetTerrain", targets.release());
        put(allActors.get(), std::to_string(rid).c_str(), entry.release());
    }
    put(snapshot.get(), "actors", allActors.release());
    put(snapshot.get(), "blades", blades.release());
    return snapshot;
}
}
PythonContext::PythonContext(const Config& config) {
    auto dict = checked(PyDict_New());
    put(dict.get(), "advertisedAddress", PyUnicode_FromString(config.advertisedAddress.c_str()));
    put(dict.get(), "instancePort", PyLong_FromLong(config.ports[2]));
    put(dict.get(), "playerName", PyUnicode_FromString(config.playerName.c_str()));
    put(dict.get(), "runtimeMovement", PyBool_FromLong(config.runtimeMovement ? 1 : 0));
    put(dict.get(), "clientDirectory", PyUnicode_FromWideChar(config.clientDirectory.c_str(), -1));
    put(dict.get(), "dashAttack", dashValue(config.dashAttack).release());
    if (!config.playerProfilePath.empty())
        put(dict.get(), "playerProfilePath", PyUnicode_FromWideChar(config.playerProfilePath.c_str(), -1));
    config_ = std::move(dict);
}

Object PythonContext::make(uint64_t now, const std::vector<uint64_t>& connections,
                           const PublishedWorld* publication, bool includeContacts) {
    static const PublishedWorld empty;
    const auto& published = publication ? *publication : empty;
    const auto& snapshot = published.snapshot;
    rebuilt_ = !world_ || version_ != published.version || epoch_ != snapshot.instanceStartedAt;
    observation_.clear();
    Object nextWorld, nextContacts;
    if (rebuilt_) nextWorld = snapshotValue(snapshot);
    auto base = rebuilt_ ? nextWorld.get() : world_.get();
    if (includeContacts && snapshot.contactsReady && (rebuilt_ || !worldWithContacts_)) {
        nextContacts = checked(PyDict_Copy(base));
        put(nextContacts.get(), "contactJournal", contactJournal(snapshot, true).release());
    }
    if (rebuilt_) {
        const auto& player = snapshot.actors[0];
        const std::array<uint64_t, 5> observed = snapshot.ready && player.ready
            ? std::array<uint64_t, 5>{snapshot.instanceStartedAt, player.entity, player.state, player.sequence, player.phaseAt}
            : std::array<uint64_t, 5>{};
        if (observed != observed_ && player.ready && snapshot.ready) {
            observation_ = "battle-native-state actor=1 epoch=" + std::to_string(snapshot.instanceStartedAt)
                + " state=" + std::to_string(player.state) + " seq=" + std::to_string(player.sequence)
                + " phaseAt=" + std::to_string(player.phaseAt) + " observedAt=" + std::to_string(snapshot.sampledAt)
                + " worldVersion=" + std::to_string(published.version);
        }
        world_ = std::move(nextWorld); worldWithContacts_.reset();
        version_ = published.version; epoch_ = snapshot.instanceStartedAt; observed_ = observed;
    }
    if (nextContacts) worldWithContacts_ = std::move(nextContacts);
    auto result = checked(PyDict_Copy(config_.get()));
    put(result.get(), "nowMs", PyLong_FromUnsignedLongLong(now));
    put(result.get(), "botWorld", Py_NewRef(includeContacts && worldWithContacts_ ? worldWithContacts_.get() : world_.get()));
    auto live = checked(PyList_New(static_cast<Py_ssize_t>(connections.size())));
    for (size_t i = 0; i < connections.size(); ++i) {
        auto connection = checked(PyLong_FromUnsignedLongLong(connections[i]));
        PyList_SET_ITEM(live.get(), static_cast<Py_ssize_t>(i), connection.release());
    }
    put(result.get(), "connections", live.release());
    return result;
}
}
