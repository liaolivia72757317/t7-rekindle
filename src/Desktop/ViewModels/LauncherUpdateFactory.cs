using System;
using System.Collections.Generic;
using System.Threading;
using System.Threading.Tasks;
using T7R.Desktop.Services;

namespace T7R.Desktop.ViewModels
{
    internal sealed class LauncherUpdateFactory
    {
        private readonly Func<UpdateChannel, Task<LauncherUpdateInfo>> _check;
        private readonly Func<LauncherUpdateInfo, UpdateDownloadViewModel> _download;
        private readonly Func<UpdateChannel, CancellationToken, Task<IReadOnlyList<LauncherHistoryEntry>>> _history;
        internal LauncherUpdateFactory(Func<UpdateChannel, Task<LauncherUpdateInfo>> check = null,
            Func<LauncherUpdateInfo, UpdateDownloadViewModel> download = null,
            Func<UpdateChannel, CancellationToken, Task<IReadOnlyList<LauncherHistoryEntry>>> history = null)
        { _check = check ?? LauncherInformation.CheckUpdateAsync; _download = download; _history = history; }

        internal AboutViewModel Create(IDesktopInteraction interaction, Func<UpdateChannel> channel, Func<bool> canStart) =>
            new AboutViewModel(interaction, () => _check(channel()), _download, _history, canStartUpdateOperation: canStart);
    }
}
