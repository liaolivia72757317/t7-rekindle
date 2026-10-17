from pathlib import Path

from Business.scripts import contracts
from Business.scripts.codec import battle_flow, kill_flow, leave_flow, settlement_flow


ROWS = Path(__file__).resolve().parents[1] / 'fixtures/protocol/battle-outcome-wire-v1.txt'


def test_outcome_wire_corpus_matches_original_client_codecs():
    expected = {
        'guard-value': (4, battle_flow.encode_parry_value_notify(value=37.5)),
        'dead': (4, battle_flow.encode_battle_dead_notify(entity_rid=202, server_time_ms=1300)),
        'death-state': (4, battle_flow.encode_battle_state_sync(instance_id=202, seq_no=38,
                           state=42, state_change_ms=300, state_time_ms=0, target_rid=101)),
        'kill': (10, kill_flow.encode_kill_event(server_time_ms=1300, source_rid=101,
                                                  target_rid=202, weapon_tid=701)),
        'streak': (12, kill_flow.encode_score_stat_message(server_time_ms=1300,
                        entity_rid=101, count=2)),
        'butcher-end': (12, kill_flow.encode_butcher_killer_message(server_time_ms=1300,
                             entity_rid=101)),
        'score-update': (12, kill_flow.encode_score_update_event(server_time_ms=1300, entity_rid=101,
                           previous_kills=2, kills=3, previous_points=200, points=300,
                           previous_deaths=1, deaths=1, stat_message_id=730000)),
        'camp-update': (23, kill_flow.encode_camp_kill_update(server_time_ms=1300,
                          instance_id=1, blue_kills=3, red_kills=2, kill_target=10)),
        'score-local': (12, settlement_flow.encode_final_score(server_time_ms=7000, entity_rid=101,
                           kills=3, deaths=2, points=300, result=1, rank=1)),
        'score-bot': (12, settlement_flow.encode_final_score(server_time_ms=7000, entity_rid=202,
                         kills=2, deaths=3, points=200, result=2, rank=1)),
        'round-result': (10, contracts.roundResult(7000, 1)),
        'round-state-end': (10, contracts.roundState(7000, 5, 5000)),
        'settle': (52, settlement_flow.encode_settle_update(level=100, start_pattern=2, result=1)),
        'finish': (10, leave_flow.encode_si_finish_game(server_time_ms=12000, game_result=1,
                         local_user_id=10000)),
    }
    rows = {}
    for line in ROWS.read_text(encoding='utf-8').splitlines():
        if line.startswith('#') or not line:
            continue
        name, command, body = line.split()
        rows[name] = (int(command), bytes.fromhex(body))
    assert rows == expected
