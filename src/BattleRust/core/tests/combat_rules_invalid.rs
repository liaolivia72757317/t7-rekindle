use t7r_battle_core::{damage, guard};

#[test]
fn damage_rejects_invalid_evidence_before_short_circuiting() {
    let rules = damage::Rules::default();
    for invalid in [f64::NAN, f64::INFINITY, f64::NEG_INFINITY, -1.0] {
        for field in 0..8 {
            let mut hit = damage::Hit {
                friendly: true,
                ..Default::default()
            };
            *match field {
                0 => &mut hit.base,
                1 => &mut hit.armor,
                2 => &mut hit.held_ms,
                3 => &mut hit.peak_ms,
                4 => &mut hit.phase,
                5 => &mut hit.buff_multiplier,
                6 => &mut hit.health_ratio,
                _ => &mut hit.damage_scale,
            } = invalid;
            assert_eq!(
                damage::calculate(&hit, &rules),
                Err(damage::RuleError::InvalidEvidence)
            );
        }
    }
    for invalid in [f64::NAN, f64::INFINITY, f64::NEG_INFINITY] {
        for field in 0..3 {
            let mut hit = damage::Hit::default();
            (match field {
                0 => &mut hit.source_velocity,
                1 => &mut hit.target_velocity,
                _ => &mut hit.weapon_velocity,
            })[1] = invalid;
            assert_eq!(
                damage::calculate(&hit, &rules),
                Err(damage::RuleError::InvalidEvidence)
            );
        }
    }
    for hit in [
        damage::Hit {
            phase: 1.01,
            ..Default::default()
        },
        damage::Hit {
            health_ratio: 1.01,
            ..Default::default()
        },
        damage::Hit {
            peak_ms: 0.0,
            ..Default::default()
        },
    ] {
        assert_eq!(
            damage::calculate(&hit, &rules),
            Err(damage::RuleError::InvalidEvidence)
        );
    }
}

#[test]
fn damage_rejects_invalid_rules_and_arithmetic_overflow() {
    let default = damage::Rules::default();
    for rules in [
        damage::Rules {
            phase_points: vec![],
            ..default.clone()
        },
        damage::Rules {
            phase_points: vec![[0.0, 1.0], [0.0, 2.0]],
            ..default.clone()
        },
        damage::Rules {
            charge_decay_ms: 0,
            ..default.clone()
        },
        damage::Rules {
            speed_min: 2.0,
            ..default.clone()
        },
        damage::Rules {
            desperate_health: 1.01,
            ..default.clone()
        },
        damage::Rules {
            cleave_scale: std::array::from_fn(|_| vec![]),
            ..default.clone()
        },
        damage::Rules {
            armor_scale: [f64::NAN, 1.0, 1.0],
            ..default.clone()
        },
        damage::Rules {
            assassin: [1.0, f64::INFINITY, 1.0],
            ..default.clone()
        },
        damage::Rules {
            charge_mastery: -1.0,
            ..default.clone()
        },
    ] {
        assert_eq!(
            damage::calculate(&damage::Hit::default(), &rules),
            Err(damage::RuleError::InvalidRules)
        );
    }
    let hit = damage::Hit {
        base: f64::MAX,
        buff_multiplier: 2.0,
        ..Default::default()
    };
    assert_eq!(
        damage::calculate(&hit, &default),
        Err(damage::RuleError::Overflow)
    );
    assert_eq!(
        damage::speed_multiplier([f64::MAX; 3], [0.0; 3], [-f64::MAX; 3], true, &default),
        Err(damage::RuleError::Overflow)
    );
}

