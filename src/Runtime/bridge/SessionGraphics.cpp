#include "Session.h"

namespace t7::bridge {
int32_t Session::graphics(GraphicsSnapshot& result) const {
    std::lock_guard<std::mutex> lock(mutex_);
    if (closing_) return T7NB_INVALID_HANDLE;
    result = graphics_;
    if (snapshot_.state != T7NB_STATE_RUNNING || snapshot_.operation != T7NB_OPERATION_NONE)
        result.state = T7NB_GRAPHICS_UNAVAILABLE;
    return T7NB_OK;
}
int32_t Session::applyGraphics(uint64_t revision, const GraphicsValues& values) {
    if (!validGraphicsValues(values)) return T7NB_INVALID_ARGUMENT;
    std::lock_guard<std::mutex> lock(mutex_);
    if (closing_) return T7NB_INVALID_HANDLE;
    if (snapshot_.state != T7NB_STATE_RUNNING || snapshot_.operation != T7NB_OPERATION_NONE
        || graphics_.state == T7NB_GRAPHICS_UNAVAILABLE || graphics_.state == T7NB_GRAPHICS_FAILED)
        return T7NB_NOT_READY;
    if (graphics_.state == T7NB_GRAPHICS_APPLYING || graphicsQueued_) return T7NB_BUSY;
    if (revision != graphics_.revision) return T7NB_NOT_READY;
    requestedGraphics_ = values; requestedGraphicsRevision_ = revision;
    graphicsQueued_ = true; graphics_.state = T7NB_GRAPHICS_APPLYING;
    return T7NB_OK;
}
void Session::monitorGraphics() {
    {
        std::lock_guard<std::mutex> lock(mutex_);
        if (graphics_.state == T7NB_GRAPHICS_FAILED) return;
    }
    try {
        GraphicsValues values; uint32_t result = 0; bool applied = false;
        if (!bootstrap_.pollGraphics(values, result, applied)) return;
        if (applied && result == 0) log("Graphics application was not confirmed by the client display state", "WARNING");
        bool submit = false;
        GraphicsValues request;
        {
            std::lock_guard<std::mutex> lock(mutex_);
            if (result == 0) {
                graphics_.state = applied ? T7NB_GRAPHICS_FAILED : T7NB_GRAPHICS_UNAVAILABLE;
                graphicsQueued_ = false; return;
            }
            if (!sameGraphicsValues(graphics_.values, values) || graphics_.state == T7NB_GRAPHICS_UNAVAILABLE) {
                graphics_.values = values; ++graphics_.revision;
            }
            if (graphicsQueued_) {
                submit = requestedGraphicsRevision_ == graphics_.revision;
                request = requestedGraphics_; graphicsQueued_ = false;
                graphics_.state = submit ? T7NB_GRAPHICS_APPLYING : T7NB_GRAPHICS_CONFLICT;
            } else if (applied) {
                graphics_.state = result == 2 ? T7NB_GRAPHICS_CONFLICT : T7NB_GRAPHICS_READY;
            } else if (graphics_.state != T7NB_GRAPHICS_CONFLICT) {
                graphics_.state = T7NB_GRAPHICS_READY;
            }
        }
        if (submit) bootstrap_.applyGraphics(request);
        if (applied && result == 1) log("Graphics settings applied on client frame thread and read back");
    } catch (const std::exception& error) {
        {
            std::lock_guard<std::mutex> lock(mutex_);
            graphics_.state = T7NB_GRAPHICS_FAILED; graphicsQueued_ = false;
        }
        log(std::string("Graphics synchronization stopped: ") + error.what(), "WARNING");
    }
}
}
