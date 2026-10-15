#pragma once
#include "../core/Common.h"

namespace t7r {
constexpr uint32_t TITLE_CODE_OFFSET = 0x1000, TITLE_CATALOGUE_OFFSET = 0x2000, TITLE_ROWS_OFFSET = 0x5000;
constexpr uint32_t TITLE_MAX_COUNT = 64, TITLE_MAX_ORIGINAL_COUNT = 1024, TITLE_ROW_SIZE = 548;
constexpr uint32_t TITLE_RECORD_SIZE = 164, TITLE_CATALOGUE_MAX_SIZE = 8 + TITLE_MAX_COUNT * TITLE_RECORD_SIZE;
constexpr uint32_t TITLE_PAGE_SIZE = (TITLE_ROWS_OFFSET + (TITLE_MAX_ORIGINAL_COUNT + TITLE_MAX_COUNT) * TITLE_ROW_SIZE + 0xFFF) & ~0xFFFu;
constexpr uint32_t TITLE_REFRESH_RVA = 0x5AB130, TITLE_REINDEX_RVA = 0x67FF0;
Bytes buildTitleHook(uint32_t address, uint32_t imageBase, uint32_t threadId);
bool validTitleCatalogue(const Bytes& bytes);
class TitleSettings {
public:
    void attach(HANDLE process, uint32_t address) { process_ = process; remote_ = address; }
    void clear() { process_ = nullptr; remote_ = 0; pending_ = false; retryAt_ = 0; desired_.clear(); sent_.clear(); applied_.clear(); }
    void update(const Bytes& bytes);
    void poll();
    bool applied() const { return !desired_.empty() && desired_ == applied_ && !pending_; }
private:
    HANDLE process_ = nullptr;
    uint32_t remote_ = 0;
    bool pending_ = false;
    uint64_t requestedAt_ = 0, retryAt_ = 0;
    Bytes desired_, sent_, applied_;
};
}
