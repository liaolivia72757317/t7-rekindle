using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using System.Windows.Input;

namespace T7.Rekindle.Desktop.Views
{
    public partial class LaunchPage : UserControl
    {
        private ToolTip _focusedHint;
        public LaunchPage() { InitializeComponent(); }
        private void OnHeroSizeChanged(object sender, SizeChangedEventArgs e)
        {
            HeroPanel.Height = e.NewSize.Width * 790 / 1991;
            HeroArtwork.Clip = new RectangleGeometry(new Rect(e.NewSize), 8, 8);
        }
        private void OnHeroFailed(object sender, ExceptionRoutedEventArgs e)
        {
            HeroArtwork.Visibility = Visibility.Collapsed;
        }
        private void OnValueFocused(object sender, KeyboardFocusChangedEventArgs e)
        {
            if (_focusedHint != null) _focusedHint.IsOpen = false;
            var text = (TextBlock)sender;
            _focusedHint = new ToolTip { Content = text.Text, PlacementTarget = text, IsOpen = true };
        }
        private void OnValueBlurred(object sender, KeyboardFocusChangedEventArgs e)
        {
            if (_focusedHint != null) _focusedHint.IsOpen = false;
        }
    }
}
