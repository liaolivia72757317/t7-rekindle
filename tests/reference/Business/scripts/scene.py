"""Request-driven fixed instance assembly; native timers survive script reload."""
import struct

from . import actors, roster, contracts as wire, controls, battle, bot, combat, player_life
from . import battle_clock, settlement, loadouts
from .codec import battle_flow, kill_flow, leave_flow, login_flow, room_flow, vision_flow

END_HOLD_MS = 5000


def begin(flow):
    if not kill_flow.valid_kill_target(wire.KILL_TARGET):
        raise ValueError('kill target must fit a positive int32')
    battle_clock.initialize(flow)
    flow.session['killTarget'] = wire.KILL_TARGET
    flow.state.pop('settlement', None)
    bot.stopAll(flow, publish=False)
    flow.cancelTimers()
    for key in ('roundEnded', 'leaving', 'logoutData', 'battleEntered', 'heroChosen', 'loaded'):
        flow.session.pop(key, None)
    battle.resetAction(flow)
    player_life.reset(flow)
    flow.send(0x2C, flow.syncBody(), "instance-sync-login-rsp-after-auth")
    flow.send(0xA, wire.instanceInfo(flow.now), "instance-minimal-update-after-auth")
    flow.session["instanceStartedAt"] = flow.now
    flow.session["moveClock"] = controls.MOVE_CLOCK
    flow.session["movementMode"] = controls.movementMode(flow)
    flow.session["initialized"] = True
    flow.session["controlBaseline"] = wire.BASELINE_ID
    flow.session["groundEnabled"] = False
    if controls.runtimeMovement(flow):
        roster.assemble(flow)
    loadouts.begin(flow)
    flow.phase("instance-data-sent")
    flow.later("instance-game-info", 200)
    flow.later("time-sync", 2000)


def initializeBattleState(flow):
    startedAt = flow.session.get("instanceStartedAt")
    if startedAt is None:
        flow.log("INFO", "new instance required for natural initialization; unknown instance epoch")
        return
    body = battle_flow.encode_battle_state_sync_simple(
        instance_id=wire.ACTOR_ID, seq_no=1, state=battle_flow.ACT_STATE_IDLE,
        state_change_ms=flow.now - startedAt, state_time_ms=0)
    flow.send(battle_flow.BATTLE_COMMAND, body, "instance-battle-initial-idle")


def battleEntry(flow):
    if flow.session.get("battleEntered") or not flow.session.get("heroChosen"):
        return
    flow.session["battleEntered"] = True
    # IN_SCENE closes hero selection before returning to the preparation input lock.
    flow.send(0x36, wire.actorState(flow.now, 6), "actor-in-scene-after-battle-confirm")
    flow.send(0xE, vision_flow.encode_vision_del_event(), "actor-vision-del-after-battle-confirm")
    flow.send(0xE, roster.playerVision(flow),
              "actor-vision-add-after-battle-confirm")
    from . import shields
    shields.publishAll(flow)
    from . import guard_state
    guard_state.publish(flow, wire.ACTOR_ID)
    if not controls.groundEnabled(flow):
        flow.send(0x36, wire.actorState(flow.now, 8), "actor-ready-play-after-battle-confirm")
        if controls.runtimeMovement(flow):
            controls.activate(flow)
    if flow.session.get("controlBaseline") == wire.BASELINE_ID:
        if not controls.runtimeMovement(flow):
            ground = controls.groundState(flow)
            controls.broadcast(flow, wire.POSITION, ground["heading"], 1, 0, 0,
                               "instance-ground-initial-stop")
        initializeBattleState(flow)
    for key in actors.bots(flow.session):
        bot.enterScene(flow, int(key))
    flow.phase("battle-entry-sent")


def startRound(flow):
    pending = flow.session.get("pending", {})
    # Bots are preselected; only confirmed battle entry completes the human's selection.
    if (not flow.session.get("battleEntered") or flow.session.get("roundEnded")
            or controls.groundEnabled(flow) or "round-game" in pending or "round-end" in pending):
        return
    flow.cancel("actor-select")
    flow.cancel("round-start")
    flow.session.pop("prepareExpired", None)
    flow.send(0xA, wire.roundState(flow.now, 3, wire.START_MS), "instance-round-state-start")
    flow.later("round-game", wire.START_MS)
    flow.phase("start-sent")


