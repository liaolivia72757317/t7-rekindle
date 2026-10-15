#include "TitleSettings.h"
#include "RemoteImage.h"
#include <algorithm>
#include <cstring>

namespace t7r {
namespace {
class Code {
public:
    Bytes bytes;
    void emit(std::initializer_list<unsigned char> v) { bytes.insert(bytes.end(), v); }
    void word(uint32_t v) { for (unsigned i = 0; i < 32; i += 8) bytes.push_back(static_cast<unsigned char>(v >> i)); }
    size_t branch(unsigned char condition = 0) {
        if (condition) emit({0x0F, condition}); else emit({0xE9});
        auto p = bytes.size(); word(0); return p;
    }
    void bind(size_t p, size_t target) { auto d = static_cast<uint32_t>(target - p - 4); std::memcpy(bytes.data() + p, &d, 4); }
    void store(uint32_t at, uint32_t value) { emit({0xC7,0x05}); word(at); word(value); }
};
void write(HANDLE process, uint32_t address, const Bytes& bytes) {
    SIZE_T written = 0;
    if (!WriteProcessMemory(process, reinterpret_cast<void*>(static_cast<uintptr_t>(address)), bytes.data(), bytes.size(), &written)
        || written != bytes.size()) throw std::runtime_error(errorText("title mailbox write"));
}
}
bool validTitleCatalogue(const Bytes& bytes) {
    if (bytes.size() < 8 || bytes.size() > TITLE_CATALOGUE_MAX_SIZE) return false;
    auto word = [&](size_t at) { uint32_t value; std::memcpy(&value, bytes.data() + at, 4); return value; };
    const auto count = word(4);
    if (word(0) != 1 || !count || count > TITLE_MAX_COUNT || bytes.size() != 8 + count * TITLE_RECORD_SIZE) return false;
    uint32_t previous = 0;
    for (uint32_t i = 0; i < count; ++i) {
        const size_t start = 8 + i * TITLE_RECORD_SIZE;
        const auto id = word(start);
        if (id <= previous || id > INT32_MAX || (id != 1007 && id != 1008 && id < 1000000)) return false;
        previous = id;
        for (const auto field : {std::pair<size_t, size_t>{4, 32}, {36, 128}}) {
            const auto first = bytes.begin() + start + field.first, last = first + field.second;
            const auto end = std::find(first, last, 0);
            if (end == last || (field.first == 4 && end == first)
                || std::any_of(end, last, [](auto b) { return b != 0; })
                || std::any_of(first, end, [&](auto b) { return (b < 32 && !(field.first == 36 && b == '\n')) || b == 127 || b == '<' || b == '>'; })) return false;
            if (end != first && !MultiByteToWideChar(936, MB_ERR_INVALID_CHARS,
                    reinterpret_cast<const char*>(&*first), static_cast<int>(end - first), nullptr, 0)) return false;
        }
    }
    return true;
}
Bytes buildTitleHook(uint32_t address, uint32_t imageBase, uint32_t threadId) {
    if (!address || address > UINT32_MAX - TITLE_PAGE_SIZE || !imageBase || imageBase > UINT32_MAX - 0x2E249B8 || !threadId)
        throw std::runtime_error("title hook outside x86 range");
    Code c;
    c.emit({0x9C,0x60,0x64,0xA1}); c.word(0x24); c.emit({0x3D}); c.word(threadId);
    std::vector<size_t> idle{c.branch(0x85)};
    c.emit({0x83,0x3D}); c.word(address); c.emit({1}); idle.push_back(c.branch(0x85));
    c.store(address, 2);
    c.emit({0x8B,0xEC,0x81,0xEC}); c.word(0x210);
    c.emit({0x83,0xE4,0xF0,0x0F,0xAE,0x04,0x24,0xFC});
    c.emit({0x8B,0x1D}); c.word(imageBase + 0x2440AD0); c.emit({0x85,0xDB});
    std::vector<size_t> unavailable{c.branch(0x84)};
    c.emit({0x81,0x3B}); c.word(imageBase + 0x17302A4); unavailable.push_back(c.branch(0x85));
    c.emit({0x83,0x3D}); c.word(imageBase + 0x2E249B8); c.emit({0}); unavailable.push_back(c.branch(0x84));
    c.emit({0x83,0x7B,0x28,0}); unavailable.push_back(c.branch(0x84));
    c.emit({0x8B,0x73,0x08,0x8B,0x4B,0x0C,0x85,0xF6}); unavailable.push_back(c.branch(0x84));
    c.emit({0x85,0xC9}); unavailable.push_back(c.branch(0x8E));
    c.emit({0x81,0xF9}); c.word(TITLE_MAX_ORIGINAL_COUNT); unavailable.push_back(c.branch(0x87));
    c.emit({0x89,0x1D}); c.word(address + 8);
    c.emit({0x89,0x35}); c.word(address + 12);
    c.emit({0x89,0x0D}); c.word(address + 16);
    c.emit({0x89,0x0D}); c.word(address + 20);
    // Validate every original-name/collision constraint before touching the currently indexed copy.
    c.emit({0x8B,0x15}); c.word(address + TITLE_CATALOGUE_OFFSET + 4);
    c.emit({0xBE}); c.word(address + TITLE_CATALOGUE_OFFSET + 8);
    const auto checkRecord = c.bytes.size();
    c.emit({0x8B,0x06,0x8B,0x7B,8,0x8B,0x4B,12});
    const auto checkRow = c.bytes.size();
    c.emit({0x3B,0x07}); const auto checkFound = c.branch(0x84);
    c.emit({0x81,0xC7}); c.word(TITLE_ROW_SIZE); c.emit({0x49}); c.bind(c.branch(0x85), checkRow);
    c.emit({0x3D}); c.word(1000000); std::vector<size_t> invalid{c.branch(0x82)};
    const auto checkCustom = c.branch();
    c.bind(checkFound, c.bytes.size());
    c.emit({0x3D}); c.word(1000000); invalid.push_back(c.branch(0x83));
    c.emit({0x56,0x83,0xC6,4,0x83,0xC7,4,0xB9}); c.word(8);
    c.emit({0xF3,0xA7,0x5E}); invalid.push_back(c.branch(0x85));
    c.bind(checkCustom, c.bytes.size());
    c.emit({0x81,0xC6}); c.word(TITLE_RECORD_SIZE); c.emit({0x4A}); c.bind(c.branch(0x85), checkRecord);
    c.emit({0x8B,0x73,8,0x8B,0x4B,12});
    // A separate, process-lifetime table keeps every original row and zeroes custom attributes.
    c.emit({0x03,0x0D}); c.word(address + TITLE_CATALOGUE_OFFSET + 4);
    c.emit({0x69,0xC9}); c.word(TITLE_ROW_SIZE / 4);
    c.emit({0xBF}); c.word(address + TITLE_ROWS_OFFSET); c.emit({0x31,0xC0,0xF3,0xAB});
    c.emit({0x8B,0x0D}); c.word(address + 16);
    c.emit({0x69,0xC9}); c.word(TITLE_ROW_SIZE / 4);
    c.emit({0xBF}); c.word(address + TITLE_ROWS_OFFSET); c.emit({0xF3,0xA5});
    c.emit({0x8B,0x15}); c.word(address + TITLE_CATALOGUE_OFFSET + 4);
    c.emit({0xBE}); c.word(address + TITLE_CATALOGUE_OFFSET + 8);
    const auto nextRecord = c.bytes.size();
    c.emit({0x8B,0x06,0xBF}); c.word(address + TITLE_ROWS_OFFSET);
    c.emit({0x8B,0x0D}); c.word(address + 16);
    const auto find = c.bytes.size();
    c.emit({0x3B,0x07}); const auto found = c.branch(0x84);
    c.emit({0x81,0xC7}); c.word(TITLE_ROW_SIZE); c.emit({0x49}); c.bind(c.branch(0x85), find);
    c.emit({0xA1}); c.word(address + 20); c.emit({0x69,0xF8}); c.word(TITLE_ROW_SIZE);
    c.emit({0x81,0xC7}); c.word(address + TITLE_ROWS_OFFSET);
    c.emit({0xFF,0x05}); c.word(address + 20);
    c.bind(found, c.bytes.size());
    c.emit({0xB9}); c.word(TITLE_RECORD_SIZE / 4); c.emit({0xF3,0xA5,0x4A});
    c.bind(c.branch(0x85), nextRecord);
    // Use the client's own index builder (this = owner + 4) and UI refresh.
    // Restore its allocation pointer/count before returning: its destructor still owns the original allocation.
    c.emit({0xC7,0x43,8}); c.word(address + TITLE_ROWS_OFFSET);
    c.emit({0xA1}); c.word(address + 20); c.emit({0x89,0x43,12,0x8D,0x4B,4,0xB8});
    c.word(imageBase + TITLE_REINDEX_RVA); c.emit({0xFF,0xD0});
    c.emit({0xB8}); c.word(imageBase + TITLE_REFRESH_RVA); c.emit({0xFF,0xD0});
    c.emit({0x8B,0x1D}); c.word(address + 8);
    c.emit({0xA1}); c.word(address + 12); c.emit({0x89,0x43,8});
    c.emit({0xA1}); c.word(address + 16); c.emit({0x89,0x43,12});
    c.store(address + 4, 1); auto success = c.branch();
    for (auto p : invalid) c.bind(p, c.bytes.size());
    c.store(address + 4, 2); auto rejected = c.branch();
    for (auto p : unavailable) c.bind(p, c.bytes.size());
    c.store(address + 4, 0); c.bind(success, c.bytes.size()); c.bind(rejected, c.bytes.size());
    c.emit({0x0F,0xAE,0x0C,0x24,0x8B,0xE5}); c.store(address, 0);
    for (auto p : idle) c.bind(p, c.bytes.size());
    c.emit({0x61,0x9D,0xC3});
    if (c.bytes.size() > 0x1000) throw std::runtime_error("title hook overflow");
    Bytes page(TITLE_PAGE_SIZE, 0);
    std::copy(c.bytes.begin(), c.bytes.end(), page.begin() + TITLE_CODE_OFFSET);
    return page;
}
void TitleSettings::update(const Bytes& bytes) {
    if (!validTitleCatalogue(bytes)) throw std::runtime_error("invalid title catalogue");
    desired_ = bytes;
}
void TitleSettings::poll() {
    if (!process_ || desired_.empty()) return;
    if (pending_) {
        auto bytes = readClientMemory(process_, remote_, 8);
        uint32_t command = 0, result = 0;
        std::memcpy(&command, bytes.data(), 4); std::memcpy(&result, bytes.data() + 4, 4);
        if (command) {
            if (GetTickCount64() - requestedAt_ > 10000) throw std::runtime_error("title frame thread response timed out");
            return;
        }
        pending_ = false;
        if (result == 2) throw std::runtime_error("title catalogue conflicts with original resources");
        if (result == 1) applied_ = sent_;
        else retryAt_ = requestedAt_ + 1000;
    }
    if (desired_ == applied_ || GetTickCount64() < retryAt_) return;
    write(process_, remote_ + TITLE_CATALOGUE_OFFSET, desired_);
    sent_ = desired_;
    write(process_, remote_, Bytes{1,0,0,0});
    pending_ = true; requestedAt_ = GetTickCount64();
}
}
