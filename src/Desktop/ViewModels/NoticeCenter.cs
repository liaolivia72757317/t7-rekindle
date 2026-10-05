using System;
using System.Collections.Generic;
using System.Collections.ObjectModel;
using System.Linq;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;

namespace T7.Rekindle.Desktop.ViewModels
{
    public enum NoticeSeverity { Success, Info, Warning, Error }

    public sealed class Notice : ObservableObject
    {
        internal Notice(string key, string message, NoticeSeverity severity, DateTime createdAt, Action close,
            string actionText, Action action, bool persistent, TimeSpan? duration)
        {
            Key = key; Message = message; Severity = severity; CreatedAt = createdAt;
            ActionText = actionText ?? string.Empty;
            ActionCommand = new RelayCommand(() => action?.Invoke(), () => action != null);
            CloseCommand = new RelayCommand(close);
            Remaining = severity == NoticeSeverity.Error || persistent
                ? TimeSpan.MaxValue : duration ?? TimeSpan.FromSeconds(severity == NoticeSeverity.Success ? 3 : severity == NoticeSeverity.Info ? 4 : 6);
        }
        internal string Key { get; }
        internal TimeSpan Remaining { get; set; }
        public string Message { get; }
        public NoticeSeverity Severity { get; }
        public DateTime CreatedAt { get; }
        public string TimeText => CreatedAt.ToLocalTime().ToString("HH:mm:ss");
        public string ActionText { get; }
        public bool HasAction => ActionText.Length != 0;
        public string Icon => Severity == NoticeSeverity.Success ? "check-circle" : Severity == NoticeSeverity.Warning ? "alert-triangle"
            : Severity == NoticeSeverity.Error ? "x-circle" : "info";
        public bool IsPaused { get; set; }
        public RelayCommand CloseCommand { get; }
        public RelayCommand ActionCommand { get; }
    }

    public sealed class NoticeCenter
    {
        private readonly List<Notice> _waiting = new List<Notice>();
        private DateTime _lastTick = DateTime.UtcNow;
        public ObservableCollection<Notice> Visible { get; } = new ObservableCollection<Notice>();
        public ObservableCollection<Notice> History { get; } = new ObservableCollection<Notice>();
        public bool IsActive { get; set; } = true;
        internal int WaitingCount => _waiting.Count;

        public void Publish(string key, string message, NoticeSeverity severity, string actionText = null, Action action = null,
            bool persistent = false, TimeSpan? duration = null)
            => PublishAt(key, message, severity, DateTime.UtcNow, actionText, action, persistent, duration);

        internal void PublishAt(string key, string message, NoticeSeverity severity, DateTime now, string actionText = null,
            Action action = null, bool persistent = false, TimeSpan? duration = null)
        {
            if (string.IsNullOrWhiteSpace(message)) return;
            if (History.Any(item => item.Key == key && now - item.CreatedAt < TimeSpan.FromSeconds(2))) return;
            Tick(now);
            Notice notice = null;
            notice = new Notice(key, message, severity, now, () => Dismiss(notice), actionText, action, persistent, duration);
            History.Insert(0, notice);
            while (History.Count > 20)
            {
                var oldest = History.LastOrDefault(item => item.Severity != NoticeSeverity.Error) ?? History.Last();
                History.Remove(oldest);
            }
            if (Visible.Count < 2) Visible.Add(notice);
            else
            {
                if (severity == NoticeSeverity.Error)
                {
                    var replace = Visible.FirstOrDefault(item => item.Severity != NoticeSeverity.Error);
                    if (replace != null) { Visible.Remove(replace); Visible.Add(notice); return; }
                }
                if (_waiting.Count == 5)
                {
                    var replace = _waiting.FindIndex(item => item.Severity != NoticeSeverity.Error);
                    if (severity != NoticeSeverity.Error || replace < 0) return;
                    _waiting.RemoveAt(replace);
                }
                if (severity == NoticeSeverity.Error) _waiting.Insert(0, notice);
                else _waiting.Add(notice);
            }
        }

        public void Dismiss(Notice notice)
        {
            Visible.Remove(notice);
            _waiting.Remove(notice);
            while (Visible.Count < 2 && _waiting.Count > 0)
            {
                var next = _waiting[0]; _waiting.RemoveAt(0); Visible.Add(next);
            }
        }

        internal void Resolve(string key)
        {
            foreach (var notice in _waiting.Where(item => item.Key == key).ToArray()) _waiting.Remove(notice);
            foreach (var notice in Visible.Where(item => item.Key == key).ToArray()) Dismiss(notice);
        }

        internal void Tick(DateTime now)
        {
            var elapsed = now > _lastTick ? now - _lastTick : TimeSpan.Zero;
            _lastTick = now;
            if (!IsActive) return;
            foreach (var notice in Visible.ToArray())
            {
                if (notice.IsPaused || notice.Remaining == TimeSpan.MaxValue) continue;
                notice.Remaining -= elapsed;
                if (notice.Remaining <= TimeSpan.Zero) Dismiss(notice);
            }
        }
    }
}
