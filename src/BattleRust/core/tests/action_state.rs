use t7r_battle_core::action::*;

fn profile(id: u32, hand: u8, shield: bool, dash: bool) -> WeaponProfile {
    WeaponProfile {
        weapon_id: id,
        hand,
        shield,
        change_ms: if shield { 0 } else { 100 },
        attacks: [AttackProfile {
            enabled: !shield,
            prepare_ms: 100,
            strike_ms: 200,
            recovery_ms: 150,
            parried_ms: 80,
            rebound_ms: 90,
            contact_start_ms: 0,
            contact_end_ms: 200,
        }; 4],
        guard_prepare_ms: if shield { 0 } else { 50 },
        guard_success_ms: if shield { 0 } else { 100 },
        dash: dash.then_some(DashProfile {
            duration_ms: 250,
            move_ms: 180,
            contact_start_ms: 0,
            contact_end_ms: 220,
            rebound_ms: 100,
            speed_milli: 2000,
        }),
    }
}
fn loadout() -> Loadout {
    Loadout::new(
        vec![
            profile(100000, 3, false, false),
            profile(701, 2, false, true),
            profile(703, 1, true, false),
        ],
        vec![
            EquipmentItem {
                slot: 1,
                weapon_id: 701,
            },
            EquipmentItem {
                slot: 2,
                weapon_id: 100000,
            },
            EquipmentItem {
                slot: 4,
                weapon_id: 703,
            },
        ],
        100000,
    )
    .unwrap()
}
fn context(now: u64) -> Context {
    Context {
        now_ms: now,
        enabled: true,
        active: true,
        instance_matches: true,
        pose_ready: true,
        contact_at_ms: None,
    }
}

#[test]
fn unsupported_attack_neither_starts_nor_buffers_or_interrupts_guard() {
    let mut spear = profile(701, 3, false, false);
    for (index, attack) in spear.attacks.iter_mut().enumerate() {
        attack.enabled = index == Direction::Down as usize;
    }
    let loadout = Loadout::new(
        vec![profile(100000, 2, false, false), spear],
        vec![EquipmentItem {
            slot: 1,
            weapon_id: 701,
        }],
        100000,
    )
    .unwrap();
    let initial = ActionState::new(2, &loadout, 701, 1).unwrap();
    for intent in [
        None,
        Some(Intent::Guard(Direction::Up)),
        Some(Intent::Attack(Direction::Down)),
    ] {
        let state = if let Some(intent) = intent {
            initial
                .apply(
                    Command::Start {
                        intent,
                        released: false,
                    },
                    context(10),
                    &loadout,
                )
                .unwrap()
                .state
        } else {
            initial.clone()
        };
        for direction in [Direction::Up, Direction::Right, Direction::Left] {
            for command in [
                Command::Start {
                    intent: Intent::Attack(direction),
                    released: true,
                },
                Command::Press {
                    intent: Intent::Attack(direction),
                    defence_down: false,
                    dash_run: false,
                    dash_forward: false,
                },
            ] {
                let transition = state.apply(command, context(20), &loadout).unwrap();
                assert_eq!(transition.state, state);
                assert!(transition.events.is_empty());
            }
        }
    }
    let down = initial
        .apply(
            Command::Start {
                intent: Intent::Attack(Direction::Down),
                released: true,
            },
            context(20),
            &loadout,
        )
        .unwrap();
    assert_eq!(down.state.snapshot().phase, Phase::Prepare);
    assert_eq!(
        down.state.snapshot().intent,
        Some(Intent::Attack(Direction::Down))
    );
}

#[test]
fn attack_release_strike_and_parried_reaction_have_causal_deadlines() {
    let loadout = loadout();
    let initial = ActionState::new(2, &loadout, 701, 1).unwrap();
    let started = initial
        .apply(
            Command::Start {
                intent: Intent::Attack(Direction::Up),
                released: true,
            },
            context(10),
            &loadout,
        )
        .unwrap();
    assert_eq!(started.state.snapshot().phase, Phase::Prepare);
    assert_eq!(started.state.snapshot().phase_due_ms, Some(110));
    let busy = started
        .state
        .apply(
            Command::Start {
                intent: Intent::Attack(Direction::Left),
                released: true,
            },
            context(20),
            &loadout,
        )
        .unwrap();
    assert_eq!(busy.state, started.state);
    assert!(busy.events.is_empty());
    let strike = started
        .state
        .apply(
            Command::Timer {
                kind: TimerKind::Phase,
                deadline_ms: 110,
            },
            context(110),
            &loadout,
        )
        .unwrap();
    assert_eq!(strike.state.snapshot().phase, Phase::Strike);
    assert_eq!(strike.state.snapshot().phase_due_ms, Some(310));
    assert!(strike.events.iter().any(|event| matches!(
        event,
        ActionEvent::SwingStarted {
            sequence: 3,
            began_at_ms: 110,
            contact_end_ms: 200,
            ..
        }
    )));
    let parried = strike
        .state
        .apply(
            Command::Parried {
                target_id: 1,
                intent: Intent::Attack(Direction::Up),
                weapon_id: 701,
            },
            Context {
                contact_at_ms: Some(141.5),
                ..context(160)
            },
            &loadout,
        )
        .unwrap();
    assert_eq!(parried.state.snapshot().phase, Phase::Parried);
    assert!(parried.events.iter().any(
        |event| matches!(event,ActionEvent::CancelSwing{at_ms,interrupted:true,..} if *at_ms==141.5)
    ));
    assert_eq!(parried.state.snapshot().phase_due_ms, Some(240));
    let stale = parried
        .state
        .apply(
            Command::Timer {
                kind: TimerKind::Phase,
                deadline_ms: 310,
            },
            context(310),
            &loadout,
        )
        .unwrap();
    assert_eq!(stale.state, parried.state);
    assert!(stale.events.is_empty());
}

#[test]
fn shield_guard_weapon_toggle_and_overflow_are_explicit() {
    let loadout = loadout();
    let initial = ActionState::new(1, &loadout, 701, 1).unwrap();
    assert_eq!(initial.snapshot().offhand_id, 703);
    let shield = initial
        .apply(
            Command::Start {
                intent: Intent::Shield,
                released: false,
            },
            context(0),
            &loadout,
        )
        .unwrap();
    assert_eq!(shield.state.snapshot().phase, Phase::Prepare);
    let broken = initial.break_shield(703, &loadout).unwrap();
    assert_eq!(broken.snapshot().offhand_id, 0);
    assert_eq!(
        broken
            .apply(
                Command::Start {
                    intent: Intent::Shield,
                    released: false
                },
                context(0),
                &loadout
            )
            .unwrap()
            .state,
        broken
    );
    let fist = initial
        .apply(Command::ChangeSlot(13), context(5), &loadout)
        .unwrap();
    assert_eq!(fist.state.snapshot().weapon_id, 100000);
    assert_eq!(fist.state.snapshot().phase, Phase::WeaponChange);
    let full = initial.apply(
        Command::Start {
            intent: Intent::Attack(Direction::Left),
            released: false,
        },
        context(u64::MAX),
        &loadout,
    );
    assert_eq!(full.unwrap_err(), ActionError::TimeOverflow);
    assert_eq!(initial.snapshot().phase, Phase::Idle);
}
