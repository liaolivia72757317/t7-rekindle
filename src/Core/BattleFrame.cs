using System;

namespace T7R.Core
{
    public enum BattleMessageKind : byte
    {
        Admit = 1, Admitted = 2, OpenChannel = 3, GameData = 4, CloseChannel = 5,
        WorldSample = 6, MatchState = 7, MatchEnded = 8, Ping = 9, Pong = 10,
        PlayerIntent = 11, MatchAborted = 12, MotionAttempt = 13, ContactBatch = 14
    }

    public enum BattleChannel : byte { None = 0, Login = 1, Logic = 2, Instance = 3 }

    public sealed class BattleFrame
    {
        public const byte ProtocolVersion = 1;
        public const int HeaderSize = 36;
        public const int MaxFrameSize = 4 * 1024 * 1024;
        private readonly byte[] _payload;
        public BattleMessageKind Kind { get; }
        public BattleChannel Channel { get; }
        public ulong RoomId { get; }
        public ulong MatchId { get; }
        public ulong PlayerId { get; }
        public ulong Sequence { get; }
        public int PayloadLength => _payload.Length;

        public BattleFrame(BattleMessageKind kind, BattleChannel channel, ulong roomId, ulong matchId,
            ulong playerId, ulong sequence, byte[] payload)
        {
            if (!Enum.IsDefined(typeof(BattleMessageKind), kind)) throw new ArgumentOutOfRangeException(nameof(kind));
            if (!Enum.IsDefined(typeof(BattleChannel), channel)) throw new ArgumentOutOfRangeException(nameof(channel));
            bool gameChannel = kind == BattleMessageKind.OpenChannel || kind == BattleMessageKind.GameData
                || kind == BattleMessageKind.CloseChannel;
            if (gameChannel == (channel == BattleChannel.None)) throw new ArgumentException("Game messages require a role; other messages have no role.");
            if (payload == null) throw new ArgumentNullException(nameof(payload));
            if (payload.Length > MaxFrameSize - HeaderSize) throw new ArgumentOutOfRangeException(nameof(payload));
            Kind = kind; Channel = channel; RoomId = roomId; MatchId = matchId;
            PlayerId = playerId; Sequence = sequence;
            _payload = (byte[])payload.Clone();
        }

        public byte[] GetPayload() { return (byte[])_payload.Clone(); }
    }
}
