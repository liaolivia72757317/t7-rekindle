#include "../../src/Runtime/core/Common.h"
#include "../../src/Runtime/launcher/StartupAnimation.h"
#include "StartupAnimationFixture.h"
#include <cstring>
#include <iostream>

namespace {
void require(bool result, const char* message) {
    if (!result) throw std::runtime_error(message);
}
void reject(const std::string& source) {
    bool failed = false;
    try { t7::skipStartupAnimation(source); }
    catch (const std::runtime_error&) { failed = true; }
    require(failed, "unknown startup animation structure accepted");
}
}

bool verifyStartupAnimation() {
    try {
        const auto source = startupAnimationFixture();
        const auto changed = t7::skipStartupAnimation(source);
        const auto show = source.find("<Node Type=\"SEQ\" ID=\"43507\"");
        const auto skip = source.find("<Node Type=\"SEQ\" ID=\"43509\"");
        const auto end = source.find("</Node></Node>", skip) + 14;
        const auto expected = source.substr(0, show) + source.substr(skip, end - skip)
            + "\r\n" + source.substr(show, skip - show - 2) + source.substr(end);
        require(changed == expected && changed.size() == source.size(), "only the two title movie branches should move");
        require(t7::skipStartupAnimation(changed) == changed, "startup animation transform is not idempotent");
        require(t7::isStartupAnimationResourcePath(t7::startupAnimationResourcePath()), "login resource path not recognized");
        auto alternatePath = t7::startupAnimationResourcePath();
        alternatePath.replace(0, 14, "..\\DATA\\BTREE\\");
        require(t7::isStartupAnimationResourcePath(alternatePath), "login resource path normalization failed");
        require(!t7::isStartupAnimationResourcePath("../data/btree/tutorial.btree")
            && !t7::isStartupAnimationResourcePath(t7::startupAnimationResourcePath() + ".bak"), "unrelated resource matched");
        reject(""); reject("<BTree Version=\"4\"/>"); reject(source.substr(0, source.size() - 12));
        reject(source + '\0'); reject(std::string(4 * 1024 * 1024 + 1, 'x'));
        for (const auto* marker : {"43506", "43507", "43509", "43532", "43510", "TITLEMOVIE", "GeASEventTitleMovieDone"}) {
            auto unknown = source; unknown.replace(unknown.find(marker), std::strlen(marker), "unknown"); reject(unknown);
        }
        auto duplicate = source;
        duplicate.insert(show, source.substr(show, skip - show)); reject(duplicate);
        auto conditional = source;
        conditional.insert(conditional.find('>', skip) + 1, "<Node Type=\"CONDITION\" ID=\"4\"/>"); reject(conditional);
        auto wrongVersion = source; wrongVersion.replace(wrongVersion.find("Version=\"4\""), 11, "Version=\"5\""); reject(wrongVersion);
        std::cout << "Startup animation branch selection and structure rejection passed\n";
        return true;
    } catch (const std::exception& error) {
        std::cerr << "Startup animation: " << error.what() << '\n'; return false;
    }
}
