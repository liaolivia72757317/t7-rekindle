using System;

namespace T7R.Core
{
    public enum BattleAuthorityMode : byte { ClientSampled = 1, ServerSimulated = 2 }

    public sealed class BattleRequirements
    {
        public const ushort ContractVersion = 1;
        public BattleAuthorityMode AuthorityMode { get; }
        public uint RulesVersion { get; }
        public uint ResourceVersion { get; }
        public uint MapId { get; }

        public BattleRequirements(BattleAuthorityMode authorityMode, uint rulesVersion, uint resourceVersion, uint mapId)
        {
            if (!Enum.IsDefined(typeof(BattleAuthorityMode), authorityMode)) throw new ArgumentOutOfRangeException(nameof(authorityMode));
            if (rulesVersion == 0) throw new ArgumentOutOfRangeException(nameof(rulesVersion));
            if (resourceVersion == 0) throw new ArgumentOutOfRangeException(nameof(resourceVersion));
            if (mapId == 0) throw new ArgumentOutOfRangeException(nameof(mapId));
            AuthorityMode = authorityMode; RulesVersion = rulesVersion; ResourceVersion = resourceVersion; MapId = mapId;
        }
    }

    public sealed class BattleAdmission
    {
        public const int MaxTicketSize = 256;
        private readonly byte[] _ticket;
        public BattleRequirements Requirements { get; }

        public BattleAdmission(BattleRequirements requirements, byte[] ticket)
        {
            Requirements = requirements ?? throw new ArgumentNullException(nameof(requirements));
            if (ticket == null) throw new ArgumentNullException(nameof(ticket));
            if (ticket.Length == 0 || ticket.Length > MaxTicketSize) throw new ArgumentException("Invalid admission ticket length.", nameof(ticket));
            _ticket = (byte[])ticket.Clone();
        }

        public byte[] GetTicket() => (byte[])_ticket.Clone();
    }

    public sealed class BattleCapabilities
    {
        private readonly BattleRequirements _requirements;
        private BattleCapabilities(BattleRequirements requirements) { _requirements = requirements; }

        public static BattleCapabilities ClientSampled(uint rulesVersion, uint resourceVersion, uint mapId)
            => new BattleCapabilities(new BattleRequirements(BattleAuthorityMode.ClientSampled, rulesVersion, resourceVersion, mapId));

        public bool Supports(BattleRequirements requested) => requested != null
            && requested.AuthorityMode == _requirements.AuthorityMode
            && requested.RulesVersion == _requirements.RulesVersion
            && requested.ResourceVersion == _requirements.ResourceVersion
            && requested.MapId == _requirements.MapId;
    }
}
