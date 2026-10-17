#include "TrainingHud.h"
#include "ClientResourceXml.h"

namespace t7r {
std::string trainingHudResourcePath() {
    return resourceXml::encoded(L"../data/btree/流程_训练关_游戏中.btree");
}

bool isTrainingHudResourcePath(std::string_view path) {
    std::string normalized(path);
    for (auto& c : normalized) {
        if (c == '\\') c = '/';
        else if (c >= 'A' && c <= 'Z') c = static_cast<char>(c + ('a' - 'A'));
    }
    return normalized == trainingHudResourcePath();
}

std::string hideTrainingMatchHud(std::string_view source) {
    using namespace resourceXml;
    if (source.empty() || source.size() > 4 * 1024 * 1024 || source.find('\0') != source.npos) invalid();
    const auto nodes = elements(source);
    if (nodes[0].name != "BTree" || attribute(nodes[0], "Version") != "4") invalid();
    std::vector<size_t> edits;
    bool found = false;
    for (size_t i = 0; i < nodes.size(); ++i) {
        const auto& node = nodes[i];
        if (node.name != "Param" || attribute(node, "Value") != "SCORE_GAME_UI") continue;
        if (node.parent == NO_PARENT || attribute(node, "Name") != encoded(L"名称")
            || attribute(node, "Type") != "str") invalid();
        const auto& action = nodes[node.parent];
        if (action.name != "Node" || attribute(action, "Type") != "ACTION"
            || attribute(action, "Name") != encoded(L"设置界面可见")) invalid();
        if (unique(nodes, node.parent, "Param", "Name", encoded(L"名称")) != i) invalid();
        const auto visible = unique(nodes, node.parent, "Param", "Name", encoded(L"是否可见"));
        const auto& param = nodes[visible];
        const auto value = attribute(param, "Value");
        if (attribute(param, "Type") != "bool" || (value != "true" && value != "false")
            || param.attributes.size() != 3 || param.content != param.end) invalid();
        found = true;
        if (value == "true") edits.push_back(visible);
    }
    if (!found) invalid();
    std::string result(source);
    // Keep the native training timer, results, dialogue and all input actions unchanged.
    for (auto it = edits.rbegin(); it != edits.rend(); ++it) {
        const auto& node = nodes[*it];
        result.replace(node.begin, node.end - node.begin,
            "<Param Name=\"" + encoded(L"是否可见") + "\" Type=\"bool\" Value=\"false\"/>");
    }
    return result;
}
}