#[test]
fn public_multiplier_entrypoints_validate_inputs() {
    let rules = damage::Rules::default();
    for invalid in [f64::NAN, f64::INFINITY, -1.0] {
        assert_eq!(
            damage::charge_multiplier(invalid, 600.0, &rules),
            Err(damage::RuleError::InvalidEvidence)
        );
        assert_eq!(
            damage::charge_multiplier(0.0, invalid, &rules),
            Err(damage::RuleError::InvalidEvidence)
        );
        assert_eq!(
            damage::phase_multiplier(invalid, &rules),
            Err(damage::RuleError::InvalidEvidence)
        );
    }
    assert_eq!(
        damage::charge_multiplier(0.0, 0.0, &rules),
        Err(damage::RuleError::InvalidEvidence)
    );
    assert_eq!(
        damage::phase_multiplier(1.01, &rules),
        Err(damage::RuleError::InvalidEvidence)
    );
    assert_eq!(
        damage::speed_multiplier([f64::NAN; 3], [0.0; 3], [0.0; 3], false, &rules),
        Err(damage::RuleError::InvalidEvidence)
    );
}

#[test]
fn guard_rejects_nonfinite_evidence_and_invalid_weights() {
    let rules = guard::Rules::default();
    for invalid in [f64::NAN, f64::INFINITY, f64::NEG_INFINITY, -1.0, 0.0] {
        assert_eq!(
            guard::consumption(0, invalid, 1.0, &rules),
            Err(guard::RuleError::InvalidEvidence)
        );
        assert_eq!(
            guard::consumption(0, 1.0, invalid, &rules),
            Err(guard::RuleError::InvalidEvidence)
        );
    }
    for invalid in [f64::NAN, f64::INFINITY, f64::NEG_INFINITY] {
        assert_eq!(
            guard::chamber_window(
                guard::AttackIntent::Up,
                guard::AttackIntent::Down,
                invalid,
                &rules
            ),
            Err(guard::RuleError::InvalidEvidence)
        );
    }
    for invalid in [f64::NAN, f64::INFINITY, -1.0] {
        let context = guard::AttackContext {
            attack_grade: guard::Grade::B,
            held_ms: invalid,
            traits: Default::default(),
            long_pole: false,
        };
        assert_eq!(
            guard::attack_performance(&context, guard::AttackIntent::Up, false, 1, &rules),
            Err(guard::RuleError::InvalidEvidence)
        );
    }
    assert_eq!(
        guard::Grade::from_value(8),
        Err(guard::RuleError::InvalidEvidence)
    );
    assert_eq!(
        guard::consumption(0, f64::MAX, f64::MIN_POSITIVE, &rules),
        Err(guard::RuleError::Overflow)
    );
}

#[test]
fn guard_validates_rules_before_each_operation() {
    let default = guard::Rules::default();
    let context = guard::AttackContext {
        attack_grade: guard::Grade::B,
        held_ms: 600.0,
        traits: Default::default(),
        long_pole: false,
    };
    for rules in [
        guard::Rules {
            maximum: 0.0,
            ..default.clone()
        },
        guard::Rules {
            reset_value: 6.0,
            ..default.clone()
        },
        guard::Rules {
            weight_cost: f64::NAN,
            ..default.clone()
        },
        guard::Rules {
            hit_recovery: -1.0,
            ..default.clone()
        },
        guard::Rules {
            break_damage: vec![],
            ..default.clone()
        },
        guard::Rules {
            break_damage: vec![1.1],
            ..default.clone()
        },
        guard::Rules {
            chamber_start_ms: 201,
            ..default.clone()
        },
        guard::Rules {
            consumption_min: 3.0,
            ..default.clone()
        },
    ] {
        assert_eq!(
            guard::attack_performance(&context, guard::AttackIntent::Up, false, 0, &rules),
            Err(guard::RuleError::InvalidRules)
        );
        assert_eq!(
            guard::chamber_window(
                guard::AttackIntent::Up,
                guard::AttackIntent::Down,
                100.0,
                &rules
            ),
            Err(guard::RuleError::InvalidRules)
        );
        assert_eq!(
            guard::damage_fraction(3, false, &rules),
            Err(guard::RuleError::InvalidRules)
        );
        assert_eq!(
            guard::consumption(0, 1.0, 1.0, &rules),
            Err(guard::RuleError::InvalidRules)
        );
        assert_eq!(
            guard::body_reaction(
                guard::Grade::B,
                guard::ArmorType::Light,
                false,
                false,
                false,
                &rules
            ),
            Err(guard::RuleError::InvalidRules)
        );
    }
}
