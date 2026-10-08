using System;
using System.Runtime.InteropServices;

namespace T7.Rekindle.Core
{
    public enum AudioSyncState : uint { Unavailable, Ready, Applying, Failed, Conflict }

    public sealed class AudioSettingsValues : IEquatable<AudioSettingsValues>
    {
        public AudioSettingsValues(bool musicMuted = false, float musicVolume = 1,
            bool effectsMuted = false, float effectsVolume = 1)
        { MusicMuted = musicMuted; MusicVolume = musicVolume; EffectsMuted = effectsMuted; EffectsVolume = effectsVolume; }
        public bool MusicMuted { get; }
        public float MusicVolume { get; }
        public bool EffectsMuted { get; }
        public float EffectsVolume { get; }
        public bool IsValid => MusicVolume >= 0 && MusicVolume <= 1 && EffectsVolume >= 0 && EffectsVolume <= 1;
        public bool Equals(AudioSettingsValues other) => other != null && MusicMuted == other.MusicMuted
            && MusicVolume == other.MusicVolume && EffectsMuted == other.EffectsMuted && EffectsVolume == other.EffectsVolume;
        public override bool Equals(object obj) => Equals(obj as AudioSettingsValues);
        public override int GetHashCode() => MusicVolume.GetHashCode() ^ EffectsVolume.GetHashCode() ^ MusicMuted.GetHashCode();
    }
    public sealed class AudioSettingsSnapshot
    {
        public AudioSettingsSnapshot(ulong revision, AudioSyncState state, AudioSettingsValues values)
        { Revision = revision; State = state; Values = values; }
        public ulong Revision { get; }
        public AudioSyncState State { get; }
        public AudioSettingsValues Values { get; }
    }
    [StructLayout(LayoutKind.Sequential, Pack = 8)]
    public struct NativeAudioValues
    {
        public uint MusicMuted;
        public float MusicVolume;
        public uint EffectsMuted;
        public float EffectsVolume;
        public AudioSettingsValues ToManaged()
        {
            var result = new AudioSettingsValues(MusicMuted != 0, MusicVolume, EffectsMuted != 0, EffectsVolume);
            if (!result.IsValid || MusicMuted > 1 || EffectsMuted > 1)
                throw new InvalidOperationException("Invalid native audio values.");
            return result;
        }
        public static NativeAudioValues FromManaged(AudioSettingsValues v) => new NativeAudioValues
        { MusicMuted = v.MusicMuted ? 1u : 0u, MusicVolume = v.MusicVolume,
            EffectsMuted = v.EffectsMuted ? 1u : 0u, EffectsVolume = v.EffectsVolume };
    }
    [StructLayout(LayoutKind.Sequential, Pack = 8)]
    public struct NativeAudioSnapshot
    {
        public uint AbiVersion, StructSize;
        public ulong Revision;
        public AudioSyncState State;
        public NativeAudioValues Values;
        public uint Reserved;
    }
}
