"""Request-driven fixed instance assembly; native timers survive script reload."""
import struct

from . import contracts as wire, controls
from .codec import battle_flow, leave_flow, login_flow, room_flow, vision_flow


def begin(flow):
    flow.send(0x2C, flow.syncBody(), "instance-sync-login-rsp-after-auth")
    flow.send(0xA, wire.instanceInfo(flow.now), "instance-minimal-update-after-auth")
    flow.session["instanceStartedAt"] = flow.now
    flow.session["moveClock"] = controls.MOVE_CLOCK
    flow.session["movementMode"] = controls.movementMode(flow)
    flow.session["initialized"] = True
    flow.session["controlBaseline"] = wire.BASELINE_ID
    flow.session["groundEnabled"] = False
    flow.phase("instance-data-sent")
    flow.later("instance-game-info", 200)
    flow.later("time-sync", 2000)


def initializeBattleState(flow):
    startedAt = flow.session.get("instanceStartedAt")
    if startedAt is None:
        flow.result["logs"].append("new instance required for natural initialization; unknown instance epoch")
        return
    body = battle_flow.encode_battle_state_sync_simple(
        instance_id=wire.ACTOR_ID, seq_no=1, state=battle_flow.ACT_STATE_IDLE,
        state_change_ms=flow.now - startedAt, state_time_ms=0)
    flow.send(battle_flow.BATTLE_COMMAND, body, "instance-battle-initial-idle")


def battleEntry(flow):
    if flow.session.get("battleEntered") or not flow.session.get("heroChosen"):
        return
    flow.session["battleEntered"] = True
    actorState = 6 if controls.groundEnabled(flow) else 8
    flow.send(0x36, wire.actorState(flow.now, actorState), "actor-state-after-battle-confirm")
    flow.send(0xE, vision_flow.encode_vision_del_event(), "actor-vision-del-after-battle-confirm")
    flow.send(0xE, wire.actorVision(flow.session["camp"], flow.session["heroId"], flow.playerName,
                                    runtimeMovement=controls.runtimeMovement(flow)),
              "actor-vision-add-after-battle-confirm")
    if flow.session.get("controlBaseline") == wire.BASELINE_ID:
        if not controls.runtimeMovement(flow):
            ground = controls.groundState(flow)
            controls.broadcast(flow, wire.POSITION, ground["heading"], 1, 0, 0,
                               "instance-ground-initial-stop")
        initializeBattleState(flow)
    flow.phase("battle-entry-sent")


def timer(flow, name):
    if name == "instance-init":
        begin(flow)
    elif name == "instance-game-info":
        flow.send(0xA, room_flow.encode_instance_update_game(
            server_time_ms=flow.now, instance_id=1), "instance-game-info")
        flow.later("instance-round-info", 200)
    elif name == "instance-round-info":
        flow.send(0xA, wire.roundInfo(flow.now), "instance-round-info")
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
        if not flow.session.get("heroChosen"):
            flow.session["prepareExpired"] = True
            flow.result["logs"].append("PREPARE elapsed without hero selection; no fabricated GAME")
            return True
        battleEntry(flow)
        flow.send(0xA, wire.roundState(flow.now, 3, wire.START_MS), "instance-round-state-start")
        flow.later("round-game", wire.START_MS)
    elif name == "round-game":
        flow.send(0xA, wire.roundState(flow.now, 4, wire.GAME_MS), "instance-round-state-game")
        flow.send(0x36, wire.actorState(flow.now, 6), "actor-in-scene-after-round-game")
        controls.enableGround(flow)
        flow.phase("game-sent")
    elif name == "time-sync":
        flow.send(8, struct.pack(">HQQB", 11, flow.now, 0, 1), "instance-periodic-time-sync")
        flow.later("time-sync", 2000)
    elif name == "logout":
        flow.send(0x2C, leave_flow.encode_sync_logout_response(
            server_time_ms=flow.now, back_data=flow.session.pop("logoutData")), "sync-logout-response")
        flow.phase("lobby-data-sent")
    else:
        return False
    return True