def timer(flow, name):
    if name == "instance-init":
        begin(flow)
    elif name == "instance-game-info":
        flow.send(0xA, room_flow.encode_instance_update_game(
            server_time_ms=flow.now, instance_id=1), "instance-game-info")
        flow.later("instance-round-info", 200)
    elif name == "instance-round-info":
        flow.send(0xA, wire.roundInfo(flow.now, battle_clock.current(flow)['durationMs']), "instance-round-info")
        flow.later("instance-round-state", 200)
    elif name == "instance-round-state":
        flow.send(0xA, wire.roundState(flow.now, 2, wire.PREPARE_MS), "instance-round-state-after-auth")
        flow.later("instance-start-pattern", 200)
    elif name == "instance-start-pattern":
        flow.send(0xA, wire.startPattern(), "instance-start-pattern-data-ntf-after-auth")
        flow.later("instance-spawn-area", 200)
    elif name == "instance-spawn-area":
        flow.send(0xA, wire.spawnArea(flow.now), "instance-spawn-area-state-after-auth")
    elif name == "actor-select":
        flow.send(0x36, wire.actorState(flow.now, 5), "instance-actor-state-after-load-ok")
        flow.phase("hero-selection-sent")
    elif name == "round-start":
        if not flow.session.get("battleEntered"):
            flow.session["prepareExpired"] = True
            flow.log("INFO", "PREPARE elapsed without battle confirmation; waiting for PLAY_REQ")
            return True
        startRound(flow)
    elif name == "round-game":
        if not battle_clock.start(flow):
            return True
        clock = battle_clock.current(flow)
        flow.send(0xA, wire.roundState(flow.now, 4, clock['durationMs'], startedAt=clock['startedAt']),
                  "instance-round-state-game")
        flow.send(0x36, wire.actorState(flow.now, 6), "actor-in-scene-after-round-game")
        controls.enableGround(flow)
        flow.phase("game-sent")
        bot.start(flow)
        flow.later("round-end", battle_clock.deadline(flow.session) - flow.now)
    elif name == "round-end":
        finishRound(flow)
    elif name == "round-finish":
        publishFinish(flow)
    elif name == "time-sync":
        flow.send(8, struct.pack(">HQQB", 11, flow.now, 0, 1), "instance-periodic-time-sync")
        flow.later("time-sync", 2000)
    elif name == "logout":
        flow.send(0x2C, leave_flow.encode_sync_logout_response(
            server_time_ms=flow.now, back_data=flow.session.pop("logoutData")), "sync-logout-response")
        flow.phase("settlement" if flow.session['role'] == 'instance' else "lobby-data-sent")
    else:
        return False
    return True


def expire(flow):
    if (flow.session['role'] == 'instance' and not flow.session.get('roundEnded')
            and battle_clock.expired(flow)):
        finishRound(flow)
        return True
    return False


def checkKillTarget(flow, winnerCamp):
    target = flow.session.get('killTarget')
    # Old live rounds retain their announced rules until the next instance.
    if target is None or flow.session.get('roundEnded') or not battle.enabled(flow):
        return False
    if combat.campKills(flow)[winnerCamp] < target:
        return False
    finishRound(flow, reason='kill-target', winnerCamp=winnerCamp)
    return True


def finishRound(flow, *, reason=None, winnerCamp=None):
    if flow.session["role"] != "instance" or flow.session.get("roundEnded"):
        return
    snapshot = settlement.freeze(flow, reason=reason, winnerCamp=winnerCamp)
    flow.session["roundEnded"] = True
    bot.stopAll(flow)
    flow.cancelTimers()
    battle.resetAction(flow)
    flow.session["groundEnabled"] = False
    if flow.session.get("battleEntered"):
        controls.activate(flow)
    settlement.publishScores(flow, snapshot)
    holdMs = 0 if snapshot['reason'] == 'quit' else END_HOLD_MS
    if holdMs:
        # END's native victory banner reads the round result, not FINISH_GAME.
        flow.send(0xA, wire.roundResult(flow.now, snapshot['result']), "instance-round-result")
    flow.send(0xA, wire.roundState(flow.now, 5, holdMs), "instance-round-state-end")
    if holdMs:
        flow.later("round-finish", holdMs)
    else:
        publishFinish(flow)
    flow.phase("settlement")
    flow.log("INFO", f"settlement reason={snapshot['reason']} result={snapshot['result']} "
                               f"campKills={snapshot['campKills']} members={len(snapshot['members'])}")


