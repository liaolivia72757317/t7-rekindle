#include "ClientImage.h"
#include <algorithm>
#include <cstring>

namespace t7 {
namespace {
constexpr uint32_t IMAGE_BASE = 0x00400000, IMAGE_SIZE = 0x03B54000;
constexpr uint32_t ENTRY_RVA = 0x01220739, IMPORT_RVA = 0x03443000, IMPORT_CAPACITY = 0x00020000;
void checkCancelled(const std::function<bool()>& cancelled) {
    if (cancelled && cancelled()) throw std::runtime_error("client image preparation cancelled");
}
}

ClientImage recoverClientImage(const Bytes& original, const std::function<bool()>& cancelled) {
    const auto code = recoverClientCode(original, cancelled);
    checkCancelled(cancelled);
    IMAGE_DOS_HEADER dos{};
    std::memcpy(&dos, original.data(), sizeof(dos));
    IMAGE_NT_HEADERS32 headers{};
    std::memcpy(&headers, original.data() + dos.e_lfanew, sizeof(headers));
    if (headers.OptionalHeader.ImageBase != IMAGE_BASE || headers.OptionalHeader.SizeOfImage != IMAGE_SIZE
        || headers.FileHeader.NumberOfSections != 7 || headers.OptionalHeader.SizeOfHeaders != 0x400
        || headers.FileHeader.SizeOfOptionalHeader != sizeof(IMAGE_OPTIONAL_HEADER32)
        || headers.OptionalHeader.Magic != IMAGE_NT_OPTIONAL_HDR32_MAGIC
        || headers.OptionalHeader.DataDirectory[IMAGE_DIRECTORY_ENTRY_BASERELOC].VirtualAddress
        || headers.OptionalHeader.DataDirectory[IMAGE_DIRECTORY_ENTRY_BASERELOC].Size
        || (headers.OptionalHeader.DllCharacteristics & IMAGE_DLLCHARACTERISTICS_DYNAMIC_BASE))
        throw std::runtime_error("unsupported client memory layout");
    ClientImage image;
    image.imageBase = IMAGE_BASE;
    image.originalEntryRva = headers.OptionalHeader.AddressOfEntryPoint;
    image.entryRva = ENTRY_RVA;
    image.bytes.resize(IMAGE_SIZE, 0);
    std::copy_n(original.begin(), headers.OptionalHeader.SizeOfHeaders, image.bytes.begin());
    image.regions.push_back({0, 0x1000, PAGE_READONLY});
    const auto sectionTable = static_cast<size_t>(dos.e_lfanew) + sizeof(headers);
    for (size_t i = 0; i < 7; ++i) {
        checkCancelled(cancelled);
        IMAGE_SECTION_HEADER section{};
        std::memcpy(&section, original.data() + sectionTable + i * sizeof(section), sizeof(section));
        const auto memorySize = (std::max(section.Misc.VirtualSize, section.SizeOfRawData) + 0xFFFu) & ~0xFFFu;
        if (section.VirtualAddress > image.bytes.size() || memorySize > image.bytes.size() - section.VirtualAddress
            || section.PointerToRawData > original.size() || section.SizeOfRawData > original.size() - section.PointerToRawData)
            throw std::runtime_error("client section exceeds image layout");
        if (i < code.size()) {
            if (code[i].rva != section.VirtualAddress || code[i].bytes.size() != memorySize)
                throw std::runtime_error("client recovered code layout mismatch");
            std::copy(code[i].bytes.begin(), code[i].bytes.end(), image.bytes.begin() + section.VirtualAddress);
        } else if (i < 6) {
            std::copy_n(original.begin() + section.PointerToRawData, section.SizeOfRawData,
                        image.bytes.begin() + section.VirtualAddress);
        }
        // The former loader region stays zero-filled except for new import data.
        const DWORD protection = i < 2 ? PAGE_EXECUTE_READ : (i == 3 || i == 4) ? PAGE_READWRITE : PAGE_READONLY;
        image.regions.push_back({section.VirtualAddress, memorySize, protection});
    }
    uint32_t end = 0;
    for (const auto& region : image.regions) {
        if (region.rva != end) throw std::runtime_error("client memory regions have a gap or overlap");
        end += region.size;
    }
    if (end != IMAGE_SIZE) throw std::runtime_error("client memory regions do not cover the image");
    image.imports = writeClientImports(image.bytes, IMPORT_RVA, IMPORT_CAPACITY, clientImportModules());
    if (image.imports.symbolCount != 794 || image.imports.iatRva != 0x016F6000)
        throw std::runtime_error("client import plan coverage mismatch");
    headers.OptionalHeader.AddressOfEntryPoint = ENTRY_RVA;
    headers.OptionalHeader.DataDirectory[IMAGE_DIRECTORY_ENTRY_IMPORT] = {image.imports.rva, image.imports.size};
    headers.OptionalHeader.DataDirectory[IMAGE_DIRECTORY_ENTRY_IAT] = {image.imports.iatRva, image.imports.iatSize};
    std::memcpy(image.bytes.data() + dos.e_lfanew, &headers, sizeof(headers));
    checkCancelled(cancelled);
    return image;
}
}
