using System;
using System.Runtime.InteropServices;

namespace T7.Rekindle.Core
{
    public enum GraphicsSyncState : uint { Unavailable, Ready, Applying, Failed, Conflict }

    public sealed class GraphicsSettingsValues : IEquatable<GraphicsSettingsValues>
    {
        public GraphicsSettingsValues(uint width = 1440, uint height = 900, bool fullScreen = false,
            uint quality = 4, bool verticalSync = false, bool fog = false, uint viewDistance = 128,
            bool ragDoll = true, bool frameLimit = true, uint swoosh = 0)
        {
            Width = width; Height = height; FullScreen = fullScreen; Quality = quality;
            VerticalSync = verticalSync; Fog = fog; ViewDistance = viewDistance;
            RagDoll = ragDoll; FrameLimit = frameLimit; Swoosh = swoosh;
        }
        public uint Width { get; }
        public uint Height { get; }
        public bool FullScreen { get; }
        public uint Quality { get; }
        public bool VerticalSync { get; }
        public bool Fog { get; }
        public uint ViewDistance { get; }
        public bool RagDoll { get; }
        public bool FrameLimit { get; }
        public uint Swoosh { get; }
        public string Resolution => Width + "x" + Height;
        public bool IsValid => Width >= 640 && Width <= 7680 && Height >= 480 && Height <= 4320
            && Quality <= 4 && ViewDistance >= 32 && ViewDistance <= 1024 && Swoosh <= 2;
        public bool Equals(GraphicsSettingsValues other) => other != null && Width == other.Width && Height == other.Height
            && FullScreen == other.FullScreen && Quality == other.Quality && VerticalSync == other.VerticalSync
            && Fog == other.Fog && ViewDistance == other.ViewDistance && RagDoll == other.RagDoll
            && FrameLimit == other.FrameLimit && Swoosh == other.Swoosh;
        public override bool Equals(object obj) => Equals(obj as GraphicsSettingsValues);
        public override int GetHashCode() => Resolution.GetHashCode() ^ (int)Quality ^ (int)ViewDistance ^ (int)Swoosh;
    }

    public sealed class GraphicsSettingsSnapshot
    {
        public GraphicsSettingsSnapshot(ulong revision, GraphicsSyncState state, GraphicsSettingsValues values)
        { Revision = revision; State = state; Values = values; }
        public ulong Revision { get; }
        public GraphicsSyncState State { get; }
        public GraphicsSettingsValues Values { get; }
    }

    [StructLayout(LayoutKind.Sequential, Pack = 8)]
    public struct NativeGraphicsValues
    {
        public uint Width, Height, FullScreen, Quality, VerticalSync, Fog, ViewDistance, RagDoll, FrameLimit, Swoosh;
        public GraphicsSettingsValues ToManaged()
        {
            var result = new GraphicsSettingsValues(Width, Height, FullScreen != 0, Quality, VerticalSync != 0,
                Fog != 0, ViewDistance, RagDoll != 0, FrameLimit != 0, Swoosh);
            if (!result.IsValid || FullScreen > 1 || VerticalSync > 1 || Fog > 1 || RagDoll > 1 || FrameLimit > 1)
                throw new InvalidOperationException("Invalid native graphics values.");
            return result;
        }
        public static NativeGraphicsValues FromManaged(GraphicsSettingsValues v) => new NativeGraphicsValues
        {
            Width = v.Width, Height = v.Height, FullScreen = v.FullScreen ? 1u : 0u, Quality = v.Quality,
            VerticalSync = v.VerticalSync ? 1u : 0u, Fog = v.Fog ? 1u : 0u, ViewDistance = v.ViewDistance,
            RagDoll = v.RagDoll ? 1u : 0u, FrameLimit = v.FrameLimit ? 1u : 0u, Swoosh = v.Swoosh
        };
    }
    [StructLayout(LayoutKind.Sequential, Pack = 8)]
    public struct NativeGraphicsSnapshot
    {
        public uint AbiVersion, StructSize;
        public ulong Revision;
        public GraphicsSyncState State;
        public NativeGraphicsValues Values;
        public uint Reserved;
    }
}
