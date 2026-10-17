//! One match owner with bounded semantic I/O and a monotonic match clock.
mod preparation;
use crate::{
    INPUT_BYTE_LIMIT, INPUT_EVENT_LIMIT, SEND_BYTE_LIMIT,
    battle_session::{BattleSession, Input, Output, OutputContext, PreparedHero},
    mailbox::{self, BudgetReceiver, BudgetSender, QueueError},
};
use std::{
    io,
    mem::size_of,
    sync::{
        Arc, Mutex, OnceLock,
        atomic::{AtomicBool, Ordering},
        mpsc,
    },
    thread::{self, JoinHandle},
    time::{Duration, Instant},
};
use t7r_battle_core::{ai::MovementIntent, navigation::Point};
use tokio::sync::oneshot;

const AI_PULSE_MS: u64 = 100;
const OUTPUT_BATCH_LIMIT: usize = 32;

pub(crate) struct PublishedBatch {
    pub outputs: Vec<Output>,
    pub context: OutputContext,
}

#[derive(Clone)]
struct Clock {
    origin: Arc<OnceLock<Instant>>,
    base_ms: u64,
}

impl Clock {
    fn start(&self) -> io::Result<()> {
        self.origin
            .set(Instant::now())
            .map_err(|_| invalid("match clock is already started"))
    }

    fn now_ms(&self) -> io::Result<u64> {
        let origin = self.origin.get().ok_or_else(clock_not_started)?;
        let elapsed = u64::try_from(origin.elapsed().as_millis())
            .map_err(|_| invalid("match clock overflow"))?;
        self.base_ms
            .checked_add(elapsed)
            .ok_or_else(|| invalid("match clock overflow"))
    }

    fn instant(&self, at_ms: u64) -> io::Result<tokio::time::Instant> {
        let offset = at_ms
            .checked_sub(self.base_ms)
            .ok_or_else(|| invalid("match deadline precedes start"))?;
        self.origin
            .get()
            .ok_or_else(clock_not_started)?
            .checked_add(Duration::from_millis(offset))
            .map(tokio::time::Instant::from_std)
            .ok_or_else(|| invalid("match deadline overflow"))
    }
}

struct Request {
    epoch: u64,
    input: RequestInput,
    reply: oneshot::Sender<io::Result<bool>>,
}

enum RequestInput {
    Battle(Input),
    SelectHero(Arc<PreparedHero>),
}

#[derive(Clone)]
pub struct BattleSender {
    input: BudgetSender<Request>,
    clock: Clock,
}

impl BattleSender {
    pub fn clock_ms(&self) -> io::Result<u64> {
        self.clock.now_ms()
    }

    pub async fn submit(&self, epoch: u64, input: Input) -> io::Result<()> {
        let bytes = input_bytes(&input)?;
        self.submit_request(epoch, RequestInput::Battle(input), bytes)
            .await
            .map(|_| ())
    }

    pub(crate) async fn select_hero(
        &self,
        epoch: u64,
        selected: Arc<PreparedHero>,
    ) -> io::Result<bool> {
        self.submit_request(
            epoch,
            RequestInput::SelectHero(selected),
            size_of::<Request>(),
        )
        .await
    }

    async fn submit_request(
        &self,
        epoch: u64,
        input: RequestInput,
        bytes: usize,
    ) -> io::Result<bool> {
        if matches!(&input, RequestInput::Battle(_)) {
            self.clock.now_ms()?;
        }
        let (reply, receipt) = oneshot::channel();
        self.input
            .try_send(
                Request {
                    epoch,
                    input,
                    reply,
                },
                bytes,
            )
            .map_err(queue_error)?;
        receipt
            .await
            .map_err(|_| io::Error::new(io::ErrorKind::BrokenPipe, "battle owner stopped"))?
    }
}

pub struct BattleWorker {
    sender: BattleSender,
    outputs: BudgetReceiver<PublishedBatch>,
    stop: Arc<AtomicBool>,
    start: Option<BattleActivator>,
    thread: Option<JoinHandle<io::Result<()>>>,
}

#[derive(Clone)]
pub(crate) struct BattleActivator {
    clock: Clock,
    start: Arc<Mutex<Option<oneshot::Sender<()>>>>,
}

