using System;
using System.Collections.Generic;
using System.Globalization;
using System.Runtime.InteropServices;

namespace T7.Rekindle.Desktop.Services
{
    internal static class Direct3DEnvironment
    {
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
            + " · 预检使用默认显卡";

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

        [UnmanagedFunctionPointer(CallingConvention.StdCall)] private delegate uint GetAdapterCount(IntPtr instance);
        [UnmanagedFunctionPointer(CallingConvention.StdCall)] private delegate int GetAdapterIdentifier(IntPtr instance, uint adapter, uint flags, out AdapterIdentifier identifier);
        [UnmanagedFunctionPointer(CallingConvention.StdCall)] private delegate int GetDeviceCaps(IntPtr instance, uint adapter, uint deviceType, out DeviceCapabilities capabilities);
        [DllImport("d3d9.dll", ExactSpelling = true)]
        [DefaultDllImportSearchPaths(DllImportSearchPath.System32)]
        private static extern IntPtr Direct3DCreate9(uint sdkVersion);
    }
}
