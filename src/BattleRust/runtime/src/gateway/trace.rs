use std::{
    collections::BTreeSet,
    env,
    fs::{File, OpenOptions},
    io::{self, Write},
    path::{Path, PathBuf},
    sync::{Arc, Mutex},
    time::{SystemTime, UNIX_EPOCH},
};

#[derive(Default)]
pub(super) struct UnhandledCommands {
    observed: BTreeSet<(u16, u16)>,
}

impl UnhandledCommands {
    pub(super) fn record(
        &mut self,
        trace: Option<&GatewayTrace>,
        role: usize,
        message: &t7r_battle_core::protocol::Message,
    ) -> io::Result<()> {
        let selector = message.body.get(..2).ok_or_else(|| {
            io::Error::new(io::ErrorKind::InvalidData, "client selector is truncated")
        })?;
        let key = (
            message.command,
            u16::from_be_bytes([selector[0], selector[1]]),
        );
        // Match the Python adapter's per-connection, bounded first-observation log.
        if self.observed.len() < 64 && self.observed.insert(key) {
            if let Some(trace) = trace {
                trace.event(role, "unhandled-ignored", message.command, &message.body)?;
            } else {
                let _ = writeln!(
                    io::stderr().lock(),
                    "WARN [Gateway] role={role} phase=unhandled-ignored command={} selector={} bytes={}",
                    key.0,
                    key.1,
                    message.body.len()
                );
            }
        }
        Ok(())
    }
}

pub(super) struct GatewayTrace {
    file: Mutex<Option<File>>,
}

impl GatewayTrace {
    pub(super) fn open() -> io::Result<Option<Arc<Self>>> {
        let Some(capture) = env::var_os("T7R_BATTLE_CAPTURE_PATH") else {
            return Ok(None);
        };
        Ok(Self::open_path(Path::new(&capture)))
    }

    fn open_path(capture: &Path) -> Option<Arc<Self>> {
        let result = (|| {
            if !capture.is_absolute() {
                return Err(io::Error::new(
                    io::ErrorKind::InvalidInput,
                    "battle capture path must be absolute",
                ));
            }
            let mut name = capture.as_os_str().to_os_string();
            name.push(".gateway.log");
            OpenOptions::new()
                .write(true)
                .create_new(true)
                .open(PathBuf::from(name))
        })();
        match result {
            Ok(file) => Some(Arc::new(Self {
                file: Mutex::new(Some(file)),
            })),
            Err(error) => {
                Self::warn(&error);
                None
            }
        }
    }

    pub(super) fn event(
        &self,
        role: usize,
        phase: &'static str,
        command: u16,
        body: &[u8],
    ) -> io::Result<()> {
        let selector = body
            .get(..2)
            .map(|bytes| u16::from_be_bytes([bytes[0], bytes[1]]))
            .unwrap_or(0);
        self.write_line(format_args!(
            "role={role} phase={phase} command={command} selector={selector} bytes={}",
            body.len()
        ))
    }

    pub(super) fn hero_selection(
        &self,
        requested: i32,
        bound: Option<i32>,
        result: i32,
    ) -> io::Result<()> {
        self.write_line(format_args!(
            "role=2 phase=hero-selection requestedPosition={requested} boundPosition={} result={result}",
            bound.unwrap_or(0)
        ))
    }

    pub(super) fn match_phase(
        &self,
        phase: &'static str,
        epoch: u64,
        now_ms: u64,
    ) -> io::Result<()> {
        self.write_line(format_args!(
            "role=2 phase={phase} epoch={epoch} serverAtMs={now_ms}"
        ))
    }

    pub(super) fn settlement_sent(
        &self,
        finished: bool,
        settlement: &crate::battle_session::Settlement,
    ) -> io::Result<()> {
        use crate::battle_session::EndReason;
        let phase = if finished {
            "settlement-finish-sent"
        } else {
            "settlement-start-sent"
        };
        let reason = match settlement.reason {
            EndReason::Quit => "quit",
            EndReason::Timeout => "timeout",
            EndReason::KillTarget => "kill-target",
        };
        self.write_line(format_args!(
            "role=2 phase={phase} epoch={} reason={reason} result={} members={} startedAtMs={} endedAtMs={}",
            settlement.epoch, settlement.result, settlement.members.len(),
            settlement.started_at_ms, settlement.ended_at_ms
        ))
    }

    pub(super) fn closed(&self, role: usize, error: Option<&io::Error>) -> io::Result<()> {
        if let Some(error) = error {
            let message: String = error
                .to_string()
                .chars()
                .take(512)
                .map(|c| if c.is_control() { ' ' } else { c })
                .collect();
            self.write_line(format_args!(
                "role={role} phase=closed errorKind={:?} error={message}",
                error.kind()
            ))
        } else {
            self.write_line(format_args!("role={role} phase=closed"))
        }
    }

