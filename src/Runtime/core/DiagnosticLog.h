#pragma once
#include "Common.h"

namespace t7 {
std::string formatDiagnosticRecord(const std::string& message, const char* level, const char* source);
std::string boundedDiagnosticRecord(const std::string& record);
void appendNativeLog(const fs::path& directory, const std::string& record);
}
