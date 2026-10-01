"""Non-executable PE version resource and a pinned synthetic stdlib for package tests."""
import hashlib
import io
import struct
import zipfile


def python_dll(machine=0x8664, version=(3, 14, 4150, 1013)):
    data = bytearray(1024)
    data[:2] = b"MZ"
    struct.pack_into("<I", data, 0x3C, 0x80)
    data[0x80:0x84] = b"PE\0\0"
    struct.pack_into("<HH", data, 0x84, machine, 1)
    struct.pack_into("<H", data, 0x94, 240)
    optional = 0x98
    struct.pack_into("<H", data, optional, 0x20B)
    struct.pack_into("<I", data, optional + 108, 16)
    struct.pack_into("<II", data, optional + 128, 0x1000, 512)
    struct.pack_into("<IIII", data, optional + 240 + 8, 512, 0x1000, 512, 512)
    for offset, name, target in ((0, 16, 0x80000020), (32, 1, 0x80000040), (64, 1033, 96)):
        struct.pack_into("<H", data, 512 + offset + 14, 1)
        struct.pack_into("<II", data, 512 + offset + 16, name, target)
    struct.pack_into("<II", data, 512 + 96, 0x1080, 92)
    struct.pack_into("<HHH", data, 640, 92, 52, 0)
    key = "VS_VERSION_INFO\0".encode("utf-16le")
    data[646:646 + len(key)] = key
    struct.pack_into("<IIII", data, 680, 0xFEEF04BD, 0x10000,
                     version[0] << 16 | version[1], version[2] << 16 | version[3])
    return bytes(data)


def python_archive(path, module):
    library = io.BytesIO()
    with zipfile.ZipFile(library, "w") as archive:
        archive.writestr("encodings/__init__.pyc", b"synthetic bytecode")
    stdlib = library.getvalue()
    module.PYTHON_STDLIB_SHA256 = hashlib.sha256(stdlib).hexdigest()
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("python314.dll", python_dll())
        archive.writestr("python314.zip", stdlib)