impl BattleActivator {
    pub(crate) fn activate(&self) -> io::Result<()> {
        let start = self
            .start
            .lock()
            .map_err(|_| io::Error::other("match activation poisoned"))?
            .take()
            .ok_or_else(|| invalid("match clock is already started"))?;
        self.clock.start()?;
        start
            .send(())
            .map_err(|_| io::Error::new(io::ErrorKind::BrokenPipe, "battle owner stopped"))
    }

    fn cancel(&self) {
        self.start
            .lock()
            .unwrap_or_else(|error| error.into_inner())
            .take();
    }
}

impl BattleWorker {
    pub fn start(session: BattleSession) -> io::Result<Self> {
        Self::start_with_limits(
            session,
            OUTPUT_BATCH_LIMIT,
            SEND_BYTE_LIMIT,
            AI_PULSE_MS,
            false,
        )
    }

    pub(crate) fn start_paused(session: BattleSession) -> io::Result<Self> {
        Self::start_with_limits(
            session,
            OUTPUT_BATCH_LIMIT,
            SEND_BYTE_LIMIT,
            AI_PULSE_MS,
            true,
        )
    }

    fn start_with_limits(
        mut session: BattleSession,
        output_items: usize,
        output_bytes: usize,
        pulse_ms: u64,
        paused: bool,
    ) -> io::Result<Self> {
        if pulse_ms == 0 {
            return Err(invalid("match pulse must be positive"));
        }
        let (input, mut requests) =
            mailbox::bounded(INPUT_EVENT_LIMIT, INPUT_BYTE_LIMIT).map_err(invalid)?;
        let (outgoing, outputs) = mailbox::bounded(output_items, output_bytes).map_err(invalid)?;
        let clock = Clock {
            origin: Arc::new(OnceLock::new()),
            base_ms: session.started_at_ms(),
        };
        if !paused {
            clock.start()?;
        }
        let (start, start_receiver) = if paused {
            let (sender, receiver) = oneshot::channel();
            (
                Some(BattleActivator {
                    clock: clock.clone(),
                    start: Arc::new(Mutex::new(Some(sender))),
                }),
                Some(receiver),
            )
        } else {
            (None, None)
        };
        let stop = Arc::new(AtomicBool::new(false));
        let thread_stop = stop.clone();
        let thread_clock = clock.clone();
        let (ready, ready_receiver) = mpsc::sync_channel(1);
        let thread = thread::Builder::new()
            .name("t7r-battle-owner".into())
            .spawn(move || {
                let runtime = tokio::runtime::Builder::new_current_thread()
                    .enable_time()
                    .build()?;
                ready
                    .send(())
                    .map_err(|_| io::Error::other("battle owner closed during startup"))?;
                runtime.block_on(async move {
                    if let Some(started) = start_receiver
                        && !preparation::wait_for_start(
                            &mut session,
                            &mut requests,
                            started,
                            &thread_stop,
                        )
                        .await
                    {
                        return Ok(());
                    }
                    run(
                        session,
                        requests,
                        outgoing,
                        thread_stop,
                        thread_clock,
                        pulse_ms,
                    )
                    .await
                })
            })?;
        if ready_receiver.recv().is_err() {
            stop.store(true, Ordering::Release);
            return thread
                .join()
                .unwrap_or_else(|_| Err(io::Error::other("battle owner panicked")))
                .and(Err(io::Error::other("battle owner stopped before ready")));
        }
        Ok(Self {
            sender: BattleSender { input, clock },
            outputs,
            stop,
            start,
            thread: Some(thread),
        })
    }

    pub(crate) fn activate(&self) -> io::Result<()> {
        if !self.is_running() {
            return Err(io::Error::new(
                io::ErrorKind::BrokenPipe,
                "battle owner stopped",
            ));
        }
        self.start
            .as_ref()
            .ok_or_else(|| invalid("match clock is already started"))?
            .activate()
    }

    pub(crate) fn activator(&self) -> Option<BattleActivator> {
        self.start.clone()
    }

    pub fn sender(&self) -> BattleSender {
        self.sender.clone()
    }

    pub fn is_running(&self) -> bool {
        !self.stop.load(Ordering::Acquire)
            && self
                .thread
                .as_ref()
                .is_some_and(|thread| !thread.is_finished())
    }