def leave(flow, body):
    backData = leave_flow.decode_sync_logout_request(body)
    flow.cancelTimers()
    if flow.session["role"] == "instance":
        flow.send(0xA, wire.roundState(flow.now, 5, 0), "instance-round-state-end")
        flow.send(0xA, leave_flow.encode_si_finish_game(
            server_time_ms=flow.now, local_user_id=wire.USER_ID), "instance-finish-game")
        flow.send(0xA, leave_flow.encode_instance_leave_instance(
            server_time_ms=flow.now), "instance-leave-instance")
    flow.session["logoutData"] = backData & 0xFF
    flow.session["leaving"] = True
    flow.session["groundEnabled"] = False
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
        flow.phase("lobby-data-sent")
        return True
    if flow.session["role"] != "instance" or flow.session.get("leaving"):
        return False
    flow.session.setdefault("camp", 1)
    if command == 0xA and selector == 0x6A:
        flow.send(0xA, wire.instanceInfo(flow.now, flow.session.get("instanceStartedAt")),
                  "instance-update-request")
    elif command == 0xA and selector == 0xB:
        placeholder = room_flow.decode_instance_choose_hero_request(body)
        flow.send(0xA, room_flow.encode_instance_choose_hero_message(
            placeholder=placeholder), "instance-enter-choose-hero")
    elif command == 0x23 and selector == 1:
        wire.exact(body, 6, "camp-choice")
        camp = struct.unpack_from(">i", body, 2)[0]
        if camp not in (1, 2):
            raise ValueError("camp-choice requires camp 1 or 2")
        flow.session["camp"] = camp
        flow.send(0x23, struct.pack(">Hiiii", 2, 0, camp, 0, 0), "instance-camp-choose-result-zero")
    elif command == 0x23 and selector == 3:
        wire.exact(body, 3, "camp-choice-ok")
        camp = flow.session["camp"]
        flow.send(0x23, struct.pack(">Hi", 4, 0), "instance-camp-choose-ok-result-zero")
        flow.send(0xE, wire.actorVision(camp, actorName=flow.playerName,
                                        runtimeMovement=controls.runtimeMovement(flow)), "instance-fixed-local-actor-vision-add-before-basic-info")
        flow.send(0xA, wire.actorInfo(flow.now, camp, flow.playerName), "instance-fixed-local-actor-after-camp-choice")
        flow.send(0x23, wire.campExchange(flow.now, camp), "instance-camp-exchange-notify")
        flow.session["actorImported"] = True
        flow.phase("actor-data-sent")
    elif command == 0xE and selector == 5:
        sequence = vision_flow.decode_vision_list_request(body)
        flow.send(0xE, vision_flow.encode_vision_list_response(
            sequence=sequence, object_mids=(1, 2)), "instance-fixed-local-actor-vision-list")
    elif command == 0xE and selector == 7:
        request = vision_flow.decode_vision_get_objects_request(body)
        if any(mid not in (wire.ACTOR_ID, 2) for mid in request.object_mids):
            raise ValueError("VISION requested an object outside the fixed scene")
        for mid in request.object_mids:
            position = (flow.session.get("ground", {}).get("position")
                        if controls.runtimeMovement(flow) else None)
            vision = (wire.actorVision(flow.session["camp"], flow.session.get("heroId", wire.HERO_ID), flow.playerName,
                                       runtimeMovement=controls.runtimeMovement(flow),
                                       position=tuple(position) if position is not None else wire.POSITION)
                      if mid == wire.ACTOR_ID else wire.enemyVision())
            flow.send(0xE, vision, "instance-fixed-local-actor-vision-add-event" if mid == wire.ACTOR_ID
                      else "instance-fixed-enemy-actor-vision-add-event")
        if request.object_mids:
            controls.activate(flow)
    elif command == 0x29 and selector == 0x65:
        container, sequence = login_flow.decode_item_container_request(b"\0\1" + body[2:])
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
            flow.later("round-start", wire.PREPARE_MS - wire.START_MS)
            flow.phase("prepare-sent")
    elif command == 0x36 and selector == 0x32:
        position = room_flow.decode_actor_choose_hero_request(body)
        if not 1 <= position <= len(wire.heroIds(controls.runtimeMovement(flow))):
            raise ValueError("fixed scene exposes the available hero slots")
        if not flow.session.get("loaded"):
            raise ValueError("choose-hero precedes actor-load-ok")
        flow.session["heroChosen"] = True
        flow.session["heroId"] = wire.HERO_IDS[position - 1]
        flow.send(0x36, room_flow.encode_actor_choose_hero_response(), "actor-choose-hero-result-zero")
        flow.send(0x36, room_flow.encode_actor_choose_hero_message(
            server_time_ms=flow.now, actor_mid=1, hero_resource_id=flow.session["heroId"],
            hero_badge=0), "actor-choose-hero-message")
        if flow.session.pop("prepareExpired", False):
            flow.later("round-start", 0)
    elif command == 0x36 and selector == 0x64:
        room_flow.decode_actor_play_request(body)
        if not flow.session.get("heroChosen"):
            raise ValueError("actor-play precedes hero selection")
        flow.send(0x36, room_flow.encode_actor_play_response(), "actor-play-result-zero")
        battleEntry(flow)
    else:
        return False
    return True
