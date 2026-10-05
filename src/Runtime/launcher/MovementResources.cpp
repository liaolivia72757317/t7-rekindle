#include "MovementResources.h"
#include "ClientResourceXml.h"
#include "../core/Common.h"
#include <algorithm>
#include <map>
#include <set>

namespace t7 {
namespace {
using namespace resourceXml;
std::string gates(uint32_t firstId) {
    return "<Node Type=\"CONDITION\" ID=\"" + std::to_string(firstId) + "\" Name=\"" + encoded(L"是否为本地单位") + "\"/>"
        + "<Node Type=\"CONDITION\" ID=\"" + std::to_string(firstId + 1) + "\" Name=\"" + encoded(L"是否为战斗角色") + "\"/>";
}
std::string router() {
    // The client binds lifecycle slots by Description, not by ID.
    return "<?xml version=\"1.0\" encoding=\"gb2312\"?><BTree Version=\"4\"><Node Type=\"SEL\" ID=\"1\">"
        "<Node Type=\"SEL\" ID=\"2\" Description=\"" + encoded(L"进入节点") + "\"/>"
        + "<Node Type=\"SEL\" ID=\"3\" Description=\"" + encoded(L"退出节点") + "\"/>"
        + "<Node Type=\"SEL\" ID=\"4\" Description=\"" + encoded(L"执行节点") + "\">"
        + "<Node Type=\"SEQ\" ID=\"5\" Event=\"INIT_FINISH\">" + gates(6)
        + "<Node Type=\"ACTION\" ID=\"8\" Name=\"" + encoded(L"切换行为树") + "\">"
        + "<Param Name=\"" + encoded(L"行为树") + "\" Type=\"str\" Value=\"" + encoded(L"移动_步兵_离线_慢") + "\"/>"
        + "<Param Name=\"" + encoded(L"切换槽位") + "\" Type=\"i32\" Value=\"-1\"/>"
        + "<Param Name=\"" + encoded(L"是否关闭大于切换槽位的行为树") + "\" Type=\"bool\" Value=\"false\"/>"
        + "</Node></Node></Node></Node></BTree>";
}
struct Edit { size_t begin, end; std::string text; };
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
    if (resource == MovementResource::Router) return router();
    auto edits = resource == MovementResource::Infantry ? infantry(nodes) : birth(source, nodes);
    std::sort(edits.begin(), edits.end(), [](const auto& a, const auto& b) { return a.begin > b.begin; });
    std::string result(source);
    for (const auto& edit : edits) result.replace(edit.begin, edit.end - edit.begin, edit.text);
    if (result.size() > source.size() + MOVEMENT_XML_RESERVE) invalid();
    return result;
}
}
