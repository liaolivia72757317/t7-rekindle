using System;
using System.Collections.Generic;
using System.Globalization;
using System.Runtime.InteropServices;

namespace T7.Rekindle.Desktop.Services
{
    internal static class Direct3DEnvironment
    {
        internal static IReadOnlyList<OutputDeviceOption> ReadOutputDevices()
        {
            var d3d = Direct3DCreate9(32);
            if (d3d == IntPtr.Zero) throw new InvalidOperationException("未检出可用的 D3D9 接口。");
            try
            {
                var result = new List<OutputDeviceOption>();
                var seen = new HashSet<Guid>();
                var count = Method<GetAdapterCount>(d3d, 4)(d3d);
                for (uint index = 0; index < count; index++)
                {
                    var status = Method<GetAdapterIdentifier>(d3d, 5)(d3d, index, 0, out var adapter);
                    if (status < 0) Marshal.ThrowExceptionForHR(status);
                    var capsStatus = Method<GetDeviceCaps>(d3d, 14)(d3d, index, 1, out var caps);
                    if (capsStatus < 0 || caps.VertexShaderVersion < 0xfffe0300 || caps.PixelShaderVersion < 0xffff0300) continue;
                    if (seen.Add(adapter.DeviceIdentifier)) result.Add(new OutputDeviceOption(adapter.DeviceIdentifier, adapter.Description));
                }
                return result.ToArray();
            }
            finally { Marshal.Release(d3d); }
        }

        private static uint FindAdapter(IntPtr d3d, Guid identifier)
        {
            if (identifier == Guid.Empty) return 0;
            var count = Method<GetAdapterCount>(d3d, 4)(d3d);
            for (uint index = 0; index < count; index++)
            {
                var result = Method<GetAdapterIdentifier>(d3d, 5)(d3d, index, 0, out var adapter);
                if (result < 0) Marshal.ThrowExceptionForHR(result);
                if (adapter.DeviceIdentifier == identifier) return index;
            }
            throw new InvalidOperationException("所选输出设备未连接或驱动已变化，请重新选择。");
        }

        internal static IReadOnlyList<string> ReadResolutions() => ReadResolutions(Guid.Empty);

        internal static IReadOnlyList<string> ReadResolutions(Guid identifier)
        {
            var d3d = Direct3DCreate9(32);
            if (d3d == IntPtr.Zero) throw new InvalidOperationException("未检出可用的 D3D9 接口。");
            try
            {
                var count = Method<GetAdapterModeCount>(d3d, 6);
                var enumerate = Method<EnumAdapterModes>(d3d, 7);
                var adapter = FindAdapter(d3d, identifier);
                return ReadResolutions(format => count(d3d, adapter, format), (format, index) =>
                {
                    var result = enumerate(d3d, adapter, format, index, out var mode);
                    if (result < 0) Marshal.ThrowExceptionForHR(result);
                    return mode;
                });
            }
            finally { Marshal.Release(d3d); }
        }

        internal static IReadOnlyList<string> ReadResolutions(Func<uint, uint> countModes, Func<uint, uint, DisplayMode> readMode)
        {
            var resolutions = new List<string>();
            // Match the game's 32-bit modes, retaining enumeration order and collapsing refresh rates.
            foreach (var format in new uint[] { 21, 22 }) // D3DFMT_A8R8G8B8, D3DFMT_X8R8G8B8
            {
                var count = countModes(format);
                for (uint index = 0; index < count; index++)
                {
                    var mode = readMode(format, index);
                    var resolution = mode.Width.ToString(CultureInfo.InvariantCulture) + "x" + mode.Height.ToString(CultureInfo.InvariantCulture);
                    if (!resolutions.Contains(resolution)) resolutions.Add(resolution);
                }
            }
            if (resolutions.Count == 0) throw new InvalidOperationException("输出设备未返回可用的 D3D9 分辨率。");
            return resolutions.ToArray();
        }