    pub async fn recv(&mut self) -> Option<Vec<Output>> {
        self.outputs.recv().await.map(|batch| batch.outputs)
    }

    pub fn try_recv(&mut self) -> Result<Vec<Output>, tokio::sync::mpsc::error::TryRecvError> {
        self.outputs.try_recv().map(|batch| batch.outputs)
    }

    pub(crate) fn try_recv_contextual(
        &mut self,
    ) -> Result<PublishedBatch, tokio::sync::mpsc::error::TryRecvError> {
        self.outputs.try_recv()
    }

    pub fn stop(&mut self) -> io::Result<()> {
        self.stop.store(true, Ordering::Release);
        if let Some(start) = self.start.take() {
            start.cancel();
        }
        if let Some(thread) = self.thread.take() {
            thread
                .join()
                .unwrap_or_else(|_| Err(io::Error::other("battle owner panicked")))?;
        }
        Ok(())
    }
}

impl Drop for BattleWorker {
    fn drop(&mut self) {
        let _ = self.stop();
    }
}

async fn run(
    mut session: BattleSession,
    mut requests: BudgetReceiver<Request>,
    outgoing: BudgetSender<PublishedBatch>,
    stopped: Arc<AtomicBool>,
    clock: Clock,
    pulse_ms: u64,
) -> io::Result<()> {
    let mut pulse_due = clock.base_ms.saturating_add(pulse_ms);
    let mut processed_deadline = None;
    loop {
        if stopped.load(Ordering::Acquire) {
            return Ok(());
        }
        let timer_due = session.next_deadline_ms(processed_deadline);
        let next_due = if session.settlement().is_some() {
            None
        } else {
            Some(timer_due.map_or(pulse_due, |due| due.min(pulse_due)))
        };
        let now = clock.now_ms()?;
        if let Some(due) = next_due.filter(|&due| due <= now) {
            // Dispatch rolls back on WouldBlock. Keep the original event time and
            // do not let later requests overtake this timer while its output is full.
            let retry_started = Instant::now();
            loop {
                if stopped.load(Ordering::Acquire) {
                    return Ok(());
                }
                let input = if timer_due == Some(due) {
                    Input::Timers
                } else {
                    Input::Tick
                };
                match session.dispatch_with_context(
                    session.epoch(),
                    now,
                    input,
                    |events, context| publish(&outgoing, events, context),
                ) {
                    Ok(()) => break,
                    Err(error) if error.kind() == io::ErrorKind::WouldBlock => {
                        if retry_started.elapsed() >= Duration::from_secs(5) {
                            return Err(io::Error::new(
                                io::ErrorKind::TimedOut,
                                "battle timer output backpressure exceeded 5 seconds",
                            ));
                        }
                        match tokio::time::timeout(Duration::from_millis(5), requests.recv()).await
                        {
                            Ok(Some(request)) => {
                                let _ = request.reply.send(Err(io::Error::new(
                                    io::ErrorKind::WouldBlock,
                                    "battle timer output is pending",
                                )));
                            }
                            Ok(None) => return Ok(()),
                            Err(_) => {}
                        }
                    }
                    Err(error) => return Err(error),
                }
            }
            if timer_due == Some(due) {
                processed_deadline = Some(due);
            } else {
                let intervals = now.saturating_sub(pulse_due) / pulse_ms + 1;
                let advance = intervals
                    .checked_mul(pulse_ms)
                    .ok_or_else(|| invalid("match pulse overflow"))?;
                pulse_due = pulse_due
                    .checked_add(advance)
                    .ok_or_else(|| invalid("match pulse overflow"))?;
                processed_deadline = None;
            }
            continue;
        }
        let poll_at = tokio::time::Instant::now() + Duration::from_millis(AI_PULSE_MS);
        let poll_at = next_due
            .map(|due| clock.instant(due))
            .transpose()?
            .map_or(poll_at, |deadline| deadline.min(poll_at));
        match tokio::time::timeout_at(poll_at, requests.recv()).await {
            Ok(Some(request)) => {
                if stopped.load(Ordering::Acquire) {
                    return Ok(());
                }
                let now_ms = clock.now_ms()?;
                let result = match request.input {
                    RequestInput::Battle(input) => session
                        .dispatch_with_context(request.epoch, now_ms, input, |events, context| {
                            publish(&outgoing, events, context)
                        })
                        .map(|_| true),
                    RequestInput::SelectHero(selected) => {
                        session.select_hero(request.epoch, now_ms, &selected, |events, context| {
                            publish(&outgoing, events, context)
                        })
                    }
                };
                let _ = request.reply.send(result);
                processed_deadline = None;
            }
            Ok(None) => return Ok(()),
            Err(_) => {}
        }
    }
}

