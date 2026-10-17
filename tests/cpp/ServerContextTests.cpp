#include "../../tests/reference/native-server/PythonHost.h"
#include "../../tests/reference/native-server/PythonContext.h"
#include <iostream>
#include <limits>

namespace {
using namespace t7r;
using namespace t7r::python;
void require(bool value, const char* message) {
    if (!value) throw std::runtime_error(message);
}
PyObject* world(const Object& context) { return field(context.get(), "botWorld"); }
PyObject* actors(const Object& context) { return field(world(context), "actors"); }
Py_ssize_t contacts(const Object& context) {
    return PyList_Size(field(field(world(context), "contactJournal"), "events"));
}
void verifyCache(PythonHost& host) {
    Config config;
    PythonContext cache(config);
    PublishedWorld publication;
    publication.version = 1;
    auto& snapshot = publication.snapshot;
    snapshot.ready = snapshot.contactsReady = snapshot.actors[0].ready = true;
    snapshot.sampledAt = 100; snapshot.instanceStartedAt = 50; snapshot.contactBatchId = 1;
    snapshot.actors[0].state = 308; snapshot.actors[0].sequence = 2;
    for (auto& actor : snapshot.actors)
        for (auto& probe : actor.targetTerrain) probe.ready = true;
    snapshot.contacts.push_back({});
    snapshot.contacts.back().hit.bodyIndex = 5; snapshot.contacts.back().hit.region = 1;
    snapshot.contacts.back().sourceVelocity = {1,0,0}; snapshot.contacts.back().targetVelocity = {-1,0,0};
    snapshot.contacts.back().weaponVelocity = {3,0,0};
    snapshot.contacts.back().velocityReady = snapshot.contacts.back().airborne = snapshot.contacts.back().targetAirborne = true;
    snapshot.contacts.back().hit.defenceWeaponId = 1030411;
    auto first = cache.make(100, {1}, &publication, false);
    require(cache.rebuilt() && contacts(first) == 0, "initial non-world context did not mask contacts");
    require(!cache.observation().empty(), "native state observation missing");
    auto second = cache.make(101, {2}, &publication, false);
    require(!cache.rebuilt() && cache.observation().empty(), "unchanged context rebuilt or relogged");
    require(first.get() != second.get() && world(first) == world(second), "event shell or world identity incorrect");
    require(PyLong_AsLong(field(first.get(), "nowMs")) == 100
        && PyLong_AsLong(field(second.get(), "nowMs")) == 101, "event time leaked between contexts");
    require(PyLong_AsLong(PyList_GetItem(field(second.get(), "connections"), 0)) == 2, "live connections were cached");
    auto batch = cache.make(102, {1}, &publication, true);
    const auto journal = field(world(batch), "contactJournal");
    const auto event = PyList_GetItem(field(journal, "events"), 0);
    require(PyLong_AsLong(field(journal, "damageEvidenceVersion")) == 2
        && PyLong_AsLong(field(event, "bodyIndex")) == 5 && PyUnicode_CompareWithASCIIString(field(event, "region"), "head") == 0
        && field(event, "velocityReady") == Py_True && field(event, "airborne") == Py_True
        && field(event, "targetAirborne") == Py_True && PyLong_AsLong(field(event, "defenceWeaponId")) == 1030411
        && PyFloat_AsDouble(PyList_GetItem(field(event, "weaponVelocity"), 0)) == 3., "damage evidence lost in Python context");
    require(contacts(batch) == 1 && contacts(first) == 0 && actors(batch) == actors(first), "contact view copied or mutated the world");
    auto repeated = cache.make(102, {1}, &publication, true);
    require(world(batch) == world(repeated), "contact batch was rebuilt");
    auto module = checked(PyImport_ImportModule("host_runtime"));
    auto encoded = host.context(config, 102, {1}, snapshot);
    auto decoded = checked(PyObject_CallMethod(module.get(), "decode", "s", encoded.c_str()));
    require(PyObject_RichCompareBool(decoded.get(), batch.get(), Py_EQ) == 1, "object context differs from legacy context");
    auto oldActors = checked(Py_NewRef(actors(first)));
    first.reset(); second.reset(); batch.reset(); repeated.reset(); decoded.reset();
    ++publication.version;
    snapshot.ready = false;
    auto invalidated = cache.make(103, {1}, &publication, false);
    require(cache.rebuilt() && field(world(invalidated), "ready") == Py_False, "unready snapshot did not replace cached world");
    require(Py_REFCNT(oldActors.get()) == 1, "old world retained after version replacement");
    ++publication.version; snapshot.instanceStartedAt = 200; snapshot.ready = true;
    auto nextEpoch = cache.make(104, {1}, &publication, false);
    require(PyLong_AsLong(field(world(nextEpoch), "instanceStartedAt")) == 200, "old epoch leaked");
    const auto accepted = cache.version();
    ++publication.version; snapshot.actors[0].heading = std::numeric_limits<float>::quiet_NaN();
    bool rejected = false;
    try { cache.make(105, {1}, &publication, false); } catch (const std::exception&) { rejected = true; }
    require(rejected && cache.version() == accepted, "invalid world partially replaced cached version");
    snapshot.actors[0].heading = 0;
    auto repaired = cache.make(106, {1}, &publication, false);
    require(cache.rebuilt(), "failed version was incorrectly marked consumed");
    for (unsigned i = 0; i < 100; ++i) {
        auto retained = checked(Py_NewRef(actors(repaired)));
        repaired.reset(); ++publication.version;
        repaired = cache.make(107 + i, {1}, &publication, true);
        require(Py_REFCNT(retained.get()) == 1, "world references accumulated across publications");
    }
    auto empty = cache.make(300, {}, nullptr, false);
    require(field(world(empty), "ready") == Py_False && cache.version() == 0, "empty publication retained previous world");
}
void verifyDispatch(PythonHost& host) {
    Config config;
    host.initializeContext(config);
    auto state = host.create(host.context(config, 1, {}));
    Event connected; connected.type = "connected"; connected.role = "login"; connected.connection = 1;
    const auto expected = host.dispatch(connected, state, host.context(config, 2, {1}));
    auto native = host.dispatchNative(connected, state, 2, {1}, nullptr);
    require(native.state == expected.state && native.send.empty() && native.phase == host.phase(native.state),
        "native connected transition differs");
    Event authenticated; authenticated.type = "authenticated"; authenticated.connection = 1;
    const auto legacyAuth = host.dispatch(authenticated, native.state, host.context(config, 3, {1}));
    auto auth = host.dispatchNative(authenticated, native.state, 3, {1}, nullptr);
    require(auth.state == legacyAuth.state && auth.timers.size() == 1 && !auth.timing.contextRebuilt,
        "authentication or cache hit differs");
    Event timer; timer.type = "timer"; timer.connection = 1; timer.serialized = auth.timers[0].event;
    const auto legacyTimer = host.dispatch(timer, auth.state, host.context(config, 1003, {1}));
    const auto nativeTimer = host.dispatchNative(timer, auth.state, 1003, {1}, nullptr);
    require(nativeTimer.state == legacyTimer.state && nativeTimer.send.size() == 1
        && nativeTimer.send[0].body == legacyTimer.send[0].body, "timer snapshot changed across object bridge");
    for (int i = 0; i < 3; ++i) {
        host.prepare();
        state = host.switchVersion(nativeTimer.state, false);
        require(state == nativeTimer.state, "reload changed state snapshot");
        state = host.switchVersion(state, true);
        Event closed; closed.type = "closed"; closed.connection = 1;
        const auto result = host.dispatchNative(closed, state, 1004, {}, nullptr);
        require(result.phase == "waiting" && !result.timing.contextRebuilt, "reload retained old revision or lost context");
    }
}
}

bool verifyServerContext(const t7r::fs::path& fixtureRoot) {
    try {
        for (int cycle = 0; cycle < 2; ++cycle) {
            t7r::PythonHost host(fixtureRoot, fixtureRoot / ("context-revisions-" + std::to_string(cycle)));
            verifyCache(host);
            verifyDispatch(host);
        }
        return true;
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return false;
    }
}
