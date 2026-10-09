#include "../../src/Runtime/launcher/MovementResources.h"
#include "../../src/Runtime/core/Common.h"
#include "../../src/Runtime/launcher/ClientResourceXml.h"
#include "MovementTreeFixture.h"
#include <functional>
#include <iostream>
#include <set>

namespace {
void require(bool valid, const char* message) {
    if (!valid) throw std::runtime_error(message);
}
std::string gbk(const wchar_t* text) {
    const auto size = WideCharToMultiByte(936, WC_NO_BEST_FIT_CHARS, text, -1, nullptr, 0, nullptr, nullptr);
    std::string result(static_cast<size_t>(size), '\0');
    require(size && WideCharToMultiByte(936, WC_NO_BEST_FIT_CHARS, text, -1,
            result.data(), size, nullptr, nullptr) == size, "fixture encoding");
    result.pop_back(); return result;
}
void rejected(t7::MovementResource resource, const std::string& source) {
    bool failed = false;
    try { t7::transformMovementResource(resource, source); }
    catch (const std::runtime_error&) { failed = true; }
    require(failed, "changed resource structure was accepted");
}

std::vector<std::string> inputActions(const std::string& source, int playerState,
        bool local, bool battle, const std::string& key, bool pressed,
        const std::string& eventOverride = {}, bool jumpAvailable = true) {
    using namespace t7::resourceXml;
    const auto nodes = elements(source);
    std::vector<std::string> actions;
    std::function<bool(size_t)> execute = [&](size_t index) {
        const auto& node = nodes[index];
        const auto type = attribute(node, "Type"), name = attribute(node, "Name");
        if (type == "ACTION") {
            if (name == gbk(L"动画系统事件")) {
                const auto param = unique(nodes, index, "Param", "Name", gbk(L"事件名"));
                require(attribute(nodes[param], "Type") == "str", "animation event parameter type");
                actions.push_back("animation:" + attribute(nodes[param], "Value"));
            } else actions.push_back(name);
            return name != gbk(L"跳跃") || jumpAvailable;
        }
        if (type == "CONDITION") {
            if (name == gbk(L"是否为本地单位")) return local;
            if (name == gbk(L"是否为战斗角色")) return battle;
            if (name == gbk(L"本地玩家状态")) {
                const auto param = unique(nodes, index, "Param", "Name", gbk(L"玩家当前状态"));
                require(attribute(nodes[param], "Type") == "enum", "player state parameter type");
                return playerState == std::stoi(attribute(nodes[param], "Value"));
            }
            require(name == gbk(L"按键被按下") || name == gbk(L"按键被松开"), "unknown input condition");
            const auto param = unique(nodes, index, "Param", "Name", gbk(L"按键"));
            return key == attribute(nodes[param], "Value") && pressed == (name == gbk(L"按键被按下"));
        }
        require(type == "SEL" || type == "SEQ", "unknown input node type");
        for (size_t child = index + 1; child < nodes.size(); ++child) {
            if (nodes[child].parent != index || nodes[child].name != "Node") continue;
            const auto result = execute(child);
            if (type == "SEQ" && !result) return false;
            if (type == "SEL" && result) return true;
        }
        return type == "SEQ";
    };
    const auto event = eventOverride.empty() ? (pressed ? "GeEventKeyDown" : "GeEventKeyUp") : eventOverride;
    for (size_t i = 0; i < nodes.size(); ++i)
        if (attribute(nodes[i], "Event").find(event) != std::string::npos) execute(i);
    return actions;
}
}

