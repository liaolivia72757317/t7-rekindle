using System;
using System.Runtime.InteropServices;

namespace T7.Rekindle.Core
{
    [StructLayout(LayoutKind.Sequential, Pack = 8)]
    public struct NativeStartArgs
    {
        public uint AbiVersion;
        public uint StructSize;
        public IntPtr ClientDirectory;
        public uint ClientDirectoryLength;
        public IntPtr PlayerName;
        public uint PlayerNameLength;
    }

    [Flags]
    public enum NativeStartFlags : uint
    {
        None = 0,
        SkipStartupAnimation = 1
    }

    [StructLayout(LayoutKind.Sequential, Pack = 8)]
    public struct NativeStartOptions
    {
        public NativeStartArgs Start;
        public NativeStartFlags Flags;
        public uint Reserved;
    }

    [StructLayout(LayoutKind.Sequential, Pack = 8)]
    public struct NativePath
    {
        public uint AbiVersion;
        public uint StructSize;
        public IntPtr Data;
        public uint Length;
    }

    [StructLayout(LayoutKind.Sequential, Pack = 8)]
    public struct NativeCreateArgs
    {
        public uint AbiVersion;
        public uint StructSize;
        public IntPtr PackageRoot;
        public uint PackageRootLength;
        public uint Flags;
    }

    [StructLayout(LayoutKind.Sequential, Pack = 8)]
    public struct NativeSnapshot
    {
        public uint AbiVersion;
        public uint StructSize;
        public uint State;
        public uint Operation;
        public ulong OperationId;
        public ulong Revision;
        public ushort LoginPort;
        public ushort LogicPort;
        public ushort InstancePort;
        public ushort Reserved;
        public uint ErrorCode;
        public uint Flags;
        public ulong LogCursor;
        [MarshalAs(UnmanagedType.ByValArray, SizeConst = 64)]
        public byte[] Phase;
    }

    [StructLayout(LayoutKind.Sequential, Pack = 8)]
    public struct NativeOperation
    {
        public uint AbiVersion;
        public uint StructSize;
        public ulong OperationId;
        public uint Kind;
        public uint Status;
        public uint ErrorCode;
        public uint Reserved;
    }

    public static class NativeAbiLayout
    {
        public static void Validate()
        {
            if (Marshal.SizeOf(typeof(NativePath)) != 24
                || Marshal.SizeOf(typeof(NativeStartArgs)) != 40
                || Marshal.SizeOf(typeof(NativeStartOptions)) != 48
                || (int)Marshal.OffsetOf(typeof(NativeStartOptions), nameof(NativeStartOptions.Flags)) != 40
                || Marshal.SizeOf(typeof(NativeCreateArgs)) != 24
                || Marshal.SizeOf(typeof(NativeSnapshot)) != 120
                || Marshal.SizeOf(typeof(NativeOperation)) != 32
                || (int)Marshal.OffsetOf(typeof(NativeSnapshot), nameof(NativeSnapshot.Phase)) != 56)
            {
                throw new InvalidOperationException("NativeBridge managed ABI layout mismatch.");
            }
        }
    }
}
