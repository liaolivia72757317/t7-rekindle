use std::sync::Arc;
use tokio::sync::{OwnedSemaphorePermit, Semaphore, TryAcquireError, mpsc};

#[derive(Debug)]
pub enum QueueError<T> {
    Full(T),
    Closed(T),
    Oversized(T),
}

struct Envelope<T> {
    value: T,
    _permit: OwnedSemaphorePermit,
}

pub struct BudgetSender<T> {
    channel: mpsc::Sender<Envelope<T>>,
    bytes: Arc<Semaphore>,
    byte_limit: usize,
}

pub struct BudgetReceiver<T> {
    channel: mpsc::Receiver<Envelope<T>>,
    bytes: Arc<Semaphore>,
}

pub fn bounded<T>(
    items: usize,
    bytes: usize,
) -> Result<(BudgetSender<T>, BudgetReceiver<T>), &'static str> {
    if items == 0 || bytes == 0 || bytes > u32::MAX as usize {
        return Err("queue limits must be positive; byte limit must fit u32");
    }
    let (sender, receiver) = mpsc::channel(items);
    let budget = Arc::new(Semaphore::new(bytes));
    Ok((
        BudgetSender {
            channel: sender,
            bytes: budget.clone(),
            byte_limit: bytes,
        },
        BudgetReceiver {
            channel: receiver,
            bytes: budget,
        },
    ))
}

impl<T> Clone for BudgetSender<T> {
    fn clone(&self) -> Self {
        Self {
            channel: self.channel.clone(),
            bytes: self.bytes.clone(),
            byte_limit: self.byte_limit,
        }
    }
}

impl<T> BudgetSender<T> {
    pub fn available_bytes(&self) -> usize {
        self.bytes.available_permits()
    }

    pub async fn send(&self, value: T, bytes: usize) -> Result<(), QueueError<T>> {
        if bytes > self.byte_limit {
            return Err(QueueError::Oversized(value));
        }
        let permit = match self.bytes.clone().acquire_many_owned(bytes as u32).await {
            Ok(permit) => permit,
            Err(_) => return Err(QueueError::Closed(value)),
        };
        self.channel
            .send(Envelope {
                value,
                _permit: permit,
            })
            .await
            .map_err(|error| QueueError::Closed(error.0.value))
    }

    pub fn try_send(&self, value: T, bytes: usize) -> Result<(), QueueError<T>> {
        if bytes > self.byte_limit {
            return Err(QueueError::Oversized(value));
        }
        let permit = match self.bytes.clone().try_acquire_many_owned(bytes as u32) {
            Ok(permit) => permit,
            Err(TryAcquireError::Closed) => return Err(QueueError::Closed(value)),
            Err(TryAcquireError::NoPermits) => return Err(QueueError::Full(value)),
        };
        self.channel
            .try_send(Envelope {
                value,
                _permit: permit,
            })
            .map_err(|error| match error {
                mpsc::error::TrySendError::Full(item) => QueueError::Full(item.value),
                mpsc::error::TrySendError::Closed(item) => QueueError::Closed(item.value),
            })
    }
}

impl<T> BudgetReceiver<T> {
    pub async fn recv(&mut self) -> Option<T> {
        self.channel.recv().await.map(|item| item.value)
    }

    pub fn try_recv(&mut self) -> Result<T, mpsc::error::TryRecvError> {
        self.channel.try_recv().map(|item| item.value)
    }

    pub fn close(&mut self) {
        self.channel.close();
        self.bytes.close();
    }
}

impl<T> Drop for BudgetReceiver<T> {
    fn drop(&mut self) {
        self.bytes.close();
    }
}