bool verifyMovementResources() {
    using Resource = t7::MovementResource;
    try {
        const auto component = "<GeServerMovable><BTree Value=\"original\"/></GeServerMovable>";
        const auto physics = "<Collision Shape=\"fixture-shape\" Mask=\"7\"/><Camera Value=\"keep\"/>";
        const auto source = std::string("<?xml version=\"1.0\" encoding=\"gb2312\"?><EntSheet><Header>")
            + "<GeServerMovable><BTree Value=\"*.*\"/></GeServerMovable></Header>"
            + gbk(L"<Entity Name=\"武将\">") + component + physics + "</Entity>"
            + gbk(L"<Entity Name=\"女武将\">") + component + physics + "</Entity>"
            + "<Entity Name=\"npc\">" + component + physics + "</Entity></EntSheet>";
        const auto changed = t7::transformMovementResource(Resource::Infantry, source);
        require(changed.find("<GeOfflineMovable><BTree Value=\"*.*\"/></GeOfflineMovable>") != std::string::npos,
                "offline schema was not derived from the original movable schema");
        require(changed.find(std::string("<Entity Name=\"npc\">") + component + physics + "</Entity>") != std::string::npos,
                "non-hero components changed");
        const auto replacement = "<GeOfflineMovable><BTree Value=\"" + t7::movementResourcePath(Resource::Router)
            + "\"/></GeOfflineMovable>";
        for (auto name : {L"武将", L"女武将"})
            require(changed.find("<Entity Name=\"" + gbk(name) + "\">" + replacement + physics + "</Entity>") != std::string::npos,
                    "hero collision/camera components changed");
        rejected(Resource::Infantry, changed);
        rejected(Resource::Infantry, source.substr(0, source.size() - 1));
        rejected(Resource::Infantry, "<EntSheet><Header/></EntSheet>");
        rejected(Resource::Infantry, "<!DOCTYPE EntSheet><EntSheet/>");

        const auto birth = gbk(L"<BTree Version=\"4\"><Node Type=\"SEL\" ID=\"1\">"
            L"<Node Type=\"SEL\" ID=\"2\"><Node Type=\"SEQ\" ID=\"20\">"
            L"<Node Type=\"ACTION\" ID=\"21\" Name=\"设置步兵转向\"/>"
            L"<Node Type=\"ACTION\" ID=\"22\" Name=\"fixture-camera\"/>"
            L"</Node></Node><Node Type=\"SEL\" ID=\"3\"/></Node></BTree>");
        const auto turned = t7::transformMovementResource(Resource::Birth, birth);
        require(turned.find(gbk(L"Name=\"设置离线步兵转向\"")) != std::string::npos,
                "offline turn action missing");
        require(turned.find(gbk(L"Name=\"设置步兵转向\"")) != std::string::npos,
                "online turn fallback missing");
        require(turned.find("<Node Type=\"ACTION\" ID=\"22\" Name=\"fixture-camera\"/>") != std::string::npos,
                "birth camera action changed");
        for (auto name : {L"是否为本地单位", L"是否为战斗角色", L"步兵待机"})
            require(turned.find(gbk(name)) != std::string::npos, "birth scope or turn parameters missing");
        rejected(Resource::Birth, turned);
        rejected(Resource::Birth, "<BTree><Node ID=\"1\"/><Node ID=\"1\"/></BTree>");

        const auto movement = gbk(movementTreeFixture());
        const auto router = t7::transformMovementResource(Resource::Router, movement);
        const auto lifecycle = gbk(L"<Node Type=\"SEL\" ID=\"1\">"
            L"<Node Type=\"SEL\" ID=\"2\" Description=\"进入节点\"/>"
            L"<Node Type=\"SEL\" ID=\"3\" Description=\"退出节点\"/>"
            L"<Node Type=\"SEL\" ID=\"4\" Description=\"执行节点\">");
        require(router.find(lifecycle) != std::string::npos,
                "router lifecycle selectors need GBK Description labels, not just IDs");
        for (const auto& key : {"W", "Space", "Ctrl"}) {
            for (int state : {0, 1, 2, 3})
                require(inputActions(router, state, true, true, key, true).empty(),
                        "movement input ran before the player entered combat");
            require(inputActions(router, 4, false, true, key, true).empty(), "remote actor accepted local input");
            require(inputActions(router, 4, true, false, key, true).empty(), "non-battle actor accepted input");
        }
        require(inputActions(router, 4, true, true, "W", true) == std::vector<std::string>{"fixture-move"},
                "first combat movement input changed");
        require(inputActions(router, 4, true, true, "Space", true)
                    == std::vector<std::string>{gbk(L"跳跃"), "animation:Jump"},
                "jump must retain local physics and also start the model animation");
        require(inputActions(router, 4, true, true, "Space", false).empty(), "jump release replayed animation");
        require(inputActions(router, 4, true, true, "Space", true, {}, false) == std::vector<std::string>{gbk(L"跳跃")},
                "jump animation ran after the original jump action failed");
        require(inputActions(router, 4, true, true, "Ctrl", true) == std::vector<std::string>{"animation:Crouch"},
                "crouching still waits for a server movement response");
        require(inputActions(router, 2, true, true, "Ctrl", false).empty(), "preparation allowed standing input");
        require(inputActions(router, 4, true, true, "Ctrl", false) == std::vector<std::string>{"animation:EndCrouch"},
                "combat standing input was lost");
        require(inputActions(router, 4, true, true, "", false, "JUMP_LAND")
                    == std::vector<std::string>{"animation:EndJump", "fixture-land"},
                "landing must end the jump override and preserve the original landing actions");
        require(router.find("Value=\"JumpLand\"") == std::string::npos,
                "landing notification must not re-enter a persistent landing pose");
        require(inputActions(router, 2, true, true, "", false, "JUMP_LAND").empty(),
                "landing animation escaped the game phase gate");
        require(inputActions(router, 4, false, true, "", false, "JUMP_LAND").empty(),
                "remote actor accepted a local end-jump event");
        require(inputActions(router, 4, true, false, "", false, "JUMP_LAND").empty(),
                "non-battle actor accepted an end-jump event");
        require(router.find(gbk(L"请求进入下蹲")) == std::string::npos
                    && router.find(gbk(L"请求离开下蹲")) == std::string::npos,
                "offline crouch still dispatches online requests");
        require(router.find("Name=\"fixture-land\"") != std::string::npos, "original landing action was lost");
        require(router.find(gbk(L"切换行为树")) == std::string::npos, "router can switch into an ungated movement tree");
        std::set<std::string> ids;
        for (const auto& node : t7::resourceXml::elements(router))
            if (node.name == "Node") require(ids.insert(t7::resourceXml::attribute(node, "ID")).second, "duplicate movement node ID");
        rejected(Resource::Router, router);
        rejected(Resource::Router, "<BTree Version=\"4\"/>");
        for (const auto* action : {L"跳跃", L"请求进入下蹲", L"请求离开下蹲"}) {
            auto missing = movement;
            const auto name = gbk(action);
            missing.replace(missing.find(name), name.size(), "unsupported-action");
            rejected(Resource::Router, missing);
        }
        auto noLanding = movement;
        noLanding.replace(noLanding.find("JUMP_LAND"), std::string("JUMP_LAND").size(), "OTHER_EVENT");
        rejected(Resource::Router, noLanding);
        auto duplicateJump = movement;
        duplicateJump.replace(duplicateJump.find("fixture-move"), std::string("fixture-move").size(), gbk(L"跳跃"));
        rejected(Resource::Router, duplicateJump);
        auto exhaustedIds = movement;
        exhaustedIds.replace(exhaustedIds.find("ID=\"24\""), std::string("ID=\"24\"").size(), "ID=\"2147483639\"");
        rejected(Resource::Router, exhaustedIds);
        require(t7::movementResourceForPath("..\\DATA\\BTREE\\T7_RUNTIME_MOVEMENT.BTREE") == Resource::Router,
                "resource path normalization failed");
        require(t7::movementResourceForPath(t7::movementOfflineTreePath()) == Resource::None,
                "original movement tree must remain unchanged");
        require(t7::transformMovementResource(Resource::None, "not XML") == "not XML", "unrelated data changed");
        rejected(Resource::Router, std::string(t7::MOVEMENT_XML_LIMIT + 1, 'x'));
        return true;
    } catch (const std::exception& error) {
        std::cerr << "Movement resources: " << error.what() << '\n'; return false;
    }
}
