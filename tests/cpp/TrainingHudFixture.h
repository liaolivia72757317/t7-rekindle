#pragma once
#include "../../src/Runtime/launcher/ClientResourceXml.h"

inline std::string trainingHudFixture(bool showMatchHud = true) {
    using t7r::resourceXml::encoded;
    const auto action = [](const char* panel, bool visible) {
        return encoded(L"<Node Type=\"ACTION\" Name=\"设置界面可见\"><Param Name=\"名称\" Type=\"str\" Value=\"")
            + panel + encoded(L"\"/><Param Name=\"是否可见\" Type=\"bool\" Value=\"")
            + (visible ? "true" : "false") + "\"/></Node>";
    };
    return "<BTree Version=\"4\"><Node Type=\"SEL\"><Node Type=\"SEQ\">"
        + action("SCORE_GAME_UI", showMatchHud) + action("TRAIN_TIP_UI", true)
        + action("NPCDIALOG_UI", true) + action("WEAPON_UI", true)
        + "</Node><Node Type=\"SEQ\">" + action("SCORE_GAME_UI", false)
        + "</Node><Node Event=\"CMD_ESC\" Type=\"SEL\"/></Node></BTree>";
}
