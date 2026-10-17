use serde_json::Value;
use t7r_battle_core::{guard::Rules, guard_meter::GuardMeter};

#[test]
fn frozen_python_watermark_transitions() {
    let corpus: Value = serde_json::from_str(include_str!(
        "../../../../tests/fixtures/rules/guard-meter-v1.json"
    ))
    .unwrap();
    let rules = Rules::default();
    for case in corpus["cases"].as_array().unwrap() {
        let mut meter = GuardMeter::new(&rules).unwrap();
        for event in case["events"].as_array().unwrap() {
            let previous = meter.value();
            let at = event["at"].as_f64().unwrap();
            let mut exhausted = false;
            meter = match event["kind"].as_str().unwrap() {
                "advance" => meter.advance(at, &rules).unwrap(),
                "restore" => meter.restore(at, &rules).unwrap(),
                "reset" => GuardMeter::new(&rules).unwrap(),
                "consume" => {
                    let result = meter
                        .consume(event["amount"].as_f64().unwrap(), at, &rules)
                        .unwrap();
                    exhausted = result.exhausted;
                    result.meter
                }
                other => panic!("unknown operation {other}"),
            };
            let expected = &event["meter"];
            assert_eq!(
                meter.value(),
                expected["value"].as_f64().unwrap(),
                "{event}"
            );
            assert_eq!(meter.at(), expected["at"].as_f64().unwrap(), "{event}");
            assert_eq!(
                meter.last_block_at(),
                expected["lastBlockAt"].as_f64().unwrap()
            );
            assert_eq!(
                meter.value() != previous,
                event["changed"].as_bool().unwrap()
            );
            assert_eq!(exhausted, event["exhausted"].as_bool().unwrap());
        }
    }
}

#[test]
fn invalid_input_keeps_prior_state() {
    let rules = Rules::default();
    let meter = GuardMeter::new(&rules).unwrap();
    for invalid in [f64::NAN, f64::INFINITY, f64::NEG_INFINITY, -1.] {
        assert!(meter.advance(invalid, &rules).is_err());
        assert!(meter.consume(invalid, 100., &rules).is_err());
        assert!(meter.consume(1., invalid, &rules).is_err());
        assert!(meter.restore(invalid, &rules).is_err());
    }
    assert_eq!(meter.value(), 5.);
    assert_eq!(meter.at(), 0.);
    assert_eq!(meter.last_block_at(), 0.);
    let invalid = Rules {
        maximum: 1.,
        ..rules
    };
    assert!(GuardMeter::new(&invalid).is_err());
    assert!(meter.advance(100., &invalid).is_err());
}