fn publish(
    outgoing: &BudgetSender<PublishedBatch>,
    events: &[Output],
    context: &OutputContext,
) -> io::Result<()> {
    if events.is_empty() {
        return Ok(());
    }
    outgoing
        .try_send(
            PublishedBatch {
                outputs: events.to_vec(),
                context: context.clone(),
            },
            output_bytes(events, context)?,
        )
        .map_err(queue_error)
}

fn input_bytes(input: &Input) -> io::Result<usize> {
    let extra = match input {
        Input::World(world) => world
            .actors
            .len()
            .checked_mul(size_of::<(u32, crate::battle_session::Pose)>() + 128),
        Input::SourceWorld(world) => world
            .actors
            .len()
            .checked_mul(size_of::<(u32, crate::battle_session::Pose)>() + 128),
        Input::Contacts(batch) | Input::SourceContacts { batch, .. } => batch
            .events
            .capacity()
            .checked_mul(size_of::<crate::battle_session::contacts::ContactEvent>()),
        _ => Some(0),
    }
    .ok_or_else(|| invalid("battle input size overflow"))?;
    size_of::<Request>()
        .checked_add(extra)
        .ok_or_else(|| invalid("battle input size overflow"))
}

fn output_bytes(events: &[Output], context: &OutputContext) -> io::Result<usize> {
    let mut bytes = size_of::<PublishedBatch>()
        .checked_add(
            events
                .len()
                .checked_mul(size_of::<Output>())
                .ok_or_else(|| invalid("battle output size overflow"))?,
        )
        .and_then(|value| value.checked_add(context.actor_poses.len().checked_mul(128)?))
        .ok_or_else(|| invalid("battle output size overflow"))?;
    for event in events {
        let extra = match event {
            Output::Squad {
                previous, roster, ..
            } => previous.heap_bytes() + roster.heap_bytes(),
            Output::State(state) => state
                .actors
                .capacity()
                .checked_mul(size_of::<
                    t7r_battle_core::battle_semantics::state::StateActor,
                >())
                .ok_or_else(|| invalid("battle output size overflow"))?,
            Output::Damage { .. } | Output::Parry { .. } | Output::SurfaceHit { .. } => {
                size_of::<crate::battle_session::contacts::ContactEvent>()
            }
            Output::Ended(settlement) => settlement
                .members
                .capacity()
                .checked_mul(size_of::<crate::battle_session::RankedScore>())
                .ok_or_else(|| invalid("battle output size overflow"))?,
            Output::Bot(bot) => match &bot.movement {
                MovementIntent::Follow { points, .. } => points
                    .capacity()
                    .checked_mul(size_of::<Point>())
                    .ok_or_else(|| invalid("battle output size overflow"))?,
                _ => 0,
            },
            _ => 0,
        };
        bytes = bytes
            .checked_add(extra)
            .ok_or_else(|| invalid("battle output size overflow"))?;
    }
    Ok(bytes)
}

fn queue_error<T>(error: QueueError<T>) -> io::Error {
    match error {
        QueueError::Full(_) => io::Error::new(io::ErrorKind::WouldBlock, "battle queue full"),
        QueueError::Closed(_) => io::Error::new(io::ErrorKind::BrokenPipe, "battle queue closed"),
        QueueError::Oversized(_) => invalid("battle message exceeds queue budget"),
    }
}

fn invalid(message: impl Into<String>) -> io::Error {
    io::Error::new(io::ErrorKind::InvalidData, message.into())
}

fn clock_not_started() -> io::Error {
    io::Error::new(io::ErrorKind::WouldBlock, "match clock has not started")
}

#[cfg(test)]
mod tests;
