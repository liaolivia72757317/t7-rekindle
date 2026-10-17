use t7r_battle_core::schedule::{EventClass, Scheduler, TimerKey};

fn timer(name: &str) -> TimerKey {
    TimerKey::connection(1, name).unwrap()
}

#[test]
fn animation_only_overtakes_a_consecutive_decision_prefix() {
    let mut queue = Scheduler::new(10, 1000, 10);
    queue.enqueue(1, EventClass::Causal, 10).unwrap();
    queue.enqueue(2, EventClass::Decision, 10).unwrap();
    queue.enqueue(3, EventClass::Decision, 10).unwrap();
    queue.enqueue(4, EventClass::Animation, 10).unwrap();
    queue.enqueue(5, EventClass::Animation, 10).unwrap();
    queue.enqueue(6, EventClass::Causal, 10).unwrap();
    assert_eq!(
        (0..6)
            .map(|_| queue.pop().unwrap().value)
            .collect::<Vec<_>>(),
        [1, 4, 5, 2, 3, 6]
    );
    assert_eq!(queue.queued_bytes(), 0);
    queue.enqueue(1, EventClass::Decision, 10).unwrap();
    queue.enqueue(2, EventClass::Causal, 10).unwrap();
    queue.enqueue(3, EventClass::Animation, 10).unwrap();
    assert_eq!(
        (0..3)
            .map(|_| queue.pop().unwrap().value)
            .collect::<Vec<_>>(),
        [1, 2, 3]
    );
}

#[test]
fn animation_deadlines_start_at_commit_and_decisions_at_dispatch() {
    let mut queue = Scheduler::new(10, 1000, 10);
    queue
        .schedule(timer("battle-action-phase"), 1, 10, 100, 200, 50)
        .unwrap();
    queue
        .schedule(timer("actors-decision"), 2, 10, 100, 200, 50)
        .unwrap();
    assert_eq!(queue.next_deadline(), Some(150));
    queue.enqueue_due(149);
    assert!(queue.pop().is_none());
    queue.enqueue_due(150);
    assert_eq!(queue.pop().unwrap().value, 2);
    assert_eq!(queue.next_deadline(), Some(250));
    queue.enqueue_due(250);
    assert_eq!(queue.pop().unwrap().value, 1);
}

#[test]
fn reschedule_and_cancel_invalidate_already_enqueued_timers() {
    let mut queue = Scheduler::new(10, 1000, 10);
    let key = timer("battle-action-phase");
    queue.schedule(key.clone(), 1, 10, 0, 0, 0).unwrap();
    queue.enqueue_due(0);
    queue.schedule(key.clone(), 2, 10, 0, 0, 1).unwrap();
    assert!(queue.pop().is_none());
    queue.enqueue_due(1);
    let event = queue.pop().unwrap();
    assert_eq!(event.value, 2);
    assert_eq!(event.generation, Some(2));
    queue.schedule(key.clone(), 3, 10, 1, 1, 0).unwrap();
    queue.enqueue_due(1);
    queue.cancel(&key);
    assert!(queue.pop().is_none());
    assert_eq!(queue.queued_bytes(), 0);
}

#[test]
fn due_timers_follow_earlier_input_and_respect_backpressure() {
    let mut queue = Scheduler::new(2, 20, 2);
    queue.enqueue(1, EventClass::Causal, 15).unwrap();
    queue
        .schedule(timer("combat-hurt-1"), 2, 10, 0, 0, 0)
        .unwrap();
    queue.enqueue_due(0);
    assert_eq!(queue.active_timers(), 1);
    assert_eq!(queue.pop().unwrap().value, 1);
    queue.enqueue_due(0);
    assert_eq!(queue.pop().unwrap().value, 2);
    assert_eq!(queue.active_timers(), 0);
    assert!(queue.enqueue(3, EventClass::Causal, 21).is_err());
    assert_eq!(queue.queued_bytes(), 0);
}

#[test]
fn match_timers_survive_connection_close_and_scope_cleanup_is_exact() {
    let mut queue = Scheduler::new(10, 1000, 10);
    let local = timer("version");
    let shared = TimerKey::for_match(7, "battle-action-phase").unwrap();
    queue.schedule(local, 1, 10, 0, 0, 0).unwrap();
    queue.schedule(shared.clone(), 2, 10, 0, 0, 0).unwrap();
    queue.enqueue_due(0);
    queue.close_connection(1);
    assert_eq!(queue.pop().unwrap().value, 2);
    assert!(queue.pop().is_none());
    queue.schedule(shared, 3, 10, 0, 0, 0).unwrap();
    queue.close_match(7);
    assert_eq!(queue.next_deadline(), None);
    assert!(TimerKey::connection(0, "version").is_err());
    assert!(TimerKey::for_match(0, "version").is_err());
}

#[test]
fn failed_reschedule_does_not_remove_the_old_timer() {
    let mut queue = Scheduler::new(2, 20, 1);
    let key = timer("version");
    queue.schedule(key.clone(), 1, 10, 0, 0, 10).unwrap();
    assert!(queue.schedule(timer("other"), 2, 10, 0, 0, 0).is_err());
    assert!(queue.schedule(key.clone(), 2, 21, 0, 0, 0).is_err());
    assert!(queue.schedule(key, 2, 10, u64::MAX, u64::MAX, 1).is_err());
    queue.enqueue_due(10);
    assert_eq!(queue.pop().unwrap().value, 1);
}
