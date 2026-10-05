using System;
using System.ComponentModel;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Runtime.InteropServices;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading.Tasks;
using Microsoft.Win32;

namespace T7.Rekindle.Desktop.Services
{
    internal static class RuntimeEnvironmentInformation
    {
        internal static Task<string> CollectAsync() => Task.Run(Collect);

        private static string Collect()
        {
            var report = new StringBuilder();
            report.AppendLine("T7-Rekindle 环境信息 · " + DateTimeOffset.Now.ToString("yyyy-MM-dd HH:mm:ss zzz", CultureInfo.InvariantCulture))
                .AppendLine("启动器：" + LauncherInformation.Version + " · 进程 " + (Environment.Is64BitProcess ? "x64" : "x86")
                    + " · " + LauncherInformation.ShortHash);
            AppendSection(report, "系统", ReadOperatingSystem);
            AppendSection(report, "CPU", ReadCpu);
            AppendSection(report, "内存", ReadMemory);
            var graphics = new Lazy<Direct3DEnvironment.Information>(Direct3DEnvironment.Read);
            AppendSection(report, "显卡", () => graphics.Value.Adapters);
            AppendSection(report, "D3D9", () => graphics.Value.Capabilities);
            AppendSection(report, ".NET Framework", ReadFramework);
            AppendSection(report, "DX9（系统 x86）", () => ReadSystemRuntimeFiles("d3d9.dll", "d3dx9_43.dll"));
            AppendSection(report, "VC++ 2012（系统 x86）", () => ReadSystemRuntimeFiles("msvcr110.dll", "msvcp110.dll"));
            return report.ToString().TrimEnd();
        }

        internal static void AppendSection(StringBuilder report, string title, Func<string> read)
            => report.Append(title).Append("：").AppendLine(ReadValue(read));

        private static string ReadValue(Func<string> read)
        {
            string value;
            try { value = read(); }
            catch (Exception error)
            {
                // Keep each probe independent, and never copy exception paths into the report.
                value = "检测失败（" + error.GetType().Name + "，0x" + error.HResult.ToString("X8", CultureInfo.InvariantCulture) + "）";
            }
            var lines = (value ?? string.Empty).Split(new[] { '\r', '\n' }, StringSplitOptions.RemoveEmptyEntries)
                .Select(line => line.Trim()).Where(line => line.Length != 0).ToArray();
            return lines.Length == 0 ? "未检出" : string.Join("；", lines);
        }

        private static string ReadOperatingSystem()
        {
            using (var key = Registry.LocalMachine.OpenSubKey(@"SOFTWARE\Microsoft\Windows NT\CurrentVersion"))
            {
                if (key == null) return Environment.OSVersion.VersionString;
                var name = Convert.ToString(key.GetValue("ProductName"), CultureInfo.InvariantCulture);
                var build = Convert.ToString(key.GetValue("CurrentBuildNumber"), CultureInfo.InvariantCulture);
                if (int.TryParse(build, out var buildNumber) && buildNumber >= 22000 && name.StartsWith("Windows 10", StringComparison.Ordinal))
                    name = name.Replace("Windows 10", "Windows 11");
                var revision = Convert.ToString(key.GetValue("UBR"), CultureInfo.InvariantCulture);
                var display = Convert.ToString(key.GetValue("DisplayVersion"), CultureInfo.InvariantCulture);
                return name + (display.Length == 0 ? "" : " " + display)
                    + " · " + (Environment.Is64BitOperatingSystem ? "64 位" : "32 位")
                    + " · Build " + build + (revision.Length == 0 ? "" : "." + revision);
            }
        }

        private static string ReadCpu()
        {
            using (var processors = Registry.LocalMachine.OpenSubKey(@"HARDWARE\DESCRIPTION\System\CentralProcessor"))
            {
                if (processors == null) return "未检测到 CPU 型号；当前进程可用逻辑处理器：" + Environment.ProcessorCount;
                var entries = processors.GetSubKeyNames();
                var names = entries.Select(entry =>
                {
                    using (var processor = processors.OpenSubKey(entry))
                        return FormatCpuName(Convert.ToString(processor?.GetValue("ProcessorNameString"), CultureInfo.InvariantCulture));
                }).Where(name => name.Length != 0).Distinct(StringComparer.OrdinalIgnoreCase).ToArray();
                return (names.Length == 0 ? "未检测到 CPU 型号" : string.Join(" / ", names))
                    + " · " + entries.Length + " 逻辑处理器";
            }
        }

