using System;
using System.IO;
using System.Linq;
using System.Runtime.Serialization;
using System.Security.Cryptography;
using System.Threading;
using System.Threading.Tasks;
using T7R.Core;

namespace T7R.Battle
{
    public sealed partial class ClientGateway
    {
        private Func<CancellationToken, Task> _profileChanged;
        private Func<uint, CancellationToken, Task> _matchMapSelected;

        internal void SetProfileChangedHandler(Func<CancellationToken, Task> handler) =>
            Volatile.Write(ref _profileChanged, handler);

        internal void SetMatchMapSelectedHandler(Func<uint, CancellationToken, Task> handler) =>
            Volatile.Write(ref _matchMapSelected, handler);

        internal static uint ChooseMatchMap(uint[] mapIds)
        {
            if (mapIds == null || mapIds.Length == 0 || mapIds.Length > 256 ||
                mapIds.Any(id => id == 0) || mapIds.Distinct().Count() != mapIds.Length)
                throw new ArgumentException("Invalid match map pool.");
            var count = (ulong)mapIds.Length;
            var limit = (1UL << 32) - ((1UL << 32) % count);
            var bytes = new byte[4];
            using (var random = RandomNumberGenerator.Create())
            {
                uint value;
                do { random.GetBytes(bytes); value = BitConverter.ToUInt32(bytes, 0); } while (value >= limit);
                return mapIds[value % (uint)count];
            }
        }

        private async Task CompleteRequestAsync(byte[] bytes, ulong epoch, CancellationToken cancellation)
        {
            var response = await DispatchAsync(bytes, epoch, cancellation).ConfigureAwait(false);
            cancellation.ThrowIfCancellationRequested();
            if (response.Success && response.ProfileChanged)
            {
                var handler = Volatile.Read(ref _profileChanged);
                if (handler != null) await handler(cancellation).ConfigureAwait(false);
            }
            // A timeout, disconnect or epoch change legitimately invalidates a response.
            _native.Complete(PlatformProfileJson.Serialize(response));
        }

        private async Task<Response> DispatchAsync(byte[] bytes, ulong epoch, CancellationToken cancellation)
        {
            var request = PlatformProfileJson.Deserialize<Request>(bytes);
            if (request.Version != 1 || request.Epoch != epoch || request.RequestId == 0 ||
                (request.Operation != "readIdentity" && request.Operation != "readProfile" &&
                 request.Operation != "setFormation" && request.Operation != "setWeapons" && request.Operation != "prepareMatch"))
                throw new InvalidDataException("Unsupported platform request.");
            var response = new Response { Version = 1, Epoch = epoch, RequestId = request.RequestId };
            try
            {
                using (var deadline = CancellationTokenSource.CreateLinkedTokenSource(cancellation))
                {
                    deadline.CancelAfter(request.Operation == "prepareMatch" ? 55000 : 4000);
                    switch (request.Operation)
                    {
                        case "prepareMatch":
                            var handler = Volatile.Read(ref _matchMapSelected);
                            if (handler == null) throw new InvalidOperationException("Match map selection is not attached.");
                            response.MapId = ChooseMatchMap(request.MapIds);
                            await handler(response.MapId, deadline.Token).ConfigureAwait(false);
                            break;
                        case "readIdentity":
                            var identity = await _backend.ReadIdentityAsync(deadline.Token).ConfigureAwait(false);
                            if (identity == null) throw new InvalidDataException("Platform identity is missing.");
                            response.Identity = new Identity { DisplayName = identity.DisplayName, Portrait = identity.Portrait, Level = identity.Level };
                            break;
                        case "readProfile":
                            response.Profile = PlatformProfileJson.ToData(await _backend.ReadProfileAsync(deadline.Token).ConfigureAwait(false));
                            break;
                        default:
                            PlatformProfileUpdate update;
                            if (request.Operation == "setFormation")
                                update = await _backend.SetFormationAsync(request.Formation, deadline.Token).ConfigureAwait(false);
                            else
                                update = await _backend.SetWeaponsAsync(request.HeroId,
                                    request.Slots?.Select(slot => slot.ToSlot()).ToArray(), deadline.Token).ConfigureAwait(false);
                            response.Previous = PlatformProfileJson.ToData(update.Previous);
                            response.Profile = PlatformProfileJson.ToData(update.Profile);
                            response.ProfileChanged = update.Profile.Revision != update.Previous.Revision;
                            break;
                    }
                    // A successful write has already committed. Do not turn it into
                    // a reported failure because its deadline expired during flush.
                    response.Success = true;
                }
            }
            catch (OperationCanceledException) when (cancellation.IsCancellationRequested) { throw; }
            catch (Exception error)
            {
                Volatile.Write(ref _lastBackendError, error.GetType().Name + ": " + error.Message);
                response.Error = error is ArgumentException && (request.Operation == "setFormation" || request.Operation == "setWeapons")
                    ? "invalidProfile" : "backendFailure";
            }
            return response;
        }

        [DataContract] private sealed class Request
        {
            [DataMember(Name = "version", IsRequired = true)] public uint Version { get; set; }
            [DataMember(Name = "epoch", IsRequired = true)] public ulong Epoch { get; set; }
            [DataMember(Name = "requestId", IsRequired = true)] public ulong RequestId { get; set; }
            [DataMember(Name = "operation", IsRequired = true)] public string Operation { get; set; }
            [DataMember(Name = "formation")] public int[] Formation { get; set; }
            [DataMember(Name = "heroId")] public int HeroId { get; set; }
            [DataMember(Name = "slots")] public PlatformProfileJson.SlotData[] Slots { get; set; }
            [DataMember(Name = "mapIds")] public uint[] MapIds { get; set; }
        }
        [DataContract] private sealed class Response
        {
            [IgnoreDataMember] public bool ProfileChanged { get; set; }
            [DataMember(Name = "version")] public uint Version { get; set; }
            [DataMember(Name = "epoch")] public ulong Epoch { get; set; }
            [DataMember(Name = "requestId")] public ulong RequestId { get; set; }
            [DataMember(Name = "success")] public bool Success { get; set; }
            [DataMember(Name = "mapId", EmitDefaultValue = false)] public uint MapId { get; set; }
            [DataMember(Name = "error", EmitDefaultValue = false)] public string Error { get; set; }
            [DataMember(Name = "identity", EmitDefaultValue = false)] public Identity Identity { get; set; }
            [DataMember(Name = "profile", EmitDefaultValue = false)] public PlatformProfileJson.ProfileData Profile { get; set; }
            [DataMember(Name = "previous", EmitDefaultValue = false)] public PlatformProfileJson.ProfileData Previous { get; set; }
        }
        [DataContract] private sealed class Identity
        {
            [DataMember(Name = "displayName")] public string DisplayName { get; set; }
            [DataMember(Name = "portrait")] public int Portrait { get; set; }
            [DataMember(Name = "level")] public int Level { get; set; }
        }
    }
}
