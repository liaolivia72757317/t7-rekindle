using System;
using System.Collections.Generic;
using System.IO;
using System.Threading;
using System.Threading.Tasks;
using T7.Rekindle.Core;

namespace T7.Rekindle.Desktop.ViewModels
{
    public sealed partial class MainWindowViewModel
    {
        private readonly Queue<string> _nativeLogs = new Queue<string>();
        private ulong _logCursor;
        private int _logPollActive;
        private string _nativeLogText = "暂无日志。启动与检查记录会显示在这里。";
        public string NativeLogText { get => _nativeLogText; private set => SetProperty(ref _nativeLogText, value); }
        public bool HasSessionLog => _nativeLogs.Count > 0 || _failureMessage.Length != 0 || _snapshot.State == SessionState.Failed;
        public static string LogDirectory => Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "T7-Rekindle", "logs");

        private void AppendLog(string level, string message)
        {
            var text = DateTime.Now.ToString("HH:mm:ss") + "  " + level + "  [Launcher] " + message;
            _log.Info(text);
            EnqueueLog(text);
            NativeLogText = string.Join(Environment.NewLine, _nativeLogs);
        }

        private void EnqueueLog(string text)
        {
            _nativeLogs.Enqueue(text);
            while (_nativeLogs.Count > 100) _nativeLogs.Dequeue();
            OnPropertyChanged(nameof(HasSessionLog));
        }

        private async void Poll()
        {
            if (_disposed) return;
            Refresh();
            if (Interlocked.CompareExchange(ref _logPollActive, 1, 0) != 0) return;
            try
            {
                var cursor = _logCursor;
                var batch = await Task.Run(() =>
                {
                    var result = _bridge.ReadLogRecords(ref cursor);
                    return new LogBatch { Cursor = cursor, Result = result };
                });
                if (_disposed) return;
                _logCursor = batch.Cursor;
                if (batch.Result.Gap) EnqueueLog("[NativeBridge] 日志窗口已轮转，完整记录请查看日志目录。");
                foreach (var record in batch.Result.Records)
                {
                    var text = record.Text;
                    EnqueueLog(text);
                    _log.Info(text);
                }
                if (_nativeLogs.Count > 0) NativeLogText = string.Join(Environment.NewLine, _nativeLogs);
            }
            catch (ObjectDisposedException) when (_disposed) { }
            catch (Exception error)
            {
                _log.Error("日志读取失败", error);
                if (!_disposed) NativeLogText = "日志读取失败：" + error.Message;
            }
            finally { Volatile.Write(ref _logPollActive, 0); }
        }

        private void OpenLogs()
        {
            try { _interaction.OpenDirectory(LogDirectory); }
            catch (Exception error)
            {
                ReportUiError("打开日志目录失败", error);
                _interaction.ShowText("日志目录", "打开日志目录失败，请检查权限。可复制下方路径手动访问：\n\n" + LogDirectory + "\n\n" + error.Message);
            }
        }

        private void CopyLogs()
        {
            if (!_interaction.Confirm("日志可能包含个人路径、玩家名称等信息。分享前请检查并移除个人信息。\n复制本次运行的诊断信息？（不会自动上传）")) return;
            try
            {
                _interaction.CopyText(EndpointText + Environment.NewLine + NativeLogText);
                ShowNotice("当前日志已复制；分享前请检查个人信息。", false);
            }
            catch (Exception error) { ReportUiError("复制日志失败", error); }
        }

        private sealed class LogBatch
        {
            public ulong Cursor { get; set; }
            public LogReadResult Result { get; set; }
        }
    }
}
