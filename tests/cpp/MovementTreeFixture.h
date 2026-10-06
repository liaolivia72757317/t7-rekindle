#pragma once

inline const wchar_t* movementTreeFixture() {
    return L"<BTree Version=\"4\"><Node Type=\"SEL\" ID=\"1\">"
        L"<Node Type=\"SEL\" ID=\"2\" Description=\"进入节点\"/>"
        L"<Node Type=\"SEL\" ID=\"3\" Description=\"退出节点\"/>"
        L"<Node Type=\"SEL\" ID=\"4\" Description=\"执行节点\">"
        L"<Node Type=\"SEL\" ID=\"10\" Event=\"GeEventKeyDown;GeEventKeyUp\">"
        L"<Node Type=\"SEQ\" ID=\"11\"><Node Type=\"CONDITION\" ID=\"12\" Name=\"按键被按下\">"
        L"<Param Name=\"按键\" Type=\"str\" Value=\"W\"/></Node>"
        L"<Node Type=\"ACTION\" ID=\"13\" Name=\"fixture-move\"/></Node>"
        L"<Node Type=\"SEQ\" ID=\"14\"><Node Type=\"CONDITION\" ID=\"15\" Name=\"按键被按下\">"
        L"<Param Name=\"按键\" Type=\"str\" Value=\"Space\"/></Node>"
        L"<Node Type=\"ACTION\" ID=\"16\" Name=\"fixture-jump\"/></Node>"
        L"<Node Type=\"SEQ\" ID=\"17\"><Node Type=\"CONDITION\" ID=\"18\" Name=\"按键被按下\">"
        L"<Param Name=\"按键\" Type=\"str\" Value=\"Ctrl\"/></Node>"
        L"<Node Type=\"ACTION\" ID=\"19\" Name=\"fixture-crouch\"/></Node>"
        L"<Node Type=\"SEQ\" ID=\"20\"><Node Type=\"CONDITION\" ID=\"21\" Name=\"按键被松开\">"
        L"<Param Name=\"按键\" Type=\"str\" Value=\"Ctrl\"/></Node>"
        L"<Node Type=\"ACTION\" ID=\"22\" Name=\"fixture-stand\"/></Node></Node>"
        L"<Node Type=\"SEQ\" ID=\"23\" Event=\"JUMP_LAND\">"
        L"<Node Type=\"ACTION\" ID=\"24\" Name=\"fixture-land\"/></Node>"
        L"</Node></Node></BTree>";
}
