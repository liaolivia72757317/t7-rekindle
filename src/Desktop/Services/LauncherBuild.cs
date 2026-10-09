using System;
using System.Globalization;
using System.IO;
using System.Text.RegularExpressions;

namespace T7.Rekindle.Desktop.Services
{
    public enum UpdateChannel { Stable, Preview }

    internal sealed class LauncherBuild
    {
        internal LauncherBuild(UpdateChannel channel, string version, long runId = 0, long runNumber = 0,
            int runAttempt = 0, string commitHash = "")
        {
            if (channel != UpdateChannel.Stable && channel != UpdateChannel.Preview)
                throw new ArgumentOutOfRangeException(nameof(channel));
            ReleaseMetadata.ParseVersion(version);
            if ((runId != 0 || runNumber != 0 || runAttempt != 0)
                && (channel != UpdateChannel.Preview || runId <= 0 || runNumber <= 0 || runAttempt <= 0
                    || !Regex.IsMatch(commitHash ?? "", @"\A(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})\z")))
                throw new InvalidDataException("预览构建身份无效。");
            Channel = channel;
            Version = version;
            RunId = runId;
            RunNumber = runNumber;
            RunAttempt = runAttempt;
            CommitHash = commitHash ?? string.Empty;
        }

        internal UpdateChannel Channel { get; }
        internal string Version { get; }
        internal long RunId { get; }
        internal long RunNumber { get; }
        internal int RunAttempt { get; }
        internal string CommitHash { get; }
        internal bool HasPreviewIdentity => RunNumber > 0;
        internal string Identity => Channel == UpdateChannel.Stable ? "stable:" + Version
            : "preview:" + RunId.ToString(CultureInfo.InvariantCulture) + ":" + RunAttempt.ToString(CultureInfo.InvariantCulture);
        internal string DisplayVersion => Channel == UpdateChannel.Stable ? Version
            : !HasPreviewIdentity ? Version + " · 开发构建"
            : Version + "p" + RunNumber.ToString(CultureInfo.InvariantCulture) + "." + RunAttempt.ToString(CultureInfo.InvariantCulture);

        internal void ApplyTo(LauncherUpdateInfo info)
        {
            info.CurrentVersion = Version;
            info.CurrentChannel = Channel;
            if (!info.HasPublishedRelease) return;
            int comparison;
            if (Channel != info.Channel || (Channel == UpdateChannel.Preview && !HasPreviewIdentity)) comparison = 1;
            else if (info.Channel == UpdateChannel.Stable)
                comparison = ReleaseMetadata.ParseVersion(info.TargetVersion).CompareTo(ReleaseMetadata.ParseVersion(Version));
            else comparison = ComparePreview(info.TargetBuild);
            info.IsNewVersion = comparison > 0;
            info.IsCurrentVersionAhead = comparison < 0;
        }

        private int ComparePreview(LauncherBuild target) => target.RunNumber == RunNumber
            ? target.RunAttempt.CompareTo(RunAttempt) : target.RunNumber.CompareTo(RunNumber);
    }
}
