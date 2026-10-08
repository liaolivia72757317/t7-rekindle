#include "Session.h"

namespace t7::bridge {
int32_t Session::audio(AudioSnapshot& result) const {
    std::lock_guard<std::mutex> lock(mutex_);
    if (closing_) return T7NB_INVALID_HANDLE;
    result = audio_;
    if (snapshot_.state != T7NB_STATE_RUNNING || snapshot_.operation != T7NB_OPERATION_NONE)
        result.state = T7NB_AUDIO_UNAVAILABLE;
    return T7NB_OK;
}
int32_t Session::applyAudio(uint64_t revision, const AudioValues& values) {
    if (!validAudioValues(values)) return T7NB_INVALID_ARGUMENT;
    std::lock_guard<std::mutex> lock(mutex_);
    if (closing_) return T7NB_INVALID_HANDLE;
    if (snapshot_.state != T7NB_STATE_RUNNING || snapshot_.operation != T7NB_OPERATION_NONE
        || audio_.state == T7NB_AUDIO_UNAVAILABLE || audio_.state == T7NB_AUDIO_FAILED)
        return T7NB_NOT_READY;
    if (audio_.state == T7NB_AUDIO_APPLYING || audioQueued_) return T7NB_BUSY;
    if (revision != audio_.revision) return T7NB_NOT_READY;
    requestedAudio_ = values; requestedAudioRevision_ = revision;
    audioQueued_ = true; audio_.state = T7NB_AUDIO_APPLYING;
    return T7NB_OK;
}
void Session::monitorAudio() {
    {
        std::lock_guard<std::mutex> lock(mutex_);
        if (audio_.state == T7NB_AUDIO_FAILED) return;
    }
    try {
        AudioValues values; uint32_t result = 0; bool applied = false;
        if (!bootstrap_.pollAudio(values, result, applied)) return;
        bool submit = false;
        AudioValues request;
        {
            std::lock_guard<std::mutex> lock(mutex_);
            if (result == 0) {
                audio_.state = applied ? T7NB_AUDIO_FAILED : T7NB_AUDIO_UNAVAILABLE;
                audioQueued_ = false; return;
            }
            if (!sameAudioValues(audio_.values, values) || audio_.state == T7NB_AUDIO_UNAVAILABLE) {
                audio_.values = values; ++audio_.revision;
            }
            if (audioQueued_) {
                submit = requestedAudioRevision_ == audio_.revision;
                request = requestedAudio_; audioQueued_ = false;
                audio_.state = submit ? T7NB_AUDIO_APPLYING : T7NB_AUDIO_CONFLICT;
            } else if (applied) {
                audio_.state = result == 2 ? T7NB_AUDIO_CONFLICT : T7NB_AUDIO_READY;
            } else if (audio_.state != T7NB_AUDIO_CONFLICT) {
                audio_.state = T7NB_AUDIO_READY;
            }
        }
        if (submit) bootstrap_.applyAudio(request);
        if (applied && result == 1) log("Audio settings applied on client frame thread and read back");
    } catch (const std::exception& error) {
        {
            std::lock_guard<std::mutex> lock(mutex_);
            audio_.state = T7NB_AUDIO_FAILED; audioQueued_ = false;
        }
        log(std::string("Audio synchronization stopped: ") + error.what(), "WARNING");
    }
}
}
