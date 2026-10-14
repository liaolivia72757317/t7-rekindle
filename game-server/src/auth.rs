//! One-use guest admission tickets. Display names are not account ownership.
use crate::limits::{
    LOGIN_ATTEMPTS_PER_SOURCE, LOGIN_WINDOW_SECS, MAX_AUTH_RECORDS, TICKET_TTL_SECS,
};
use serde::{Deserialize, Serialize};
use std::{
    collections::HashMap,
    sync::Mutex,
    time::{Duration, Instant},
};

#[derive(Clone)]
pub struct GuestIdentity {
    pub account_id: String,
    pub display_name: String,
}
struct Ticket {
    identity: GuestIdentity,
    expires: Instant,
}
struct State {
    tickets: HashMap<String, Ticket>,
    attempts: HashMap<String, (Instant, usize)>,
}
pub struct Admission {
    state: Mutex<State>,
}
impl Default for Admission {
    fn default() -> Self {
        Self {
            state: Mutex::new(State {
                tickets: HashMap::new(),
                attempts: HashMap::new(),
            }),
        }
    }
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct GuestRequest {
    pub display_name: String,
}
#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub struct TicketReply {
    pub account_id: String,
    pub display_name: String,
    pub ticket: String,
    pub expires_in_seconds: u64,
}
impl Admission {
    pub fn allow_attempt(&self, source: &str) -> bool {
        let now = Instant::now();
        let mut state = self.state.lock().expect("admission state");
        state
            .attempts
            .retain(|_, (at, _)| now.duration_since(*at) < Duration::from_secs(LOGIN_WINDOW_SECS));
        if !state.attempts.contains_key(source) && state.attempts.len() >= MAX_AUTH_RECORDS {
            return false;
        }
        let attempt = state.attempts.entry(source.to_owned()).or_insert((now, 0));
        attempt.1 += 1;
        attempt.1 <= LOGIN_ATTEMPTS_PER_SOURCE
    }
    pub fn issue(&self, request: GuestRequest) -> Option<TicketReply> {
        if request.display_name.is_empty()
            || request.display_name.chars().count() > 31
            || request.display_name.chars().any(char::is_control)
        {
            return None;
        }
        let now = Instant::now();
        let mut state = self.state.lock().expect("admission state");
        state.tickets.retain(|_, ticket| ticket.expires > now);
        if state.tickets.len() >= MAX_AUTH_RECORDS {
            return None;
        }
        let identity = GuestIdentity {
            account_id: uuid::Uuid::new_v4().to_string(),
            display_name: request.display_name,
        };
        let token = format!(
            "{}{}",
            uuid::Uuid::new_v4().simple(),
            uuid::Uuid::new_v4().simple()
        );
        state.tickets.insert(
            token.clone(),
            Ticket {
                identity: identity.clone(),
                expires: now + Duration::from_secs(TICKET_TTL_SECS),
            },
        );
        Some(TicketReply {
            account_id: identity.account_id,
            display_name: identity.display_name,
            ticket: token,
            expires_in_seconds: TICKET_TTL_SECS,
        })
    }
    pub fn redeem(&self, ticket: &str) -> Option<GuestIdentity> {
        if ticket.len() != 64 || !ticket.bytes().all(|b| b.is_ascii_hexdigit()) {
            return None;
        }
        let value = self
            .state
            .lock()
            .expect("admission state")
            .tickets
            .remove(ticket)?;
        (value.expires > Instant::now()).then_some(value.identity)
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn same_name_gets_independent_identity_and_tickets_are_one_use_and_expiring() {
        let auth = Admission::default();
        assert!(
            auth.issue(GuestRequest {
                display_name: "".into()
            })
            .is_none()
        );
        let first = auth
            .issue(GuestRequest {
                display_name: "Player".into(),
            })
            .unwrap();
        let second = auth
            .issue(GuestRequest {
                display_name: "Player".into(),
            })
            .unwrap();
        assert_ne!(first.account_id, second.account_id);
        let identity = auth.redeem(&first.ticket).unwrap();
        assert_eq!(identity.account_id, first.account_id);
        assert_eq!(identity.display_name, "Player");
        assert!(auth.redeem(&first.ticket).is_none());
        auth.state
            .lock()
            .unwrap()
            .tickets
            .get_mut(&second.ticket)
            .unwrap()
            .expires = Instant::now() - Duration::from_secs(1);
        assert!(auth.redeem(&second.ticket).is_none());
    }
    #[test]
    fn guest_issue_rate_is_bounded_per_source() {
        let auth = Admission::default();
        for _ in 0..LOGIN_ATTEMPTS_PER_SOURCE {
            assert!(auth.allow_attempt("source"));
        }
        assert!(!auth.allow_attempt("source"));
        assert!(auth.allow_attempt("other-source"));
    }
}