        internal static string FormatCpuName(string name)
        {
            var model = Regex.Replace(name ?? string.Empty, @"\((?:R|TM)\)|[®™]", "", RegexOptions.IgnoreCase);
            model = Regex.Replace(model.Trim(), @"^\d+(?:st|nd|rd|th)\s+Gen\s+", "", RegexOptions.IgnoreCase);
            return Regex.Replace(model, @"\s+", " ").Trim();
        }

        private static string ReadMemory()
        {
            var memory = new MemoryStatus { Length = (uint)Marshal.SizeOf(typeof(MemoryStatus)) };
            if (!GlobalMemoryStatusEx(ref memory)) throw new Win32Exception(Marshal.GetLastWin32Error());
            return FormatMemory(memory.TotalPhysical)
                + " · 可用 " + FormatMemory(memory.AvailablePhysical) + " · 使用率 " + memory.MemoryLoad + "%";
        }

        internal static string FormatMemory(ulong bytes) => (bytes / (1024.0 * 1024 * 1024)).ToString("F1", CultureInfo.InvariantCulture) + " GiB";

        private static string ReadFramework()
        {
            using (var machine = RegistryKey.OpenBaseKey(RegistryHive.LocalMachine, RegistryView.Registry32))
            using (var framework = machine.OpenSubKey(@"SOFTWARE\Microsoft\NET Framework Setup\NDP\v4\Full"))
            {
                var release = framework?.GetValue("Release") as int?;
                return DescribeFrameworkRelease(release) + " · CLR " + Environment.Version;
            }
        }

        internal static string DescribeFrameworkRelease(int? release)
        {
            if (!release.HasValue) return "未检出 Release 值";
            var version = release >= 533320 ? "≥4.8.1" : release >= 528040 ? "4.8" : "低于 4.8";
            return version + " · Release " + release.Value;
        }

        private static string ReadSystemRuntimeFiles(params string[] names)
        {
            var directory = Environment.GetFolderPath(Environment.Is64BitOperatingSystem
                ? Environment.SpecialFolder.SystemX86 : Environment.SpecialFolder.System);
            if (string.IsNullOrEmpty(directory)) return "未检出系统 x86 组件目录";
            return DescribeRuntimeFiles(names, name => DescribeRuntimeFile(Path.Combine(directory, name)));
        }

        internal static string DescribeRuntimeFiles(string[] names, Func<string, string> read)
        {
            var versions = names.Select(name => ReadValue(() => read(name))).ToArray();
            if (versions.Length > 1 && Version.TryParse(versions[0], out var version) && version.Revision >= 0
                && versions.All(value => value == versions[0]))
                return string.Join(" / ", names) + " 均为 " + versions[0];
            return string.Join("；", names.Select((name, index) => name + " " + versions[index]));
        }

        internal static string CompactFileVersion(string version) =>
            Regex.Match(version ?? string.Empty, @"^\s*(\d+\.\d+\.\d+\.\d+)(?:\s|$|\()").Groups[1].Value;

        internal static string DescribeRuntimeFile(string path)
        {
            try
            {
                var version = FileVersionInfo.GetVersionInfo(path);
                if (string.IsNullOrWhiteSpace(version.FileVersion)) return "已检出（版本未记录）";
                var compact = CompactFileVersion(version.FileVersion);
                return compact.Length != 0 ? compact
                    : new Version(version.FileMajorPart, version.FileMinorPart, version.FileBuildPart, version.FilePrivatePart).ToString();
            }
            catch (FileNotFoundException) { return "未检出"; }
            catch (DirectoryNotFoundException) { return "未检出"; }
        }

        [StructLayout(LayoutKind.Sequential)]
        private struct MemoryStatus
        {
            public uint Length, MemoryLoad;
            public ulong TotalPhysical, AvailablePhysical, TotalPageFile, AvailablePageFile,
                TotalVirtual, AvailableVirtual, AvailableExtendedVirtual;
        }

        [DllImport("kernel32.dll", SetLastError = true)]
        [return: MarshalAs(UnmanagedType.Bool)]
        private static extern bool GlobalMemoryStatusEx(ref MemoryStatus status);
    }
}
