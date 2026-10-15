using System.ComponentModel;

namespace T7R.Desktop.ViewModels
{
    public sealed partial class MainWindowViewModel
    {
        public bool ArePlayerNameFieldsLocked => AreSessionFieldsLocked || Account.IsSignedIn || Account.IsRegistrationOpen;
        public bool CanEditPlayerName => !ArePlayerNameFieldsLocked;
        public string PlayerNameHint => Account.IsSignedIn ? "游戏名称由账号同步，如需修改请联系管理员；已运行游戏下次启动生效。"
            : Account.IsRegistrationOpen ? "请先完成游戏名称设置，或取消登录。" : "结束编辑后自动保存。";

        private void OnAccountChanged(object sender, PropertyChangedEventArgs e)
        {
            if (_disposed) return;
            if (Account.IsSignedIn && _playerName != Account.GameName)
            {
                _playerName = Account.GameName;
                _hasEditedPlayerName = false;
                SaveValidatedFields();
            }
            foreach (var property in new[] { nameof(PlayerName), nameof(SavedPlayerName), nameof(PlayerNameError),
                nameof(HasPlayerNameError), nameof(ShowPlayerNameError), nameof(NameFieldError), nameof(PlayerNameHint) })
                OnPropertyChanged(property);
            UpdatePresentation();
        }
    }
}
