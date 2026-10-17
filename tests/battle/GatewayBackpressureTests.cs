using System;
using System.Diagnostics;
using System.Linq;
using System.Linq.Expressions;
using System.Reflection;
using T7R.Battle;

namespace T7R.BattleHarness
{
    internal static class GatewayBackpressureTests
    {
        internal static void Run(string library)
        {
            using (var native = new ClientGatewayNative(library, LocalProfileTests.Resources))
            {
                var type = typeof(ClientGatewayNative);
                Func<string, FieldInfo> field = name => type.GetField(name, BindingFlags.Instance | BindingFlags.NonPublic);
                var status = 8;
                Func<int> dispatch = () => status;
                var dispatchField = field("_dispatchBattle");
                var parameters = dispatchField.FieldType.GetMethod("Invoke").GetParameters()
                    .Select(parameter => Expression.Parameter(parameter.ParameterType)).ToArray();
                dispatchField.SetValue(native, Expression.Lambda(dispatchField.FieldType,
                    Expression.Invoke(Expression.Constant(dispatch)), parameters).Compile());
                field("_battleStarted").SetValue(native, true);
                Program.Check(!native.TryDispatchBattleOutput(), "BUSY preserves the pending output without failing");
                Program.Check(field("_battleOutputBusySince").GetValue(native) != null, "BUSY starts a bounded retry budget");
                status = 0;
                Program.Check(native.TryDispatchBattleOutput(), "retry publishes the retained batch");
                Program.Check(field("_battleOutputBusySince").GetValue(native) == null, "successful retry clears the budget");
                status = 8;
                native.TryDispatchBattleOutput();
                field("_battleOutputBusySince").SetValue(native, Stopwatch.GetTimestamp() - 6 * Stopwatch.Frequency);
                Program.Throws<TimeoutException>(() => native.TryDispatchBattleOutput());
                status = 5;
                Program.Check(!native.TryDispatchBattleOutput() && field("_battleOutputBusySince").GetValue(native) == null,
                    "EMPTY clears backpressure without pretending a batch was sent");
                status = 3;
                Program.Throws<InvalidOperationException>(() => native.TryDispatchBattleOutput());
                native.Stop();
                Program.Check(!native.TryDispatchBattleOutput(), "stopped battle does not retry output");
            }
        }
    }
}
