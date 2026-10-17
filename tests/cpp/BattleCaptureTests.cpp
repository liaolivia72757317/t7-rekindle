#include "../../src/Runtime/bridge/BattleCapture.h"
#include <fstream>
#include <iostream>
#include <iterator>
#include <stdexcept>
#include <string>

namespace {
void require(bool condition, const char* message) {
    if (!condition) throw std::runtime_error(message);
}

std::string readFile(const t7r::fs::path& path) {
    std::ifstream input(path, std::ios::binary);
    require(input.good(), "capture output missing");
    return {std::istreambuf_iterator<char>(input), std::istreambuf_iterator<char>()};
}

void verifyLine(const std::string& line, std::initializer_list<const char*> fragments) {
    for (const auto* fragment : fragments)
        require(line.find(fragment) != std::string::npos, fragment);
}
}

bool verifyBattleCapture() {
    namespace fs = t7r::fs;
    const auto root = fs::temp_directory_path()
        / (L"t7r-battle-capture-" + std::to_wstring(GetCurrentProcessId()) + L"-" + std::to_wstring(GetTickCount64()));
    const auto client = root / L"client" / L"bin";
    const auto package = root / L"package";
    const auto output = root / L"capture.jsonl";
    const auto incomplete = root / L"incomplete.jsonl";
    try {
        require(fs::create_directories(client), "capture client fixture directory");
        require(fs::create_directory(package), "capture package fixture directory");
        t7r::WorldSnapshot world;
        world.ready = world.contactsReady = true;
        world.instanceStartedAt = 1000;
        world.sampledAt = 2000;
        world.contactFrames = 3;
        world.contactBatchId = 7;
        world.actors[0].ready = true;
        world.actors[0].entity = 101;
        world.actors[0].position = {1, 2, 3};
        world.actors[0].heading = 90;
        world.actors[0].forwardAttemptAt = 1995;
        world.actors[19].ready = true;
        world.actors[19].entity = 120;
        world.actors[19].forwardAttemptAt = 1500;
        t7r::BladeContactEvent contact;
        contact.actorId = 1;
        contact.hit.targetId = 20;
        contact.hit.guardVolume = true; contact.hit.defenceWeaponId = 701;
        contact.hit.time = .25f;
        contact.hit.along = .75f;
        contact.hit.contact.material = 2;
        contact.hit.contact.position = {1, 2, 3};
        contact.instanceStartedAt = 1000;
        contact.occurredAt = 1999.5;
        contact.targetObservedAt = 2001; contact.sourceObservedAt = 2002;
        world.contacts.push_back(contact);
        {
            t7r::bridge::BattleCapture capture(output, client, package);
            capture.record(world);
            world.instanceStartedAt = 3000;
            world.sampledAt = 4000;
            world.contactBatchId = 1;
            world.contacts.clear();
            world.actors[0].forwardAttemptAt = 0;
            capture.record(world);
            capture.stop();
        }
        const auto text = readFile(output);
        const auto first = text.find('\n');
        const auto second = text.find('\n', first + 1);
        const auto third = text.find('\n', second + 1);
        require(first != std::string::npos && second != std::string::npos && third != std::string::npos,
            "capture must contain start, samples and end");
        verifyLine(text.substr(0, first), {"\"type\":\"captureStart\"", "\"sourceId\":1",
            "\"actorId\":1,\"clientRid\":1", "\"actorId\":20,\"clientRid\":20", "\"guardGeometry\":{\"shape\":\"frontObb\"", "\"halfExtents\":[0.15,0.6,0.9]"});
        verifyLine(text.substr(first + 1, second - first - 1), {"\"type\":\"sample\"",
            "\"epoch\":1000", "\"worldBatchId\":1", "\"contactBatchId\":7",
            "\"sampledAtUptimeMs\":2000", "\"contactThroughAtUptimeMs\":2000",
            "\"contactFrames\":3", "\"forwardAttemptAtUptimeMs\":1995,\"attemptedForward\":true,\"freshForwardAttempt\":true",
            "\"forwardAttemptAtUptimeMs\":1500,\"attemptedForward\":true,\"freshForwardAttempt\":false",
            "\"targetId\":20", "\"contactOrigin\":\"guardVolume\"", "\"defenceWeaponId\":701", "\"targetObservedAtUptimeMs\":2001", "\"sourceObservedAtUptimeMs\":2002", "\"occurredAtUptimeMs\":1999.5", "\"sweepTime\":0.25"});
        verifyLine(text.substr(second + 1, third - second - 1), {"\"epoch\":3000",
            "\"worldBatchId\":1", "\"contactBatchId\":1", "\"attemptedForward\":false,\"freshForwardAttempt\":false"});
        verifyLine(text.substr(third + 1), {"\"type\":\"captureEnd\"", "\"complete\":true"});

        bool rejected = false;
        try { t7r::bridge::BattleCapture existing(output, client, package); }
        catch (const std::exception&) { rejected = true; }
        require(rejected && readFile(output) == text, "existing capture was overwritten");
        rejected = false;
        try { t7r::bridge::BattleCapture insideClient(client / L"capture.jsonl", client, package); }
        catch (const std::exception&) { rejected = true; }
        require(rejected, "capture accepted a client output path");
        rejected = false;
        try { t7r::bridge::BattleCapture insidePackage(package / L"capture.jsonl", client, package); }
        catch (const std::exception&) { rejected = true; }
        require(rejected, "capture accepted a package output path");

        world.droppedContacts = 1;
        {
            t7r::bridge::BattleCapture capture(incomplete, client, package);
            rejected = false;
            try { capture.record(world); } catch (const std::exception&) { rejected = true; }
            require(rejected, "dropped native contacts were silently accepted");
            rejected = false;
            try { capture.stop(); } catch (const std::exception&) { rejected = true; }
            require(rejected, "incomplete capture stop was reported successful");
        }
        verifyLine(readFile(incomplete), {"\"droppedContacts\":1", "\"complete\":false"});
        fs::remove(output);
        fs::remove(incomplete);
        fs::remove(package);
        fs::remove(client);
        fs::remove(client.parent_path());
        fs::remove(root);
        return true;
    } catch (const std::exception& error) {
        std::cerr << "battle capture: " << error.what() << '\n';
        return false;
    }
}
