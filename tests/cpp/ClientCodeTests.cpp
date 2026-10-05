#include "../../src/Runtime/launcher/ClientCode.h"
#include "../../src/Runtime/launcher/ClientImage.h"
#include <iostream>

bool verifyClientCode() {
    try {
        bool baselineRejected = false;
        try { t7::recoverClientCode(t7::Bytes{'M', 'Z'}); }
        catch (const std::runtime_error& error) {
            baselineRejected = std::string(error.what()) == "unsupported client code baseline";
        }
        if (!baselineRejected) throw std::runtime_error("unknown client image accepted");
        bool cancelled = false;
        try { t7::recoverClientCode({}, [] { return true; }); }
        catch (const std::runtime_error& error) {
            cancelled = std::string(error.what()) == "client code preparation cancelled";
        }
        if (!cancelled) throw std::runtime_error("client code cancellation ignored");
        bool imageRejected = false;
        try { t7::recoverClientImage(t7::Bytes{'M', 'Z'}); }
        catch (const std::runtime_error& error) {
            imageRejected = std::string(error.what()) == "unsupported client code baseline";
        }
        if (!imageRejected) throw std::runtime_error("unknown image layout accepted");
        std::cout << "Client code baseline and cancellation cases passed\n";
        return true;
    } catch (const std::exception& error) {
        std::cerr << "Client code test failure: " << error.what() << '\n';
        return false;
    }
}
