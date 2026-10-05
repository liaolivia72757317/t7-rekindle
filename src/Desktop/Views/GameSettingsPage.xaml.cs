using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using System.Windows.Threading;
using T7.Rekindle.Desktop.ViewModels;

namespace T7.Rekindle.Desktop.Views
{
    public partial class GameSettingsPage : UserControl
    {
        private bool _isComposing;
        public GameSettingsPage()
        {
            InitializeComponent();
            TextCompositionManager.AddPreviewTextInputStartHandler(this, (_, __) => _isComposing = true);
            TextCompositionManager.AddPreviewTextInputHandler(this, (_, __) => _isComposing = false);
        }
        private void OnEditCompleted(object sender, KeyboardFocusChangedEventArgs e)
        {
            var field = (TextBox)sender;
            if (!_isComposing) { field.GetBindingExpression(TextBox.TextProperty)?.UpdateSource(); return; }
            Dispatcher.BeginInvoke(DispatcherPriority.Input, new System.Action(() =>
            {
                _isComposing = false;
                field.GetBindingExpression(TextBox.TextProperty)?.UpdateSource();
            }));
        }
        private void OnDirectoryDraftChanged(object sender, TextChangedEventArgs e)
        {
            var field = (TextBox)sender;
            if (field.IsKeyboardFocused && DataContext is MainWindowViewModel model && field.Text != model.ClientDirectory)
                model.BeginDirectoryDraft();
        }
        private void OnEditKeyDown(object sender, KeyEventArgs e)
        {
            if (e.Key != Key.Enter || _isComposing || e.IsRepeat) return;
            ((TextBox)sender).GetBindingExpression(TextBox.TextProperty)?.UpdateSource();
            e.Handled = true;
        }
    }
}
