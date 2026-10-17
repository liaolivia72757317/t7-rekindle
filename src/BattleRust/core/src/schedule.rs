use std::collections::{BTreeMap, VecDeque};

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
pub enum EventClass {
    Causal,
    Decision,
    Animation,
}

#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord)]
pub struct TimerKey {
    // Preserve the existing native map order when several timers are overdue.
    id: String,
    owner: Owner,
    class: EventClass,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
enum Owner {
    Connection(u64),
    Match(u64),
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ScheduleError {
    InvalidKey,
    Capacity,
    ClockOverflow,
}

impl TimerKey {
    pub fn connection(connection: u64, name: &str) -> Result<Self, ScheduleError> {
        Self::new(connection.to_string(), Owner::Connection(connection), name)
    }

    pub fn for_match(id: u64, name: &str) -> Result<Self, ScheduleError> {
        Self::new(format!("match:{id}"), Owner::Match(id), name)
    }

    fn new(prefix: String, owner: Owner, name: &str) -> Result<Self, ScheduleError> {
        let id = format!("{prefix}:{name}");
        if matches!(owner, Owner::Connection(0) | Owner::Match(0))
            || name.is_empty()
            || name.contains(':')
            || id.len() > 128
            || name.chars().any(char::is_control)
        {
            return Err(ScheduleError::InvalidKey);
        }
        let class = if [
            "battle-action-phase",
            "bot-action-phase",
            "bot-release",
            "combat-hurt-",
        ]
        .iter()
        .any(|prefix| name.starts_with(prefix))
        {
            EventClass::Animation
        } else if matches!(name, "actors-pulse" | "actors-decision" | "bot-think")
            || name.starts_with("bot-think-")
        {
            EventClass::Decision
        } else {
            EventClass::Causal
        };
        Ok(Self { id, owner, class })
    }

    pub fn as_str(&self) -> &str {
        &self.id
    }
}

pub struct QueuedEvent<T> {
    pub value: T,
    pub generation: Option<u64>,
    pub due_at_ms: Option<u64>,
    class: EventClass,
    key: Option<TimerKey>,
    bytes: usize,
}

struct Timer<T> {
    due_at_ms: u64,
    event: QueuedEvent<T>,
}

pub struct Scheduler<T> {
    events: VecDeque<QueuedEvent<T>>,
    timers: BTreeMap<TimerKey, Timer<T>>,
    bytes: usize,
    event_limit: usize,
    byte_limit: usize,
    timer_limit: usize,
    generation: u64,
}

impl<T> Scheduler<T> {
    pub fn new(event_limit: usize, byte_limit: usize, timer_limit: usize) -> Self {
        Self {
            events: VecDeque::new(),
            timers: BTreeMap::new(),
            bytes: 0,
            event_limit,
            byte_limit,
            timer_limit,
            generation: 0,
        }
    }

    pub fn queued_bytes(&self) -> usize {
        self.bytes
    }
    pub fn active_timers(&self) -> usize {
        self.timers.len()
    }

    fn fits(&self, bytes: usize) -> bool {
        self.events.len() < self.event_limit && bytes <= self.byte_limit - self.bytes
    }

    /// `bytes` includes the payload and its queue metadata, not just wire bytes.
    pub fn enqueue(
        &mut self,
        value: T,
        class: EventClass,
        bytes: usize,
    ) -> Result<(), ScheduleError> {
        if !self.fits(bytes) {
            return Err(ScheduleError::Capacity);
        }
        self.events.push_back(QueuedEvent {
            value,
            class,
            bytes,
            key: None,
            generation: None,
            due_at_ms: None,
        });
        self.bytes += bytes;
        Ok(())
    }

    pub fn schedule(
        &mut self,
        key: TimerKey,
        value: T,
        bytes: usize,
        dispatched_ms: u64,
        committed_ms: u64,
        delay_ms: u64,
    ) -> Result<(), ScheduleError> {
        if bytes > self.byte_limit
            || self.event_limit == 0
            || (!self.timers.contains_key(&key) && self.timers.len() >= self.timer_limit)
        {
            return Err(ScheduleError::Capacity);
        }
        let origin = if key.class == EventClass::Animation {
            committed_ms
        } else {
            dispatched_ms
        };
        let due = origin
            .checked_add(delay_ms)
            .ok_or(ScheduleError::ClockOverflow)?;
        let generation = self
            .generation
            .checked_add(1)
            .ok_or(ScheduleError::ClockOverflow)?;
        self.cancel(&key);
        self.generation = generation;
        let event = QueuedEvent {
            value,
            bytes,
            class: key.class,
            key: Some(key.clone()),
            generation: Some(generation),
            due_at_ms: Some(due),
        };
        self.timers.insert(
            key,
            Timer {
                due_at_ms: due,
                event,
            },
        );
        Ok(())
    }

    pub fn cancel(&mut self, key: &TimerKey) {
        self.timers.remove(key);
        self.events.retain(|event| {
            let keep = event.key.as_ref() != Some(key);
            if !keep {
                self.bytes -= event.bytes;
            }
            keep
        });
    }

    pub fn enqueue_due(&mut self, now_ms: u64) {
        let keys: Vec<_> = self
            .timers
            .iter()
            .filter(|(_, timer)| timer.due_at_ms <= now_ms)
            .map(|(key, _)| key.clone())
            .collect();
        for key in keys {
            if !self.fits(self.timers[&key].event.bytes) {
                continue;
            }
            let timer = self.timers.remove(&key).expect("selected timer");
            self.bytes += timer.event.bytes;
            self.events.push_back(timer.event);
        }
    }

    pub fn next_deadline(&self) -> Option<u64> {
        self.timers.values().map(|timer| timer.due_at_ms).min()
    }

    pub fn pop(&mut self) -> Option<QueuedEvent<T>> {
        let index = self
            .events
            .iter()
            .position(|event| event.class != EventClass::Decision)
            .filter(|&index| self.events[index].class == EventClass::Animation)
            .unwrap_or(0);
        let event = self.events.remove(index)?;
        self.bytes -= event.bytes;
        Some(event)
    }

    fn close_owner(&mut self, owner: Owner) {
        self.timers.retain(|key, _| key.owner != owner);
        self.events.retain(|event| {
            let keep = !event.key.as_ref().is_some_and(|key| key.owner == owner);
            if !keep {
                self.bytes -= event.bytes;
            }
            keep
        });
    }

    pub fn close_connection(&mut self, connection: u64) {
        self.close_owner(Owner::Connection(connection));
    }
    pub fn close_match(&mut self, id: u64) {
        self.close_owner(Owner::Match(id));
    }
}
