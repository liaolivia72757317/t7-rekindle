using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using System.Windows.Automation.Peers;
using T7.Rekindle.Desktop.ViewModels;

namespace T7.Rekindle.Desktop.Views
{
    public partial class ToastHost : UserControl
    {
        public ToastHost() { InitializeComponent(); }
        private void OnMessageLoaded(object sender, RoutedEventArgs e)
        {
            if (!AutomationPeer.ListenerExists(AutomationEvents.LiveRegionChanged)) return;
            var peer = UIElementAutomationPeer.CreatePeerForElement((TextBlock)sender);
            peer?.RaiseAutomationEvent(AutomationEvents.LiveRegionChanged);
        }
        private void OnPauseChanged(object sender, MouseEventArgs e) => SetPaused((FrameworkElement)sender);
        private void OnFocusChanged(object sender, DependencyPropertyChangedEventArgs e) => SetPaused((FrameworkElement)sender);
        private static void SetPaused(FrameworkElement element)
        {
            if (element.DataContext is Notice notice) notice.IsPaused = element.IsMouseOver || element.IsKeyboardFocusWithin;
        }
    }
}
