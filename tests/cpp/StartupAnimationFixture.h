#pragma once
#include <string>

inline std::string startupAnimationFixture() {
    const wchar_t* text = L"<?xml version=\"1.0\" encoding=\"gb2312\"?>\r\n<BTree Version=\"4\">"
        L"<Node Type=\"SEQ\" ID=\"1\" Event=\"GeRecvLogin\">"
        L"<Node Type=\"ACTION\" ID=\"2\" Name=\"fixture-before\"/>"
        L"<Node Type=\"SEL\" ID=\"43506\" Description=\"是否显示片头动画\">\r\n"
        L"<Node Type=\"SEQ\" ID=\"43507\" Description=\"显示片头动画\">"
        L"<Node Type=\"ACTION\" ID=\"43532\" Name=\"加载界面组\">"
        L"<Param Name=\"分类\" Type=\"str\" Value=\"TITLEMOVIE\"/></Node></Node>\r\n"
        L"<Node Type=\"SEQ\" ID=\"43509\" Description=\"跳过片头动画\">"
        L"<Node Type=\"ACTION\" ID=\"43510\" Name=\"发送全局消息\">"
        L"<Param Name=\"消息名\" Type=\"event\" Value=\"GeASEventTitleMovieDone\"/></Node></Node>\r\n"
        L"</Node><Node Type=\"ACTION\" ID=\"3\" Name=\"fixture-after\"/></Node></BTree>\r\n";
    const auto size = WideCharToMultiByte(936, WC_NO_BEST_FIT_CHARS, text, -1, nullptr, 0, nullptr, nullptr);
    std::string result(static_cast<size_t>(size), '\0');
    if (!size || WideCharToMultiByte(936, WC_NO_BEST_FIT_CHARS, text, -1,
            result.data(), size, nullptr, nullptr) != size) throw std::runtime_error("fixture encoding");
    result.pop_back(); return result;
}
