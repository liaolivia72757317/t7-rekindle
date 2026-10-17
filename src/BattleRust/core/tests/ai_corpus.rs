use serde_json::Value;
use t7r_battle_core::{
    action::{Direction, Intent, Phase},
    ai::{
        self, ActionObservation, ActorSnapshot, AttackTimings, BotConfig, ContactObservation,
        ContactState, DecisionContext, DecisionKind, Profile,
    },
};

fn number(v: &Value) -> u64 {
    v.as_u64().unwrap()
}
fn position(v: &Value) -> [f64; 3] {
    std::array::from_fn(|i| v[i].as_f64().unwrap())
}
fn direction(id: u64) -> Direction {
    match id {
        300560 | 300580 => Direction::Up,
        300550 | 300590 => Direction::Right,
        300570 | 300600 => Direction::Down,
        300540 | 300610 => Direction::Left,
        _ => panic!("unknown direction {id}"),
    }
}
fn intent(id: u64) -> Option<Intent> {
    match id {
        0 => None,
        700250 => Some(Intent::Dash),
        300130 => Some(Intent::Shield),
        300540 | 300550 | 300560 | 300570 => Some(Intent::Attack(direction(id))),
        300580 | 300590 | 300600 | 300610 => Some(Intent::Guard(direction(id))),
        _ => panic!("unknown intent {id}"),
    }
}
fn intent_id(intent: Option<Intent>) -> u64 {
    match intent {
        None => 0,
        Some(Intent::Dash) => 700250,
        Some(Intent::Shield) => 300130,
        Some(Intent::Attack(Direction::Up)) => 300560,
        Some(Intent::Attack(Direction::Right)) => 300550,
        Some(Intent::Attack(Direction::Down)) => 300570,
        Some(Intent::Attack(Direction::Left)) => 300540,
        Some(Intent::Guard(Direction::Up)) => 300580,
        Some(Intent::Guard(Direction::Right)) => 300590,
        Some(Intent::Guard(Direction::Down)) => 300600,
        Some(Intent::Guard(Direction::Left)) => 300610,
    }
}
fn profile(name: &str) -> Profile {
    match name {
        "aggressive" => Profile::Aggressive,
        "balanced" => Profile::Balanced,
        "defensive" => Profile::Defensive,
        "berserker" => Profile::Berserker,
        _ => panic!("unknown profile"),
    }
}
fn actor(row: &Value, id: u32, camp: u8, target: u32) -> ActorSnapshot {
    let action = &row["action"];
    let intent = intent(number(&action["intent"]));
    let phase = match action["reaction"].as_str().unwrap() {
        "parried" => Phase::Parried,
        "parry-success" => Phase::ParrySuccess,
        "terrain-rebound" => Phase::Terrain,
        "" => match action["index"].as_i64().unwrap() {
            -1 => Phase::Idle,
            0 => Phase::Prepare,
            1 => {
                if intent == Some(Intent::Dash) {
                    Phase::Strike
                } else {
                    Phase::Hold
                }
            }
            2 => Phase::Strike,
            3 => Phase::Recovery,
            _ => panic!("bad index"),
        },
        _ => panic!("bad reaction"),
    };
    let contact = match row["contact"].as_str().unwrap() {
        "clear" => ContactObservation {
            target_id: Some(target),
            state: ContactState::Clear,
        },
        "blocked" => ContactObservation {
            target_id: Some(target),
            state: ContactState::Blocked,
        },
        "unavailable" => ContactObservation::unavailable(),
        _ => panic!("bad contact"),
    };
    ActorSnapshot {
        id,
        camp,
        alive: true,
        blocks_movement: true,
        position: position(&row["position"]),
        heading: row["heading"].as_f64().unwrap(),
        health: 100,
        preferred_distance: 1.2,
        shield_equipped: false,
        hurt: row["hurt"].as_bool().unwrap(),
        contact,
        action: ActionObservation {
            intent,
            phase,
            sequence: number(&action["seq"]) as u16,
            released: action["released"].as_bool().unwrap(),
            phase_due_ms: row["due"].as_u64(),
        },
    }
}