        internal static Information Read()
        {
            var d3d = Direct3DCreate9(32);
            if (d3d == IntPtr.Zero) return new Information("未检出", "未检出可用的 D3D9 接口");
            try
            {
                // IDirect3D9 vtable slots from the Windows SDK (including IUnknown).
                var count = Method<GetAdapterCount>(d3d, 4)(d3d);
                if (count == 0) return new Information("未检出", "未检出 D3D9 适配器");
                var identify = Method<GetAdapterIdentifier>(d3d, 5);
                var capabilities = Method<GetDeviceCaps>(d3d, 14);
                var adapters = new List<string>();
                for (uint index = 0; index < count; index++)
                {
                    var result = identify(d3d, index, 0, out var adapter);
                    adapters.Add(result < 0 ? "#" + (index + 1) + " 检测失败（HRESULT 0x" + result.ToString("X8", CultureInfo.InvariantCulture) + "）"
                        : DescribeAdapter(index, adapter.Description, adapter.DriverVersion));
                }
                var capabilityResult = capabilities(d3d, 0, 1, out var caps);
                var summary = capabilityResult < 0 ? "HAL x64 · 默认显卡能力检测失败（HRESULT 0x" + capabilityResult.ToString("X8", CultureInfo.InvariantCulture) + "）"
                    : DescribeCapabilities(caps.VertexShaderVersion, caps.PixelShaderVersion);
                return new Information(string.Join("；", adapters), summary);
            }
            finally { Marshal.Release(d3d); }
        }

        internal static string DescribeAdapter(uint index, string name, long driverVersion) =>
            name + "（#" + (index + 1) + (index == 0 ? "，默认" : "") + "）· 驱动 " + FormatDriverVersion(driverVersion);

        internal static string DescribeCapabilities(uint vertexShader, uint pixelShader) =>
            "HAL x64 · " + (vertexShader >= 0xfffe0300 && pixelShader >= 0xffff0300 ? "PS/VS 3.0 达标"
                : "PS " + ShaderVersion(pixelShader) + " / VS " + ShaderVersion(vertexShader) + " 未达标（需 PS/VS 3.0）")
            + " · 此处检测系统默认显卡";

        internal sealed class Information
        {
            internal Information(string adapters, string capabilities) { Adapters = adapters; Capabilities = capabilities; }
            internal string Adapters { get; }
            internal string Capabilities { get; }
        }

        private static string ShaderVersion(uint version) => ((version >> 8) & 0xff) + "." + (version & 0xff);

        internal static string FormatDriverVersion(long version) =>
            ((version >> 48) & 0xffff) + "." + ((version >> 32) & 0xffff) + "." + ((version >> 16) & 0xffff) + "." + (version & 0xffff);

        private static T Method<T>(IntPtr instance, int slot) where T : Delegate =>
            Marshal.GetDelegateForFunctionPointer<T>(Marshal.ReadIntPtr(Marshal.ReadIntPtr(instance), slot * IntPtr.Size));

        [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Ansi)]
        internal struct AdapterIdentifier
        {
            [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 512)] public string Driver;
            [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 512)] public string Description;
            [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 32)] public string DeviceName;
            public long DriverVersion;
            public uint VendorId, DeviceId, SubSysId, Revision;
            public Guid DeviceIdentifier;
            public uint WhqlLevel;
        }

        // D3DCAPS9 has 304 bytes; only the two shader fields are needed by the launch gate.
        [StructLayout(LayoutKind.Explicit, Size = 304)]
        internal struct DeviceCapabilities
        {
            [FieldOffset(196)] public uint VertexShaderVersion;
            [FieldOffset(204)] public uint PixelShaderVersion;
        }

        [StructLayout(LayoutKind.Sequential)]
        internal struct DisplayMode
        {
            public uint Width, Height, RefreshRate, Format;
        }

        [UnmanagedFunctionPointer(CallingConvention.StdCall)] private delegate uint GetAdapterCount(IntPtr instance);
        [UnmanagedFunctionPointer(CallingConvention.StdCall)] private delegate uint GetAdapterModeCount(IntPtr instance, uint adapter, uint format);
        [UnmanagedFunctionPointer(CallingConvention.StdCall)] private delegate int EnumAdapterModes(IntPtr instance, uint adapter, uint format, uint index, out DisplayMode mode);
        [UnmanagedFunctionPointer(CallingConvention.StdCall)] private delegate int GetAdapterIdentifier(IntPtr instance, uint adapter, uint flags, out AdapterIdentifier identifier);
        [UnmanagedFunctionPointer(CallingConvention.StdCall)] private delegate int GetDeviceCaps(IntPtr instance, uint adapter, uint deviceType, out DeviceCapabilities capabilities);
        [DllImport("d3d9.dll", ExactSpelling = true)]
        [DefaultDllImportSearchPaths(DllImportSearchPath.System32)]
        private static extern IntPtr Direct3DCreate9(uint sdkVersion);
    }
}
