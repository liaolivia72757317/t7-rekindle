#include "../../src/Runtime/launcher/TrainingHud.h"
#include "TrainingHudFixture.h"
#include <iostream>

namespace {
void require(bool value, const char* message) {
    if (!value) throw std::runtime_error(message);
}
void reject(const std::string& source) {
    bool rejected = false;
    try { t7r::hideTrainingMatchHud(source); }
    catch (const std::runtime_error&) { rejected = true; }
    require(rejected, "invalid training HUD resource was accepted");
}
}

bool verifyTrainingHud() {
    try {
        const auto source = trainingHudFixture();
        const auto expected = trainingHudFixture(false);
        require(t7r::hideTrainingMatchHud(source) == expected,
            "training must hide only the match HUD and preserve native training/dialogue/input actions");
        require(t7r::hideTrainingMatchHud(expected) == expected, "training HUD transform is not idempotent");
        require(t7r::isTrainingHudResourcePath(t7r::trainingHudResourcePath()), "training path not recognized");
        auto alternate = t7r::trainingHudResourcePath();
        alternate.replace(0, 14, "..\\DATA\\BTREE\\");
        require(t7r::isTrainingHudResourcePath(alternate), "training path normalization failed");
        require(!t7r::isTrainingHudResourcePath(t7r::trainingHudResourcePath() + ".bak")
            && !t7r::isTrainingHudResourcePath(t7r::resourceXml::encoded(L"../data/btree/流程_游戏中.btree")),
            "ordinary battle flow must stay unchanged");
        reject(""); reject("<BTree Version=\"4\"/>"); reject(source + '\0');
        reject(source.substr(0, source.size() - 8));
        auto invalid = source;
        invalid.replace(invalid.find("Value=\"true\""), 12, "Value=\"unknown\""); reject(invalid);
        invalid = source;
        const auto param = t7r::resourceXml::encoded(L"<Param Name=\"是否可见\" Type=\"bool\" Value=\"true\"/>");
        invalid.insert(invalid.find(param), param); reject(invalid);
        std::cout << "Training HUD visibility, native action preservation and path isolation passed\n";
        return true;
    } catch (const std::exception& error) {
        std::cerr << "Training HUD: " << error.what() << '\n'; return false;
    }
}
