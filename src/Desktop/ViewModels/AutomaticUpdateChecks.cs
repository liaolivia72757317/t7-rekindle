using System;
using System.Collections.Generic;

namespace T7R.Desktop.ViewModels
{
    internal sealed class AutomaticUpdateChecks
    {
        private static readonly TimeSpan Interval = TimeSpan.FromMinutes(30);
        private readonly AboutViewModel _about;
        private readonly HashSet<string> _announced = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        private DateTime? _nextCheckUtc;
        private bool _automaticCheck;
        internal IEnumerable<string> AnnouncedIdentities => _announced;
        internal AutomaticUpdateChecks(AboutViewModel about) { _about = about; }
        internal bool Start(DateTime utcNow)
        {
            if (_nextCheckUtc.HasValue) return false;
            _nextCheckUtc = utcNow + Interval;
            return true;
        }
        internal void Tick(DateTime utcNow)
        {
            if (!_nextCheckUtc.HasValue || utcNow < _nextCheckUtc.Value) return;
            _nextCheckUtc = utcNow + Interval;
            Trigger();
        }
        internal void Trigger()
        {
            if (!_about.CheckUpdateCommand.CanExecute(null)) return;
            _automaticCheck = true;
            _about.CheckUpdateCommand.Execute(null);
        }
        internal bool Complete(bool canNotify)
        {
            var automatic = _automaticCheck;
            _automaticCheck = false;
            return canNotify && automatic && _about.HasNewUpdate && _announced.Add(_about.UpdateReminderIdentity);
        }
    }
}
