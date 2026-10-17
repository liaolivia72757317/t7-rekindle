//! Per-life guard recovery follows contact time, never business dispatch latency.
use crate::guard::Rules;
use std::fmt;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct MeterError;

impl fmt::Display for MeterError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str("invalid guard meter state, rules or evidence")
    }
}
impl std::error::Error for MeterError {}

#[derive(Debug, Clone, Copy, PartialEq)]
pub struct GuardMeter {
    value: f64,
    at: f64,
    last_block_at: f64,
}

#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Consumption {
    pub meter: GuardMeter,
    pub exhausted: bool,
}

impl GuardMeter {
    pub fn new(rules: &Rules) -> Result<Self, MeterError> {
        let meter = Self {
            value: rules.maximum,
            at: 0.,
            last_block_at: 0.,
        };
        meter.validate(rules)?;
        Ok(meter)
    }

    pub fn value(&self) -> f64 {
        self.value
    }

    pub fn at(&self) -> f64 {
        self.at
    }

    pub fn last_block_at(&self) -> f64 {
        self.last_block_at
    }

    fn validate(&self, rules: &Rules) -> Result<(), MeterError> {
        rules.validate().map_err(|_| MeterError)?;
        if rules.reset_value > rules.maximum
            || ![self.value, self.at, self.last_block_at]
                .into_iter()
                .all(|v| v.is_finite() && v >= 0.)
            || self.value > rules.maximum
            || self.last_block_at > self.at
        {
            return Err(MeterError);
        }
        Ok(())
    }

    pub fn advance(&self, at: f64, rules: &Rules) -> Result<Self, MeterError> {
        self.validate(rules)?;
        if !at.is_finite() || at < 0. {
            return Err(MeterError);
        }
        let at = at.max(self.at);
        let elapsed = (at
            - self
                .at
                .max(self.last_block_at + rules.recover_delay_ms as f64))
        .max(0.);
        Ok(Self {
            value: rules
                .maximum
                .min(self.value + elapsed * rules.recover_per_second / 1000.),
            at,
            ..*self
        })
    }

    pub fn consume(&self, amount: f64, at: f64, rules: &Rules) -> Result<Consumption, MeterError> {
        if !amount.is_finite() || amount < 0. {
            return Err(MeterError);
        }
        let previous = self.advance(at, rules)?;
        let remaining = (previous.value - amount).max(0.);
        let exhausted = remaining <= 1e-9;
        Ok(Consumption {
            meter: Self {
                value: if exhausted {
                    rules.reset_value
                } else {
                    remaining
                },
                last_block_at: at.max(previous.last_block_at),
                ..previous
            },
            exhausted,
        })
    }

    pub fn restore(&self, at: f64, rules: &Rules) -> Result<Self, MeterError> {
        let previous = self.advance(at, rules)?;
        Ok(Self {
            value: rules.maximum.min(previous.value + rules.hit_recovery),
            ..previous
        })
    }
}
