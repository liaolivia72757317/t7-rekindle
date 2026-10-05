using System.Collections.ObjectModel;
using CommunityToolkit.Mvvm.ComponentModel;

namespace T7.Rekindle.Desktop.ViewModels
{
    // The desktop has no room discovery or connection service yet.
    public sealed class RoomListViewModel : ObservableObject
    {
        public ObservableCollection<RoomRow> Rooms { get; } = new ObservableCollection<RoomRow>();
        public string ListMessage => "房间列表暂不可用";
        public string Availability => "联机功能开发中";
        public bool CanConnect => false;
    }

    public sealed class RoomRow
    {
        public string Name { get; set; }
        public string Owner { get; set; }
        public string Map { get; set; }
        public string Mode { get; set; }
        public string State { get; set; }
        public string Players { get; set; }
        public string Version { get; set; }
        public string Latency { get; set; } = "—";
    }
}
