#include "StartupAnimation.h"
#include "ClientResourceXml.h"

namespace t7 {
namespace {
using namespace resourceXml;
size_t onlyChild(const std::vector<Element>& nodes, size_t parent, const char* tag) {
    const auto child = unique(nodes, parent, tag);
    for (size_t i = 0; i < nodes.size(); ++i)
        if (nodes[i].parent == parent && i != child) invalid();
    return child;
}
void branch(const std::vector<Element>& nodes, size_t index, bool skip) {
    const auto& node = nodes[index];
    if (attribute(node, "Type") != "SEQ" || attribute(node, "ID") != (skip ? "43509" : "43507")
        || attribute(node, "Description") != encoded(skip ? L"跳过片头动画" : L"显示片头动画")) invalid();
    const auto action = onlyChild(nodes, index, "Node");
    if (attribute(nodes[action], "Type") != "ACTION"
        || attribute(nodes[action], "ID") != (skip ? "43510" : "43532")
        || attribute(nodes[action], "Name") != encoded(skip ? L"发送全局消息" : L"加载界面组")) invalid();
    const auto param = onlyChild(nodes, action, "Param");
    if (attribute(nodes[param], "Name") != encoded(skip ? L"消息名" : L"分类")
        || attribute(nodes[param], "Type") != (skip ? "event" : "str")
        || attribute(nodes[param], "Value") != (skip ? "GeASEventTitleMovieDone" : "TITLEMOVIE")
        || nodes[param].content != nodes[param].end) invalid();
}
}

std::string startupAnimationResourcePath() { return resourceXml::encoded(L"../data/btree/流程_登陆.btree"); }
bool isStartupAnimationResourcePath(std::string_view path) {
    std::string normalized(path);
    for (auto& c : normalized) {
        if (c == '\\') c = '/';
        else if (c >= 'A' && c <= 'Z') c = static_cast<char>(c + ('a' - 'A'));
    }
    return normalized == startupAnimationResourcePath();
}
std::string skipStartupAnimation(std::string_view source) {
    using namespace resourceXml;
    if (source.empty() || source.size() > 4 * 1024 * 1024 || source.find('\0') != source.npos) invalid();
    const auto nodes = elements(source);
    if (nodes[0].name != "BTree" || attribute(nodes[0], "Version") != "4") invalid();
    size_t selector = NO_PARENT;
    for (size_t i = 0; i < nodes.size(); ++i) {
        if (nodes[i].name != "Node" || attribute(nodes[i], "ID") != "43506") continue;
        if (selector != NO_PARENT) invalid();
        selector = i;
    }
    if (selector == NO_PARENT || attribute(nodes[selector], "Type") != "SEL"
        || attribute(nodes[selector], "Description") != encoded(L"是否显示片头动画")) invalid();
    const auto show = unique(nodes, selector, "Node", "ID", "43507");
    const auto skip = unique(nodes, selector, "Node", "ID", "43509");
    for (size_t i = 0; i < nodes.size(); ++i)
        if (nodes[i].parent == selector && i != show && i != skip) invalid();
    branch(nodes, show, false); branch(nodes, skip, true);
    if (skip < show) return std::string(source);
    // Preserve both original actions, whitespace and all other login branches.
    const auto& first = nodes[show]; const auto& second = nodes[skip];
    return std::string(source.substr(0, first.begin))
        + std::string(source.substr(second.begin, second.end - second.begin))
        + std::string(source.substr(first.end, second.begin - first.end))
        + std::string(source.substr(first.begin, first.end - first.begin))
        + std::string(source.substr(second.end));
}
}
