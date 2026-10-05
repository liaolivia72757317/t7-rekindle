#pragma once
#include "../core/Common.h"
#include <map>
#include <string_view>

namespace t7::resourceXml {
constexpr size_t NO_PARENT = SIZE_MAX;
struct Element {
    size_t begin, content, closing, end, parent;
    std::string name;
    std::map<std::string, std::string> attributes;
};
inline std::string encoded(const wchar_t* value) {
    BOOL substituted = FALSE;
    const auto size = WideCharToMultiByte(936, WC_NO_BEST_FIT_CHARS, value, -1, nullptr, 0, nullptr, &substituted);
    if (!size || substituted) throw std::runtime_error("client resource encoding failed");
    std::string result(static_cast<size_t>(size), '\0');
    if (WideCharToMultiByte(936, WC_NO_BEST_FIT_CHARS, value, -1, result.data(), size, nullptr, &substituted) != size || substituted)
        throw std::runtime_error("client resource encoding failed");
    result.pop_back(); return result;
}
inline bool space(char c) { return c == ' ' || c == '\t' || c == '\r' || c == '\n'; }
[[noreturn]] inline void invalid() { throw std::runtime_error("client resource XML structure mismatch"); }

// Record byte ranges, not a reserialized DOM: untouched collision, camera and
// input definitions must reach the original parser byte-for-byte.
inline std::vector<Element> elements(std::string_view source) {
    std::vector<Element> result;
    std::vector<size_t> stack;
    size_t cursor = 0, roots = 0;
    auto skipSpace = [&] { while (cursor < source.size() && space(source[cursor])) ++cursor; };
    auto name = [&]() {
        const auto begin = cursor;
        while (cursor < source.size() && !space(source[cursor])
               && source[cursor] != '/' && source[cursor] != '>' && source[cursor] != '=') {
            if (source[cursor] == '<' || source[cursor] == '"' || source[cursor] == '\'') invalid();
            ++cursor;
        }
        if (cursor == begin) invalid();
        return std::string(source.substr(begin, cursor - begin));
    };
    while (cursor < source.size()) {
        if (source[cursor] != '<') {
            if (stack.empty() && !space(source[cursor])) invalid();
            ++cursor; continue;
        }
        const auto begin = cursor;
        if (source.substr(cursor, 4) == "<!--") {
            const auto end = source.find("-->", cursor + 4);
            if (end == source.npos) invalid();
            cursor = end + 3; continue;
        }
        if (source.substr(cursor, 5) == "<?xml" && result.empty()) {
            const auto end = source.find("?>", cursor + 5);
            if (end == source.npos) invalid();
            cursor = end + 2; continue;
        }
        if (++cursor == source.size() || source[cursor] == '!' || source[cursor] == '?') invalid();
        const bool closing = source[cursor] == '/';
        if (closing) ++cursor;
        auto tag = name();
        if (closing) {
            skipSpace();
            if (cursor == source.size() || source[cursor++] != '>' || stack.empty()
                || result[stack.back()].name != tag) invalid();
            result[stack.back()].closing = begin;
            result[stack.back()].end = cursor; stack.pop_back(); continue;
        }
        Element element{begin, 0, 0, 0, stack.empty() ? NO_PARENT : stack.back(), std::move(tag), {}};
        if (stack.empty() && ++roots != 1) invalid();
        while (cursor < source.size()) {
            const auto beforeSpace = cursor;
            skipSpace();
            if (cursor == source.size()) invalid();
            if (source[cursor] == '/' || source[cursor] == '>') break;
            if (cursor == beforeSpace) invalid();
            auto key = name(); skipSpace();
            if (cursor == source.size() || source[cursor++] != '=') invalid();
            skipSpace();
            if (cursor == source.size() || (source[cursor] != '\'' && source[cursor] != '"')) invalid();
            const auto quote = source[cursor++];
            const auto start = cursor;
            while (cursor < source.size() && source[cursor] != quote) {
                if (source[cursor] == '<') invalid();
                ++cursor;
            }
            if (cursor == source.size() || !element.attributes.emplace(std::move(key), source.substr(start, cursor - start)).second) invalid();
            ++cursor;
        }
        if (cursor == source.size()) invalid();
        const bool empty = source[cursor] == '/';
        if (empty) ++cursor;
        if (cursor == source.size() || source[cursor++] != '>') invalid();
        element.content = cursor;
        if (empty) { element.closing = begin; element.end = cursor; }
        result.push_back(std::move(element));
        if (!empty) stack.push_back(result.size() - 1);
    }
    if (!stack.empty() || roots != 1) invalid();
    return result;
}
inline std::string attribute(const Element& element, const char* key) {
    const auto found = element.attributes.find(key);
    return found == element.attributes.end() ? std::string{} : found->second;
}
inline size_t unique(const std::vector<Element>& nodes, size_t parent, const char* tag,
              const char* key = nullptr, const std::string& value = {}) {
    size_t result = NO_PARENT;
    for (size_t i = 0; i < nodes.size(); ++i) {
        if (nodes[i].parent != parent || nodes[i].name != tag || (key && attribute(nodes[i], key) != value)) continue;
        if (result != NO_PARENT) invalid();
        result = i;
    }
    if (result == NO_PARENT) invalid();
    return result;
}
}