#[test]
fn sixty_three_decisions_match_original_python_golden() {
    let corpus: Value = serde_json::from_slice(include_bytes!(
        "../../../../tests/fixtures/ai/decisions-v1.json"
    ))
    .unwrap();
    assert_eq!(corpus["cases"].as_array().unwrap().len(), 63);
    for case in corpus["cases"].as_array().unwrap() {
        let context = &case["context"];
        let source = actor(&case["source"], 2, 2, 1);
        let target = actor(&case["target"], 1, 1, 2);
        let timings = AttackTimings {
            allowed: [true; 4],
            windup_ms: std::array::from_fn(|i| number(&case["windups"][i])),
        };
        let input = DecisionContext {
            now_ms: number(&context["now"]),
            profile: profile(context["profile"].as_str().unwrap()),
            tempo: number(&context["tempo"]) as u16,
            seed: number(&context["seed"]) as u32,
            last_attack: match number(&context["lastAttack"]) {
                0 => None,
                id => Some(direction(id)),
            },
            react_at_ms: context["reactAt"].as_u64(),
            observed_since_ms: number(&context["observedSince"]),
            phase_observed_since_ms: number(&context["phaseObservedSince"]),
        };
        let result = ai::choose_decision(&input, &source, &target, &timings).unwrap();
        let expected = &case["expected"];
        let kind = match result.kind {
            DecisionKind::Wait => "wait",
            DecisionKind::Attack => "attack",
            DecisionKind::Parry => "parry",
        };
        assert_eq!(kind, expected["kind"].as_str().unwrap(), "{}", case["name"]);
        assert_eq!(
            result.reason,
            expected["reason"].as_str().unwrap(),
            "{}",
            case["name"]
        );
        assert_eq!(
            intent_id(result.intent),
            number(&expected["intent"]),
            "{}",
            case["name"]
        );
        assert_eq!(
            u64::from(result.seed),
            number(&expected["seed"]),
            "{}",
            case["name"]
        );
    }
}

#[test]
fn nineteen_profiles_match_original_python_golden() {
    let corpus: Value = serde_json::from_slice(include_bytes!(
        "../../../../tests/fixtures/ai/decisions-v1.json"
    ))
    .unwrap();
    let points = |camp: &str| {
        corpus["spawns"][camp]
            .as_array()
            .unwrap()
            .iter()
            .map(position)
            .collect::<Vec<_>>()
    };
    let (blue, red) = (points("1"), points("2"));
    assert_eq!(corpus["profiles"].as_array().unwrap().len(), 19);
    for case in corpus["profiles"].as_array().unwrap() {
        let camp = number(&case["camp"]) as u8;
        let (own, other) = if camp == 1 {
            (&blue, &red)
        } else {
            (&red, &blue)
        };
        let bot = BotConfig::from_spawns(
            number(&case["actorId"]) as u32,
            number(&case["ordinal"]) as usize,
            camp,
            number(&case["seed"]) as u32,
            own,
            other,
        )
        .unwrap();
        let expected = &case["expected"];
        assert_eq!(bot.profile.name(), expected["profile"].as_str().unwrap());
        assert_eq!(bot.tempo as u64, number(&expected["tempo"]));
        assert_eq!(i64::from(bot.lane), expected["lane"].as_i64().unwrap());
        assert_eq!(u64::from(bot.route_seed), number(&expected["routeSeed"]));
        assert_eq!(u64::from(bot.rhythm_seed), number(&expected["rhythmSeed"]));
        let pos = position(&expected["patrolAnchor"]);
        assert!(
            bot.patrol_anchor
                .into_iter()
                .zip(pos)
                .all(|(a, b)| (a - b).abs() < 1e-10)
        );
    }
}
