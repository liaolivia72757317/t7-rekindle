use super::*;
use std::{
    future::{Future, poll_fn},
    pin::pin,
    task::Poll,
};

enum Event {
    Started(bool),
    Request(Option<Request>),
}

pub(super) async fn wait_for_start(
    session: &mut BattleSession,
    requests: &mut BudgetReceiver<Request>,
    started: oneshot::Receiver<()>,
    stopped: &AtomicBool,
) -> bool {
    let mut started = pin!(started);
    loop {
        let event = {
            let mut request = pin!(requests.recv());
            poll_fn(|context| {
                if let Poll::Ready(result) = started.as_mut().poll(context) {
                    Poll::Ready(Event::Started(result.is_ok()))
                } else {
                    request.as_mut().poll(context).map(Event::Request)
                }
            })
            .await
        };
        if stopped.load(Ordering::Acquire) {
            return false;
        }
        match event {
            Event::Started(active) => return active,
            Event::Request(None) => return false,
            Event::Request(Some(request)) => {
                let result = match request.input {
                    RequestInput::SelectHero(selected) => {
                        session.prepare_hero(request.epoch, &selected)
                    }
                    RequestInput::Battle(_) => Err(clock_not_started()),
                };
                let _ = request.reply.send(result);
            }
        }
    }
}