def publishFinish(flow):
    snapshot = flow.state['settlement']
    settlement.publishResult(flow, snapshot)
    flow.send(0xA, leave_flow.encode_si_finish_game(
        server_time_ms=flow.now, game_result=snapshot['result'], local_user_id=wire.USER_ID), "instance-finish-game")


def leave(flow, body):
    backData = leave_flow.decode_sync_logout_request(body)
    if flow.session.get('leaving'):
        return
    finishRound(flow)
    if "round-finish" in flow.session.get("pending", {}):
        publishFinish(flow)
    flow.cancelTimers()
    if flow.session["role"] == "instance":
        flow.send(0xA, leave_flow.encode_instance_leave_instance(
            server_time_ms=flow.now), "instance-leave-instance")
    flow.session["logoutData"] = backData & 0xFF
    flow.session["leaving"] = True
    flow.phase("leaving")
    flow.later("logout", 250)


def message(flow, command, selector, body):
    if command == 0x2C and selector == 2:
        leave(flow, body)
        return True
    if command == 0x37 and selector == 1:
        guid, groupType = leave_flow.decode_user_group_quit_request(body)
        flow.send(0x37, leave_flow.encode_user_group_quit_response(
            user_group_guid=guid, user_group_type=groupType), "user-group-quit-response")
        if groupType == leave_flow.USER_GROUP_TYPE_END_SETTLE_ALL_ACTOR:
            flow.state.pop('settlement', None)
        flow.phase("lobby-data-sent")
        return True
    if flow.session["role"] != "instance" or flow.session.get("leaving"):
        return False
    if flow.session.get("roundEnded"):
        return True
    flow.session.setdefault("camp", 1)
    if command == 0xA and selector == 0x6A:
        flow.send(0xA, wire.instanceInfo(flow.now, flow.session.get("instanceStartedAt")),
                  "instance-update-request")
        clock = battle_clock.current(flow)
        if clock['startedAt'] is not None:
            flow.send(0xA, wire.roundState(flow.now, 4, clock['durationMs'], startedAt=clock['startedAt']),
                      'instance-round-state-refresh')
        combat.publishCampScore(flow)
    elif command == 0xA and selector == 0xB:
        placeholder = room_flow.decode_instance_choose_hero_request(body)
        if flow.session.get('battleEntered') and not player_life.canChoose(flow):
            return True
        flow.send(0xA, room_flow.encode_instance_choose_hero_message(
            placeholder=placeholder), "instance-enter-choose-hero")
    elif command == 0x23 and selector == 1:
        wire.exact(body, 6, "camp-choice")
        camp = struct.unpack_from(">i", body, 2)[0]
        if camp not in (1, 2):
            raise ValueError("camp-choice requires camp 1 or 2")
        if flow.session.get("battleEntered") and camp != flow.session.get("camp"):
            raise ValueError("camp-choice is closed after battle entry")
        flow.session["camp"] = camp
        if "bots" in flow.session and not flow.session.get("battleEntered"):
            roster.assemble(flow)
        elif "bot" in flow.session:
            flow.session["bot"]["camp"] = 3 - camp
        flow.send(0x23, struct.pack(">Hiiii", 2, 0, camp, 0, 0), "instance-camp-choose-result-zero")
    elif command == 0x23 and selector == 3:
        wire.exact(body, 3, "camp-choice-ok")
        camp = flow.session["camp"]
        flow.send(0x23, struct.pack(">Hi", 4, 0), "instance-camp-choose-ok-result-zero")
        flow.send(0xE, roster.playerVision(flow), "instance-fixed-local-actor-vision-add-before-basic-info")
        flow.send(0xA, wire.actorInfo(flow.now, camp, flow.playerName, flow.session.get('heroId', wire.HERO_ID)),
                  "instance-fixed-local-actor-after-camp-choice")
        flow.send(0x23, wire.campExchange(flow.now, camp), "instance-camp-exchange-notify")
        combat.publishScore(flow, wire.ACTOR_ID)
        for key in actors.bots(flow.session):
            bot.importMember(flow, int(key))
        combat.publishCampScore(flow)
        flow.session["actorImported"] = True
        flow.phase("actor-data-sent")
    elif command == 0xE and selector == 5:
        sequence = vision_flow.decode_vision_list_request(body)
        flow.send(0xE, vision_flow.encode_vision_list_response(
            sequence=sequence, object_mids=actors.ids(flow.session) if actors.bots(flow.session) else (1, 2)), "instance-fixed-local-actor-vision-list")
    elif command == 0xE and selector == 7:
        request = vision_flow.decode_vision_get_objects_request(body)
        if any(mid not in (actors.ids(flow.session) if actors.bots(flow.session) else (1, 2)) for mid in request.object_mids):
            raise ValueError("VISION requested an object outside the fixed scene")
        for mid in request.object_mids:
            # Keep the native corpse/camera timeline intact during VISION refresh.
            if mid == wire.ACTOR_ID and player_life.dead(flow):
                continue
            vision = (roster.playerVision(flow) if mid == wire.ACTOR_ID
                      else bot.vision(actors.data(flow, mid), mid) if actors.bots(flow.session) else wire.enemyVision())
            flow.send(0xE, vision, "instance-fixed-local-actor-vision-add-event" if mid == wire.ACTOR_ID
                      else "instance-fixed-enemy-actor-vision-add-event")
            if mid != wire.ACTOR_ID and actors.bots(flow.session):
                bot.refresh(flow, mid)
        if request.object_mids:
            controls.activate(flow)
    elif command == 0x29 and selector == 0x65:
        container, sequence = login_flow.decode_item_container_request(b"\0\1" + body[2:])
        if container == 2 and loadouts.syncInstance(flow, sequence):
            return True
        response = (wire.battleHeroes(sequence, controls.runtimeMovement(flow))
                    if container == 2 else b"\0\x66" + login_flow.encode_empty_item_container_response(
                        container, sequence)[2:])
        flow.send(0x29, response, "instance-sync-item-container")
    elif command == 0x36 and selector == 0x14:
        wire.exact(body, 3, "actor-load-ok")
        if body[2] != 0:
            raise ValueError("actor-load-ok requires result 0")
        if not flow.session.get("actorImported"):
            raise ValueError("actor-load-ok precedes fixed actor import")
        if not flow.session.get("loaded"):
            flow.session["loaded"] = True
            flow.send(0xA, wire.roundState(flow.now, 2, wire.PREPARE_MS), "instance-round-state-prepare")
            flow.later("actor-select", 1000)
            flow.later("round-start", wire.PREPARE_MS)
            flow.phase("prepare-sent")
    elif command == 0x36 and selector == 0x32:
        position = room_flow.decode_actor_choose_hero_request(body)
        heroes = flow.session.get('loadoutProfile', {}).get('formation', wire.heroIds(controls.runtimeMovement(flow)))
        if not 1 <= position <= len(heroes):
            raise ValueError("fixed scene exposes the available hero slots")
        if not flow.session.get("loaded"):
            raise ValueError("choose-hero precedes actor-load-ok")
        if flow.session.get('battleEntered') and not player_life.canChoose(flow):
            flow.send(0x36, room_flow.encode_actor_choose_hero_response(result=1), 'player-choose-not-ready')
            return True
        flow.session["heroChosen"] = True
        if not loadouts.select(flow, position):
            flow.session['heroId'] = heroes[position - 1]
        flow.send(0x36, room_flow.encode_actor_choose_hero_response(), "actor-choose-hero-result-zero")
        flow.send(0x36, room_flow.encode_actor_choose_hero_message(
            server_time_ms=flow.now, actor_mid=1, hero_resource_id=flow.session["heroId"],
            hero_badge=0), "actor-choose-hero-message")
    elif command == 0x36 and selector == 0x64:
        room_flow.decode_actor_play_request(body)
        if 'life' in flow.session:
            player_life.requestSpawn(flow)
            return True
        if not flow.session.get("heroChosen"):
            raise ValueError("actor-play precedes hero selection")
        flow.send(0x36, room_flow.encode_actor_play_response(), "actor-play-result-zero")
        battleEntry(flow)
        startRound(flow)
    else:
        return False
    return True
