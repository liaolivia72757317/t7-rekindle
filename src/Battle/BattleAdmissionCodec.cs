using System;
using System.IO;
using T7R.Core;

namespace T7R.Battle
{
    public static class BattleAdmissionCodec
    {
        public static byte[] Encode(BattleAdmission admission)
        {
            if (admission == null) throw new ArgumentNullException(nameof(admission));
            var ticket = admission.GetTicket();
            var bytes = new byte[18 + ticket.Length];
            Put(bytes, 0, BattleRequirements.ContractVersion, 2);
            bytes[2] = (byte)admission.Requirements.AuthorityMode;
            Put(bytes, 4, admission.Requirements.RulesVersion, 4);
            Put(bytes, 8, admission.Requirements.ResourceVersion, 4);
            Put(bytes, 12, admission.Requirements.MapId, 4);
            Put(bytes, 16, (uint)ticket.Length, 2);
            Buffer.BlockCopy(ticket, 0, bytes, 18, ticket.Length);
            return bytes;
        }

        public static BattleAdmission Decode(byte[] bytes)
        {
            if (bytes == null) throw new ArgumentNullException(nameof(bytes));
            if (bytes.Length < 18) throw new InvalidDataException("Truncated admission.");
            if (Read(bytes, 0, 2) != BattleRequirements.ContractVersion || bytes[3] != 0)
                throw new InvalidDataException("Unsupported admission contract or reserved flags.");
            var size = Read(bytes, 16, 2);
            if (size == 0 || size > BattleAdmission.MaxTicketSize || bytes.Length != 18 + size)
                throw new InvalidDataException("Invalid admission ticket length.");
            try
            {
                var requirements = new BattleRequirements((BattleAuthorityMode)bytes[2], Read(bytes, 4, 4), Read(bytes, 8, 4), Read(bytes, 12, 4));
                var ticket = new byte[(int)size];
                Buffer.BlockCopy(bytes, 18, ticket, 0, ticket.Length);
                return new BattleAdmission(requirements, ticket);
            }
            catch (ArgumentException error) { throw new InvalidDataException("Invalid admission requirements.", error); }
        }

        private static uint Read(byte[] bytes, int offset, int size)
        {
            uint value = 0;
            for (int i = 0; i < size; ++i) value = (value << 8) | bytes[offset + i];
            return value;
        }

        private static void Put(byte[] bytes, int offset, uint value, int size)
        {
            for (int i = size - 1; i >= 0; --i) { bytes[offset + i] = (byte)value; value >>= 8; }
        }
    }
}
