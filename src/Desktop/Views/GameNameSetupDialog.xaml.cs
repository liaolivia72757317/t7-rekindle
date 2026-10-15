using System;
using System.Threading;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Input;
using T7R.Desktop.Services;
using T7R.Desktop.ViewModels;

namespace T7R.Desktop.Views
{
    public partial class GameNameSetupDialog : Window
    {
        private bool _composing;
        internal GameNameSetupDialog(GameNameSetupViewModel model)
        {
            InitializeComponent();
            DataContext = model;
            var layout = new WindowLayoutController(this, error => new LogService().Error("调整名称窗口失败", error), Width, Height);
            Action accepted = () => DialogResult = true;
            model.Accepted += accepted;
            Closed += (_, __) => { model.Accepted -= accepted; layout.Dispose(); model.Dispose(); };
            TextCompositionManager.AddPreviewTextInputStartHandler(this, (_, __) => _composing = true);
            TextCompositionManager.AddPreviewTextInputHandler(this, (_, __) => _composing = false);
        }
        private void OnNameKeyDown(object sender, KeyEventArgs e)
        {
            if (e.Key != Key.Enter || _composing || e.IsRepeat) return;
            var model = (GameNameSetupViewModel)DataContext;
            if (model.SubmitCommand.CanExecute(null)) model.SubmitCommand.Execute(null);
            e.Handled = true;
        }
        internal static Task<AccountSession> ShowAsync(Func<string, CancellationToken, Task<AccountSession>> submit, CancellationToken cancellation)
        {
            cancellation.ThrowIfCancellationRequested();
            using (var model = new GameNameSetupViewModel(submit, cancellation))
            {
                var dialog = new GameNameSetupDialog(model);
                bool closed = false;
                dialog.Closed += (_, __) => closed = true;
                using (cancellation.Register(() => dialog.Dispatcher.BeginInvoke(new Action(() => { if (!closed) dialog.Close(); }))))
                {
                    bool? accepted = DesktopInteraction.ShowModal(dialog);
                    return Task.FromResult(accepted == true ? model.Result : null);
                }
            }
        }
    }
}
