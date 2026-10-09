#include "MovementResources.h"
#include "ClientResourceXml.h"
#include "../core/Common.h"
#include <algorithm>
#include <map>
#include <set>

namespace t7 {
namespace {
using namespace resourceXml;
struct Edit { size_t begin, end; std::string text; };
std::string applyEdits(std::string_view source, std::vector<Edit> edits) {
    std::sort(edits.begin(), edits.end(), [](const auto& a, const auto& b) { return a.begin > b.begin; });
    std::string result(source);
    for (const auto& edit : edits) result.replace(edit.begin, edit.end - edit.begin, edit.text);
    return result;
}
std::string animationEvent(const std::string& id, const char* event) {
    return "<Node Type=\"ACTION\" ID=\"" + id + "\" Name=\"" + encoded(L"动画系统事件") + "\">"
        + "<Param Name=\"" + encoded(L"事件名") + "\" Type=\"str\" Value=\"" + event + "\"/></Node>";
}
std::vector<Edit> modelActions(std::string_view source, const std::vector<Element>& nodes) {
    uint32_t maxId = 0;
    for (const auto& node : nodes)
        if (node.name == "Node") maxId = std::max(maxId, static_cast<uint32_t>(std::stoul(attribute(node, "ID"))));
    auto nextId = [&]() {
        if (maxId == INT32_MAX) invalid();
        return std::to_string(++maxId);
    };
    int jumps = 0, crouches = 0, stands = 0, landings = 0;
    std::vector<Edit> edits;
    for (size_t i = 0; i < nodes.size(); ++i) {
        const auto& node = nodes[i];
        if (node.name != "Node") continue;
        if (attribute(node, "Event") == "JUMP_LAND") {
            // The router has placed the original landing sequence after its gates.
            const auto sequence = unique(nodes, i, "Node", "Type", "SEQ");
            // End the jump override instead of entering another persistent landing pose.
            edits.push_back({nodes[sequence].content, nodes[sequence].content, animationEvent(nextId(), "EndJump")});
            ++landings;
        }
        const auto name = attribute(node, "Name");
        if (name != encoded(L"跳跃") && name != encoded(L"请求进入下蹲") && name != encoded(L"请求离开下蹲")) continue;
        if (attribute(node, "Type") != "ACTION" || node.content != node.end) invalid();
        auto parent = node.parent;
        while (parent != NO_PARENT && attribute(nodes[parent], "Event").empty()) parent = nodes[parent].parent;
        if (parent == NO_PARENT || attribute(nodes[parent], "Event") != "GeEventKeyDown;GeEventKeyUp") invalid();
        if (name == encoded(L"跳跃")) {
            const auto sequenceId = nextId(), eventId = nextId();
            edits.push_back({node.begin, node.end, "<Node Type=\"SEQ\" ID=\"" + sequenceId + "\">"
                + std::string(source.substr(node.begin, node.end - node.begin))
                + animationEvent(eventId, "Jump") + "</Node>"});
            ++jumps;
        } else {
            const bool crouch = name == encoded(L"请求进入下蹲");
            edits.push_back({node.begin, node.end, animationEvent(attribute(node, "ID"), crouch ? "Crouch" : "EndCrouch")});
            if (crouch) ++crouches; else ++stands;
        }
    }
    if (jumps != 1 || crouches != 1 || stands != 1 || landings != 1) invalid();
    return edits;
}
std::string gates(uint32_t firstId) {
    return "<Node Type=\"CONDITION\" ID=\"" + std::to_string(firstId) + "\" Name=\"" + encoded(L"是否为本地单位") + "\"/>"
        + "<Node Type=\"CONDITION\" ID=\"" + std::to_string(firstId + 1) + "\" Name=\"" + encoded(L"是否为战斗角色") + "\"/>";
}
std::vector<Edit> router(std::string_view source, const std::vector<Element>& nodes) {
    const auto root = unique(nodes, 0, "Node", "ID", "1");
    for (const auto* label : {L"进入节点", L"退出节点"}) {
        const auto branch = unique(nodes, root, "Node", "Description", encoded(label));
        if (attribute(nodes[branch], "Type") != "SEL" || nodes[branch].content != nodes[branch].end) invalid();
    }
    const auto execute = unique(nodes, root, "Node", "Description", encoded(L"执行节点"));
    if (attribute(nodes[root], "Type") != "SEL" || attribute(nodes[execute], "Type") != "SEL") invalid();
    unique(nodes, execute, "Node", "Event", "GeEventKeyDown;GeEventKeyUp");
    std::set<uint32_t> ids;
    uint32_t maxId = 0;
    for (const auto& node : nodes) {
        if (node.name != "Node") continue;
        const auto value = attribute(node, "ID");
        if (value.empty() || value.size() > 10 || value.find_first_not_of("0123456789") != value.npos) invalid();
        const auto id = std::stoull(value);
        if (id > INT32_MAX || !ids.insert(static_cast<uint32_t>(id)).second
            || attribute(node, "Name") == encoded(L"本地玩家状态")
            || (!attribute(node, "Event").empty() && node.parent != execute)) invalid();
        maxId = std::max(maxId, static_cast<uint32_t>(id));
    }
    std::vector<Edit> edits;
    for (const auto& node : nodes) {
        if (node.parent != execute) continue;
        const auto type = attribute(node, "Type"), event = attribute(node, "Event");
        if (node.name != "Node" || (type != "SEL" && type != "SEQ") || event.empty()
            || event.find('"') != event.npos || node.content == node.end || maxId > INT32_MAX - 4) invalid();
        // Gate each event, not INIT_FINISH: otherwise the original offline tree
        // handles WASD, jump and crouch before GAME, independently of the server.
        const auto replacement = "<Node Type=\"SEQ\" ID=\"" + attribute(node, "ID") + "\" Event=\"" + event + "\">"
            + gates(maxId + 1)
            + "<Node Type=\"CONDITION\" ID=\"" + std::to_string(maxId + 3) + "\" Name=\"" + encoded(L"本地玩家状态") + "\">"
            + "<Param Name=\"" + encoded(L"玩家当前状态") + "\" Type=\"enum\" Value=\"4\"/></Node>"
            + "<Node Type=\"" + type + "\" ID=\"" + std::to_string(maxId + 4) + "\">"
            + std::string(source.substr(node.content, node.closing - node.content)) + "</Node></Node>";
        edits.push_back({node.begin, node.end, replacement});
        maxId += 4;
    }
    return edits;
}
std::vector<Edit> infantry(const std::vector<Element>& nodes) {
    const auto header = unique(nodes, 0, "Header");
    const auto schema = unique(nodes, header, "GeServerMovable");
    unique(nodes, schema, "BTree", "Value", "*.*");
    for (const auto& node : nodes) if (node.name == "GeOfflineMovable") invalid();
    std::vector<Edit> edits{{nodes[header].closing, nodes[header].closing,
        "<GeOfflineMovable><BTree Value=\"*.*\"/></GeOfflineMovable>"}};
    for (const auto* name : {L"武将", L"女武将"}) {
        const auto entity = unique(nodes, 0, "Entity", "Name", encoded(name));
        const auto movable = unique(nodes, entity, "GeServerMovable");
        unique(nodes, movable, "BTree");
        edits.push_back({nodes[movable].begin, nodes[movable].end,
            "<GeOfflineMovable><BTree Value=\"" + movementResourcePath(MovementResource::Router) + "\"/></GeOfflineMovable>"});
    }
    return edits;
}
std::vector<Edit> birth(std::string_view source, const std::vector<Element>& nodes) {
    std::set<uint32_t> ids;
    uint32_t maxId = 0;
    size_t action = NO_PARENT;
    for (size_t i = 0; i < nodes.size(); ++i) {
        const auto& node = nodes[i];
        if (node.name != "Node") continue;
        const auto value = attribute(node, "ID");
        if (value.empty() || value.size() > 10 || value.find_first_not_of("0123456789") != value.npos) invalid();
        const auto id = std::stoull(value);
        if (id > INT32_MAX - 6 || !ids.insert(static_cast<uint32_t>(id)).second) invalid();
        maxId = std::max(maxId, static_cast<uint32_t>(id));
        const auto name = attribute(node, "Name");
        if (name == encoded(L"设置离线步兵转向") || name == encoded(L"设置离线转向参数组")) invalid();
        if (name != encoded(L"设置步兵转向")) continue;
        if (action != NO_PARENT || attribute(node, "Type") != "ACTION" || node.content != node.end) invalid();
        action = i;
    }
    if (action == NO_PARENT) invalid();
    bool entering = false;
    for (auto parent = nodes[action].parent; parent != NO_PARENT; parent = nodes[parent].parent)
        if (nodes[parent].name == "Node" && attribute(nodes[parent], "ID") == "2") entering = true;
    if (!entering) invalid();
    const auto replacement = "<Node Type=\"SEL\" ID=\"" + std::to_string(maxId + 1) + "\">"
        + "<Node Type=\"SEQ\" ID=\"" + std::to_string(maxId + 2) + "\">" + gates(maxId + 3)
        + "<Node Type=\"ACTION\" ID=\"" + std::to_string(maxId + 5) + "\" Name=\"" + encoded(L"设置离线转向参数组") + "\">"
        + "<Param Name=\"" + encoded(L"参数组名称") + "\" Type=\"str\" Value=\"" + encoded(L"步兵待机") + "\"/></Node>"
        + "<Node Type=\"ACTION\" ID=\"" + std::to_string(maxId + 6) + "\" Name=\"" + encoded(L"设置离线步兵转向") + "\"/></Node>"
        + std::string(source.substr(nodes[action].begin, nodes[action].end - nodes[action].begin)) + "</Node>";
    return {{nodes[action].begin, nodes[action].end, replacement}};
}
}

std::string movementResourcePath(MovementResource resource) {
    switch (resource) {
    case MovementResource::Infantry: return encoded(L"../data/entsheet/步兵.esf");
    case MovementResource::Router: return "../data/btree/t7_runtime_movement.btree";
    case MovementResource::Birth: return encoded(L"../data/btree/武将_出生.btree");
    default: return {};
    }
}
std::string movementOfflineTreePath() { return encoded(L"../data/btree/移动_步兵_离线_慢.btree"); }
MovementResource movementResourceForPath(std::string_view path) {
    std::string normalized(path);
    for (auto& c : normalized) {
        if (c == '\\') c = '/';
        else if (c >= 'A' && c <= 'Z') c = static_cast<char>(c + ('a' - 'A'));
    }
    for (auto resource : {MovementResource::Infantry, MovementResource::Router, MovementResource::Birth})
        if (normalized == movementResourcePath(resource)) return resource;
    return MovementResource::None;
}
std::string transformMovementResource(MovementResource resource, std::string_view source) {
    if (resource == MovementResource::None) return std::string(source);
    if (source.empty() || source.size() > MOVEMENT_XML_LIMIT || source.find('\0') != source.npos) invalid();
    const auto nodes = elements(source);
    if (resource != MovementResource::Infantry && (nodes[0].name != "BTree" || attribute(nodes[0], "Version") != "4")) invalid();
    auto edits = resource == MovementResource::Infantry ? infantry(nodes)
        : resource == MovementResource::Router ? router(source, nodes) : birth(source, nodes);
    auto result = applyEdits(source, std::move(edits));
    if (resource == MovementResource::Router) result = applyEdits(result, modelActions(result, elements(result)));
    if (result.size() > source.size() + MOVEMENT_XML_RESERVE) invalid();
    return result;
}
}
