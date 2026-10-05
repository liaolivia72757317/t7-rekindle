using System;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media.Imaging;
using System.Windows.Media;
using System.Windows.Input;

namespace T7.Rekindle.Desktop.Views
{
    public partial class LaunchPage : UserControl
    {
        private ToolTip _focusedHint;
        public LaunchPage() { InitializeComponent(); }
        private void OnPageSizeChanged(object sender, SizeChangedEventArgs e)
        {
            HeroPanel.Height = Math.Max(140, Math.Min(384, ActualHeight - 410));
        }
        private void OnHeroSizeChanged(object sender, SizeChangedEventArgs e)
        {
            HeroArtwork.Clip = new RectangleGeometry(new Rect(e.NewSize), 8, 8);
        }
        private void OnHeroFailed(object sender, ExceptionRoutedEventArgs e)
        {
            HeroArtwork.Source = new BitmapImage(new Uri("/T7-Rekindle;component/Resources/Assets/art/battlefield-light-reconstructed.png", UriKind.Relative));
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
