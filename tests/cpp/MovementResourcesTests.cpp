#include "../../src/Runtime/launcher/MovementResources.h"
#include "../../src/Runtime/core/Common.h"
#include <iostream>

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

        const auto router = t7::transformMovementResource(Resource::Router, "<BTree Version=\"4\"/>");
        const auto lifecycle = gbk(L"<Node Type=\"SEL\" ID=\"1\">"
            L"<Node Type=\"SEL\" ID=\"2\" Description=\"进入节点\"/>"
            L"<Node Type=\"SEL\" ID=\"3\" Description=\"退出节点\"/>"
            L"<Node Type=\"SEL\" ID=\"4\" Description=\"执行节点\">"
            L"<Node Type=\"SEQ\" ID=\"5\" Event=\"INIT_FINISH\">");
        require(router.find(lifecycle) != std::string::npos,
                "router lifecycle selectors need GBK Description labels, not just IDs");
        for (auto token : {gbk(L"是否为本地单位"), gbk(L"是否为战斗角色"), gbk(L"移动_步兵_离线_慢"), std::string("INIT_FINISH")})
            require(router.find(token) != std::string::npos, "router lacks local battle gate or original tree");
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
