#pragma once
#include "../../../src/Runtime/core/Common.h"
#include <Python.h>
#include <memory>

namespace t7r::python {
struct Release { void operator()(PyObject* p) const { Py_XDECREF(p); } };
using Object = std::unique_ptr<PyObject, Release>;
inline std::string error() {
    Object exception(PyErr_GetRaisedException());
    if (!exception) return "Python operation failed";
    std::string summary = Py_TYPE(exception.get())->tp_name;
    Object text(PyObject_Str(exception.get()));
    if (text) {
        const char* chars = PyUnicode_AsUTF8(text.get());
        if (chars) summary += std::string(": ") + chars;
    }
    PyErr_Clear();
    Object traceback(PyImport_ImportModule("traceback"));
    if (traceback) {
        Object lines(PyObject_CallMethod(traceback.get(), "format_exception", "O", exception.get()));
        Object separator(PyUnicode_FromString(""));
        if (lines && separator) {
            Object formatted(PyUnicode_Join(separator.get(), lines.get()));
            if (formatted) {
                const char* chars = PyUnicode_AsUTF8(formatted.get());
                if (chars) return summary + "\n" + chars;
            }
        }
    }
    PyErr_Clear();
    return summary;
}
inline Object checked(PyObject* p) { if (!p) throw std::runtime_error(error()); return Object(p); }
inline std::string string(PyObject* p) {
    if (!p) throw std::runtime_error("missing Python string");
    const char* result = PyUnicode_AsUTF8(p);
    if (!result) throw std::runtime_error(error());
    return result;
}
inline void put(PyObject* dict, const char* key, PyObject* owned) {
    auto value = checked(owned);
    if (PyDict_SetItemString(dict, key, value.get()) < 0) throw std::runtime_error(error());
}
inline PyObject* field(PyObject* object, const char* key) {
    if (!object || !PyDict_Check(object)) throw std::runtime_error("Python field container is not a dict");
    PyObject* value = PyDict_GetItemString(object, key);
    if (!value) throw std::runtime_error(std::string("missing field: ") + key);
    return value;
}
}
