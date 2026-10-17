"""Frozen final roster, retained until the native settlement group returns to lobby."""
from . import actors, battle_clock, bot, combat, contracts as wire
from .codec import kill_flow, settlement_flow


def freeze(flow, *, reason=None, winnerCamp=None):
    if reason is None:
        reason = 'timeout' if battle_clock.expired(flow) else 'quit'
    if reason not in ('quit', 'timeout', 'kill-target'):
        raise ValueError('unknown settlement reason')
    members = []
    for rid in actors.ids(flow.session):
        data = actors.data(flow, rid)
        kills, deaths, points = combat.scoreTotals(data)
        members.append({'rid': rid, 'userId': wire.USER_ID + rid - 1,
                        'name': flow.playerName.decode('gbk') if rid == wire.ACTOR_ID else data.get('name', bot.NAME.decode('gbk')),
                        'camp': data.get('camp', 1), 'heroId': data.get('heroId', wire.HERO_ID),
                        'kills': kills, 'deaths': deaths, 'points': points})
    campKills = {str(camp): kills for camp, kills in combat.campKills(flow).items()}
    if reason == 'kill-target':
        if (type(winnerCamp) is not int or winnerCamp not in (1, 2)
                or not kill_flow.valid_kill_target(flow.session.get('killTarget'))
                or campKills[str(winnerCamp)] < flow.session['killTarget']):
            raise ValueError('kill target settlement requires a camp that reached its target')
        result = winnerCamp
    else:
        result = (0 if reason == 'quit' else 3 if campKills['1'] == campKills['2']
                  else 1 if campKills['1'] > campKills['2'] else 2)
    ranked = []
    for camp in (1, 2):
        team = sorted((m for m in members if m['camp'] == camp),
                      key=lambda m: (-m['points'], -m['kills'], m['deaths'], m['rid']))
        personalResult = result if result in (0, 3) else 1 if result == camp else 2
        ranked.extend(dict(member, rank=rank, result=personalResult) for rank, member in enumerate(team, 1))
    clock = battle_clock.current(flow)
    snapshot = {'instanceStartedAt': flow.session.get('instanceStartedAt', flow.now),
                'startedAt': clock['startedAt'], 'durationMs': clock['durationMs'], 'endedAt': flow.now,
                'reason': reason, 'result': result, 'campKills': campKills, 'members': ranked}
    if 'killTarget' in flow.session:
        snapshot['killTarget'] = flow.session['killTarget']
    flow.state['settlement'] = snapshot
    return snapshot


def publishScores(flow, snapshot):
    for member in snapshot['members']:
        flow.send(kill_flow.SCORE_COMMAND, settlement_flow.encode_final_score(
            server_time_ms=flow.now, entity_rid=member['rid'], kills=member['kills'], deaths=member['deaths'],
            points=member['points'], result=member['result'], rank=member['rank']), 'settlement-score')


def publishResult(flow, snapshot):
    member = next(m for m in snapshot['members'] if m['rid'] == wire.ACTOR_ID)
    flow.send(settlement_flow.SETTLE_COMMAND, settlement_flow.encode_settle_update(
        level=wire.USER_LEVEL, start_pattern=wire.START_PATTERN, result=member['result']), 'instance-settlement')


def validState(state):
    if 'settlement' not in state:
        return True
    value = state['settlement']
    if (type(value) is not dict or not battle_clock.validState({'battleClock': value})
            or value.get('reason') not in ('quit', 'timeout', 'kill-target')
            or ('killTarget' in value and not kill_flow.valid_kill_target(value['killTarget']))
            or type(value.get('result')) is not int or value['result'] not in (0, 1, 2, 3)
            or (value['reason'] == 'quit') != (value['result'] == 0)
            or any(type(value.get(key)) is not int or value[key] < 0 for key in ('instanceStartedAt', 'endedAt'))
            or type(value.get('members')) is not list or not 1 <= len(value['members']) <= actors.MAX_ACTORS
            or type(value.get('campKills')) is not dict or set(value['campKills']) != {'1', '2'}):
        return False
    members = value['members']
    for member in members:
        if (type(member) is not dict or type(member.get('name')) is not str
                or any(type(member.get(key)) is not int or member[key] < 1 for key in ('rid', 'userId', 'heroId', 'rank'))
                or type(member.get('camp')) is not int or member['camp'] not in (1, 2)
                or any(type(member.get(key)) is not int or not 0 <= member[key] <= kill_flow.MAX_SCORE
                       for key in ('kills', 'deaths', 'points'))
                or type(member.get('result')) is not int
                or member['result'] != (value['result'] if value['result'] in (0, 3)
                                        else 1 if value['result'] == member['camp'] else 2)):
            return False
    if len({m['rid'] for m in members}) != len(members) or not any(m['rid'] == wire.ACTOR_ID for m in members):
        return False
    for camp in (1, 2):
        team = [m for m in members if m['camp'] == camp]
        if (type(value['campKills'][str(camp)]) is not int
                or value['campKills'][str(camp)] != sum(m['kills'] for m in team)
                or [m['rank'] for m in team] != list(range(1, len(team) + 1))):
            return False
    return (value['reason'] != 'kill-target' or value['result'] in (1, 2)
            and kill_flow.valid_kill_target(value.get('killTarget'))
            and value.get('startedAt') is not None and value['campKills'][str(value['result'])] >= value['killTarget'])
