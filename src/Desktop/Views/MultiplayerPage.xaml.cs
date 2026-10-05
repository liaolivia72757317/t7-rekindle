using System;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Threading;

namespace T7.Rekindle.Desktop.Views
{
    public partial class MultiplayerPage : UserControl
    {
        public MultiplayerPage() { InitializeComponent(); }

        private void OnIsVisibleChanged(object sender, DependencyPropertyChangedEventArgs e)
        {
            if (!IsVisible) return;
            Dispatcher.BeginInvoke(DispatcherPriority.Input, new Action(() =>
            {
                if (IsVisible) ConstructionTitle.Focus();
            }));
        }
    }
}
