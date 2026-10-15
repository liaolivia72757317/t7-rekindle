using System;
using System.Threading;
using System.Threading.Tasks;
using Newtonsoft.Json;

namespace T7R.Desktop.Services
{
    public sealed class AccountSession
    {
        [JsonConstructor]
        public AccountSession(string sessionId, string sessionToken, DateTimeOffset expiresAt, string accountId,
            string loginName, string machineId, string qqNumber, string nickname, string avatarUrl, string gameName = null)
        {
            SessionId = sessionId; SessionToken = sessionToken; ExpiresAt = expiresAt; AccountId = accountId;
            LoginName = loginName; MachineId = machineId; QqNumber = qqNumber; Nickname = nickname; AvatarUrl = avatarUrl; GameName = gameName;
        }
        public string SessionId { get; }
        public string SessionToken { get; }
        public DateTimeOffset ExpiresAt { get; }
        public string AccountId { get; }
        public string LoginName { get; }
        public string MachineId { get; }
        public string QqNumber { get; }
        public string Nickname { get; }
        public string AvatarUrl { get; }
        public string GameName { get; }
        internal AccountSession WithToken(string token) => new AccountSession(SessionId, token, ExpiresAt,
            AccountId, LoginName, MachineId, QqNumber, Nickname, AvatarUrl, GameName);
        internal AccountSession WithGameName(string name) => new AccountSession(SessionId, SessionToken, ExpiresAt,
            AccountId, LoginName, MachineId, QqNumber, Nickname, AvatarUrl, name);
    }

    public sealed class AccountGameName
    {
        [JsonConstructor]
        public AccountGameName(string gameName) { GameName = gameName; }
        public string GameName { get; }
    }

    public sealed class AccountConnection
    {
        [JsonConstructor]
        public AccountConnection(string connectionId, DateTimeOffset leaseExpiresAt, int heartbeatIntervalSeconds)
        { ConnectionId = connectionId; LeaseExpiresAt = leaseExpiresAt; HeartbeatIntervalSeconds = heartbeatIntervalSeconds; }
        public string ConnectionId { get; }
        public DateTimeOffset LeaseExpiresAt { get; }
        public int HeartbeatIntervalSeconds { get; }
    }

    public sealed class AccountKick
    {
        [JsonConstructor]
        public AccountKick(string sessionId, string connectionId, string reason, string groupNumber = null)
        { SessionId = sessionId; ConnectionId = connectionId; Reason = reason; GroupNumber = groupNumber; }
        public string SessionId { get; }
        public string ConnectionId { get; }
        public string Reason { get; }
        public string GroupNumber { get; }
    }

    public sealed class AccountApiException : Exception
    {
        public AccountApiException(int status, string code, string message = null) : base(message ?? "账号请求失败：" + status)
        { Status = status; Code = code; }
        public int Status { get; }
        public string Code { get; }
    }

    public interface IAccountApi : IDisposable
    {
        Task<AccountSession> LoginAsync(string machineId, string cookie, CancellationToken cancellation);
        Task<AccountSession> CompleteRegistrationAsync(string machineId, string cookie, string gameName, CancellationToken cancellation);
        Task<AccountGameName> GameNameAsync(AccountSession session, CancellationToken cancellation);
        Task<AccountGameName> SetGameNameAsync(AccountSession session, string gameName, CancellationToken cancellation);
        Task<AccountSession> CurrentAsync(AccountSession session, CancellationToken cancellation);
        Task<AccountConnection> OnlineAsync(AccountSession session, CancellationToken cancellation);
        Task<AccountConnection> HeartbeatAsync(AccountSession session, string connectionId, CancellationToken cancellation);
        Task OfflineAsync(AccountSession session, string connectionId, CancellationToken cancellation);
        Task LogoutAsync(AccountSession session, CancellationToken cancellation);
        Task ListenAsync(AccountSession session, string connectionId, Action ready, Action<AccountKick> kicked,
            CancellationToken cancellation);
    }

    public interface IAccountSessionStore
    {
        AccountSession Load();
        void Save(AccountSession session);
        void Clear();
    }
}