    fn write_line(&self, metadata: std::fmt::Arguments<'_>) -> io::Result<()> {
        let mut slot = match self.file.lock() {
            Ok(slot) => slot,
            Err(error) => {
                if error.into_inner().take().is_some() {
                    Self::warn(&io::Error::other("gateway trace poisoned"));
                }
                return Ok(());
            }
        };
        let Some(file) = slot.as_mut() else {
            return Ok(());
        };
        let result = (|| {
            let at_ms = SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .map_err(io::Error::other)?
                .as_millis();
            writeln!(file, "atMs={at_ms} {metadata}")?;
            file.flush()
        })();
        if let Err(error) = result {
            *slot = None;
            Self::warn(&error);
        }
        Ok(())
    }

    fn warn(error: &io::Error) {
        let _ = writeln!(
            io::stderr().lock(),
            "WARN [Gateway] trace disabled: {error}"
        );
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn failed_trace_write_disables_diagnostics_without_failing_the_connection() {
        let path = Path::new(env!("CARGO_MANIFEST_DIR")).join("Cargo.toml");
        let trace = GatewayTrace {
            file: Mutex::new(Some(File::open(path).unwrap())),
        };
        trace.event(2, "uplink", 35, &[0, 1]).unwrap();
        trace.closed(2, None).unwrap();
        assert!(trace.file.lock().unwrap().is_none());
        assert!(GatewayTrace::open_path(Path::new("relative-capture")).is_none());
    }

    #[test]
    fn trace_records_only_bounded_protocol_metadata() {
        let unique = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let path = env::temp_dir().join(format!(
            "t7r-gateway-trace-{}-{unique}.log",
            std::process::id()
        ));
        let file = OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&path)
            .unwrap();
        let trace = GatewayTrace {
            file: Mutex::new(Some(file)),
        };
        trace.event(2, "uplink", 0x23, &[0, 1, 0, 0, 0, 1]).unwrap();
        trace.hero_selection(2, Some(1), 1).unwrap();
        trace
            .closed(
                2,
                Some(&io::Error::new(
                    io::ErrorKind::InvalidData,
                    format!("invalid selection\r\n{}", "x".repeat(1000)),
                )),
            )
            .unwrap();
        trace.closed(1, None).unwrap();
        trace.match_phase("match-bound", 7, 5000).unwrap();
        trace.match_phase("match-game-started", 7, 6000).unwrap();
        trace.match_phase("logout-sent", 7, 10000).unwrap();
        for reason in [
            crate::battle_session::EndReason::Quit,
            crate::battle_session::EndReason::Timeout,
            crate::battle_session::EndReason::KillTarget,
        ] {
            let settlement = crate::battle_session::Settlement {
                epoch: 7,
                started_at_ms: 6000,
                ended_at_ms: 9000,
                camp_kills: [0, 0],
                reason,
                result: 1,
                members: Vec::new(),
            };
            trace.settlement_sent(false, &settlement).unwrap();
            trace.settlement_sent(true, &settlement).unwrap();
        }
        let mut unhandled = UnhandledCommands::default();
        for index in 0..70 {
            let message = t7r_battle_core::protocol::Message {
                sequence: index + 1,
                command: 65000,
                server_time_ms: 0,
                body: [(index as u16).to_be_bytes().as_slice(), b"private-content"].concat(),
            };
            unhandled.record(Some(&trace), 2, &message).unwrap();
            unhandled.record(Some(&trace), 2, &message).unwrap();
        }
        assert_eq!(unhandled.observed.len(), 64);
        drop(trace);
        let line = std::fs::read_to_string(&path).unwrap();
        std::fs::remove_file(&path).unwrap();
        assert!(line.contains("role=2 phase=uplink command=35 selector=1 bytes=6"));
        assert!(!line.contains("000001"));
        let lines: Vec<_> = line.lines().collect();
        assert_eq!(lines.len(), 77);
        assert!(line.contains("phase=match-game-started epoch=7 serverAtMs=6000"));
        for reason in ["quit", "timeout", "kill-target"] {
            for phase in ["settlement-start-sent", "settlement-finish-sent"] {
                assert!(line.contains(&format!("phase={phase} epoch=7 reason={reason} result=1 members=0 startedAtMs=6000 endedAtMs=9000")));
            }
        }
        assert_eq!(line.matches("phase=unhandled-ignored").count(), 64);
        assert!(!line.contains("private-content"));
        assert!(
            lines[1].contains("phase=hero-selection requestedPosition=2 boundPosition=1 result=1")
        );
        assert!(lines[2].contains("phase=closed errorKind=InvalidData error=invalid selection  "));
        assert_eq!(
            lines[2].split(" error=").nth(1).unwrap().chars().count(),
            512
        );
        assert!(lines[3].ends_with("role=1 phase=closed"));
    }
}
