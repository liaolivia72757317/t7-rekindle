using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using T7R.Desktop.Services;
using static T7R.Desktop.Services.NativeBridgeService;

namespace T7R.ManagedHarness
{
    internal static class NativeTitlePublicationTests
    {
        private const string EmptyTitles = "{\"titles\":[],\"equippedTitleId\":null}";

        internal static void Run()
        {
            foreach (var success in new[] { true, false }) ResultsSurviveSnapshots(success);
            PartialAcknowledgementAndRetry();
            EpochIsolation();
            RepeatedSnapshotAcknowledgement();
        }

        private static void ResultsSurviveSnapshots(bool success)
        {
            var queue = new TitlePublicationQueue();
            var native = new List<TitlePublication>();
            var forwarded = new List<TitlePublication>();
            queue.Publish(5, EmptyTitles, 42, success, native.Add);
            queue.Publish(5, EmptyTitles, 0, true, native.Add);
            queue.Publish(5, EmptyTitles, 43, !success, native.Add);
            queue.Publish(5, EmptyTitles, 0, true, native.Add);
            queue.Forward(new NativeTitleRequest(), forwarded.Add);
            Check(forwarded.Count == 0, "unacknowledged titles reached Rust");

            // Native may finish every publication between two managed polls.
            queue.Forward(Acknowledged(native.Last()), forwarded.Add);
            Check(forwarded.SequenceEqual(native), "a newer snapshot lost an outstanding result or changed publication order");
            Check(forwarded[0].RequestId == 42 && forwarded[0].Success == success
                && forwarded[2].RequestId == 43 && forwarded[2].Success == !success,
                "Rust request IDs or success values changed during native acknowledgement");
            queue.Forward(Acknowledged(native.Last()), forwarded.Add);
            Check(forwarded.Count == 4, "a repeated native acknowledgement duplicated results");
        }

        private static void PartialAcknowledgementAndRetry()
        {
            var queue = new TitlePublicationQueue();
            var native = new List<TitlePublication>();
            var forwarded = new List<TitlePublication>();
            queue.Publish(5, EmptyTitles, 7, true, native.Add);
            queue.Publish(5, EmptyTitles, 0, true, native.Add);
            queue.Publish(5, EmptyTitles, 8, false, native.Add);
            queue.Forward(Acknowledged(native[0]), forwarded.Add);
            Check(forwarded.SequenceEqual(native.Take(1)), "an unapplied catalogue was forwarded early");
            var failed = false;
            try
            {
                queue.Forward(Acknowledged(native[2]), value =>
                {
                    if (value.RequestId == 8) throw new IOException("fixture gateway failure");
                    forwarded.Add(value);
                });
            }
            catch (IOException) { failed = true; }
            Check(failed && forwarded.SequenceEqual(native.Take(2)), "forwarding failure lost the retry boundary");
            queue.Forward(Acknowledged(native[2]), forwarded.Add);
            Check(forwarded.SequenceEqual(native), "retry duplicated a forwarded result or dropped a failed one");

            failed = false;
            try { queue.Publish(5, EmptyTitles, 9, true, value => { throw new IOException("fixture native failure"); }); }
            catch (IOException) { failed = true; }
            Check(failed, "native publication failure was hidden");
            queue.Publish(5, EmptyTitles, 0, true, native.Add);
            queue.Forward(Acknowledged(native.Last()), forwarded.Add);
            Check(forwarded.SequenceEqual(native), "rejected native publication was retained for Rust");
        }

        private static void EpochIsolation()
        {
            var queue = new TitlePublicationQueue();
            var native = new List<TitlePublication>();
            var forwarded = new List<TitlePublication>();
            queue.Publish(5, EmptyTitles, 7, true, native.Add);
            queue.Publish(5, EmptyTitles, 0, true, native.Add);
            queue.Publish(6, EmptyTitles, 0, true, native.Add);
            queue.Forward(Acknowledged(native[1]), forwarded.Add);
            Check(forwarded.Count == 0, "an old account result was forwarded after epoch change");
            queue.Publish(5, EmptyTitles, 8, true, native.Add);
            Check(native.Count == 3, "a late old-epoch result was submitted to Native");
            queue.Forward(Acknowledged(native[2]), forwarded.Add);
            Check(forwarded.Count == 1 && forwarded[0].Epoch == 6, "old pending results blocked the new epoch");
            queue.Publish(6, EmptyTitles, 9, true, native.Add);
            var previous = native.Last();
            queue.Clear();
            queue.Publish(1, EmptyTitles, 0, true, native.Add);
            queue.Forward(Acknowledged(previous), forwarded.Add);
            Check(forwarded.Count == 1, "stopped session retained a title result");
            queue.Forward(Acknowledged(native.Last()), forwarded.Add);
            Check(forwarded.Count == 2 && forwarded[1].Epoch == 1, "new session could not publish after cleanup");
        }

        private static void RepeatedSnapshotAcknowledgement()
        {
            var queue = new TitlePublicationQueue();
            var native = new List<TitlePublication>();
            var forwarded = new List<TitlePublication>();
            queue.Publish(5, EmptyTitles, 0, true, native.Add);
            var previous = Acknowledged(native.Last());
            queue.Forward(previous, forwarded.Add);
            queue.Publish(5, EmptyTitles, 0, true, native.Add);
            queue.Forward(previous, forwarded.Add);
            Check(forwarded.Count == 1, "an old request-zero acknowledgement confirmed a newer catalogue");
            queue.Forward(Acknowledged(native.Last()), forwarded.Add);
            Check(forwarded.SequenceEqual(native) && native.All(value => value.PublicationId != 0),
                "snapshot publications lack distinct native acknowledgements");
        }

        private static NativeTitleRequest Acknowledged(TitlePublication value) => new NativeTitleRequest
        {
            AbiVersion = 1, StructSize = 32, Epoch = value.Epoch, RequestId = value.PublicationId
        };

        private static void Check(bool condition, string message)
        {
            if (!condition) throw new InvalidOperationException(message);
        }
    }
}
