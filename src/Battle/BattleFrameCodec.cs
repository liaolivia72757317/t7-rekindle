using System;
using System.IO;
using T7R.Core;

namespace T7R.Battle
{
    public static class BattleFrameCodec
    {
        public static byte[] Encode(BattleFrame frame)
        {
            if (frame == null) throw new ArgumentNullException(nameof(frame));
            var bytes = new byte[4 + BattleFrame.HeaderSize + frame.PayloadLength];
            Put(bytes, 0, (ulong)(bytes.Length - 4), 4);
            bytes[4] = BattleFrame.ProtocolVersion;
            bytes[5] = (byte)frame.Kind;
            bytes[6] = (byte)frame.Channel;
            Put(bytes, 8, frame.RoomId, 8);
            Put(bytes, 16, frame.MatchId, 8);
            Put(bytes, 24, frame.PlayerId, 8);
            Put(bytes, 32, frame.Sequence, 8);
            var payload = frame.GetPayload();
            Buffer.BlockCopy(payload, 0, bytes, 40, payload.Length);
            return bytes;
        }

        // Decode only a body whose bounded length prefix has already been read.
        public static BattleFrame DecodeBody(byte[] body)
        {
            if (body == null) throw new ArgumentNullException(nameof(body));
            if (body.Length < BattleFrame.HeaderSize || body.Length > BattleFrame.MaxFrameSize)
                throw new InvalidDataException("Battle frame length is outside the protocol limit.");
            if (body[0] != BattleFrame.ProtocolVersion || body[3] != 0)
                throw new InvalidDataException("Unsupported battle version or reserved flags.");
            var payload = new byte[body.Length - BattleFrame.HeaderSize];
            Buffer.BlockCopy(body, BattleFrame.HeaderSize, payload, 0, payload.Length);
            try
            {
                return new BattleFrame((BattleMessageKind)body[1], (BattleChannel)body[2],
                    Read(body, 4, 8), Read(body, 12, 8), Read(body, 20, 8), Read(body, 28, 8), payload);
            }
            catch (ArgumentException error) { throw new InvalidDataException("Invalid battle header.", error); }
        }

        public static int DecodeLength(byte[] prefix)
        {
            if (prefix == null || prefix.Length != 4) throw new ArgumentException("Expected a four-byte length prefix.", nameof(prefix));
            ulong value = Read(prefix, 0, 4);
            if (value < BattleFrame.HeaderSize || value > BattleFrame.MaxFrameSize)
                throw new InvalidDataException("Battle frame length is outside the protocol limit.");
            return (int)value;
        }

        private static ulong Read(byte[] bytes, int offset, int size)
        {
            ulong value = 0;
            for (int i = 0; i < size; ++i) value = (value << 8) | bytes[offset + i];
            return value;
        }

        private static void Put(byte[] bytes, int offset, ulong value, int size)
        {
            for (int i = size - 1; i >= 0; --i) { bytes[offset + i] = (byte)value; value >>= 8; }
        }
    }
}
