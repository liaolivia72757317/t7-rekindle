use super::*;

impl InstanceSession {
    pub(super) async fn handle_timer(&mut self) -> io::Result<()> {
        if self
            .pending_finish
            .as_ref()
            .is_some_and(|(due, _)| *due <= tokio::time::Instant::now())
        {
            let (_, settlement) = self.pending_finish.take().expect("finish was due");
            if self.scene_identity.is_none() {
                self.scene_identity = Some(read_identity(self.requests.clone()).await?);
            }
            let identity = self.scene_identity.as_ref().expect("identity was loaded");
            // The projected match clock can lead the quantized native uptime slightly.
            let now_ms = scene_wire::native_uptime_ms().max(settlement.ended_at_ms);
            if !self.is_training() {
                let replies = client_battle_wire::encode_settlement_finish_messages(
                    &settlement,
                    self.ingress.actor_id(),
                    u32::try_from(identity.level).map_err(|_| {
                        io::Error::new(io::ErrorKind::InvalidData, "invalid settlement level")
                    })?,
                    scene_actor_wire::LOCAL_USER_ID,
                    now_ms,
                    self.ingress.actor_rids(),
                )?;
                send_instance_messages(&mut self.writer, replies.to_vec()).await?;
            }
            if let Some(trace) = &self.trace {
                trace.settlement_sent(true, &settlement)?;
            }
            if self.leave_back_data.is_some() {
                send_instance_messages(&mut self.writer, vec![leave_wire::instance_leave(now_ms)])
                    .await?;
                self.leave_due = Some(tokio::time::Instant::now() + Duration::from_millis(250));
            }
            return Ok(());
        }
        if self
            .leave_due
            .is_some_and(|due| due <= tokio::time::Instant::now())
        {
            let now_ms = scene_wire::native_uptime_ms();
            send_instance_messages(
                &mut self.writer,
                vec![leave_wire::sync_logout_response(
                    now_ms,
                    self.leave_back_data.expect("leave data was bound"),
                )],
            )
            .await?;
            self.leave_due = None;
            self.ingress.mark_logout_sent();
            if let Some(trace) = &self.trace {
                trace.match_phase("logout-sent", self.ingress.epoch(), now_ms)?;
            }
            return Ok(());
        }
        if self
            .actor_select_due
            .is_some_and(|due| due <= tokio::time::Instant::now())
        {
            let now_ms = scene_wire::native_uptime_ms();
            send_instance_messages(
                &mut self.writer,
                vec![scene_wire::actor_state(
                    self.ingress.client_rid(),
                    5,
                    now_ms,
                )?],
            )
            .await?;
            self.actor_select_due = None;
            return Ok(());
        }
        if self
            .round_game_due
            .is_some_and(|due| due <= tokio::time::Instant::now())
        {
            self.ingress.activate_match()?;
            let now_ms = scene_wire::native_uptime_ms();
            let elapsed = now_ms
                .checked_sub(self.started_at_ms)
                .and_then(|value| u32::try_from(value).ok())
                .ok_or_else(|| {
                    io::Error::new(
                        io::ErrorKind::InvalidData,
                        "battle start is outside instance clock",
                    )
                })?;
            let mut messages = vec![
                scene_wire::round_game(
                    now_ms,
                    if self.is_training() {
                        0
                    } else {
                        self.ingress.duration_ms()
                    },
                )?,
                scene_wire::actor_state(self.ingress.client_rid(), 6, now_ms)?,
                scene_entry_wire::move_active(elapsed, true),
            ];
            if self.is_training() {
                // Native action input ignores the pre-GAME Idle sent during scene import.
                messages.push(scene_entry_wire::idle_state(
                    self.ingress.client_rid(),
                    1,
                    elapsed,
                ));
            }
            send_instance_messages(&mut self.writer, messages).await?;
            self.round_game_due = None;
            self.game_started_at_ms = Some(now_ms);
            if self.is_training() {
                send_instance_messages(
                    &mut self.writer,
                    vec![training_wire::instructor(self.ingress.map_id(), now_ms)],
                )
                .await?;
                self.ingress
                    .submit_training(crate::battle_session::training::Command::Ready)
                    .await?;
            }
            if let Some(trace) = &self.trace {
                trace.match_phase("match-game-started", self.ingress.epoch(), now_ms)?;
            }
            return Ok(());
        }
        if self.time_sync_due <= tokio::time::Instant::now() {
            let message = compat::periodic_time_sync(scene_wire::native_uptime_ms());
            if let Some(trace) = &self.trace {
                trace.event(2, "time-sync", message.command, &message.body)?;
            }
            send_instance_messages(&mut self.writer, vec![message]).await?;
            self.time_sync_due = tokio::time::Instant::now() + Duration::from_secs(2);
            return Ok(());
        }
        let now_ms = scene_wire::native_uptime_ms();
        let message =
            scene_wire::startup_phase(self.startup_phase, now_ms, self.ingress.duration_ms())?
                .ok_or_else(|| io::Error::other("instance startup phase is missing"))?;
        let response = downlink_frame(message.command, &message.body)?;
        tokio::time::timeout(Duration::from_secs(5), self.writer.send(&response))
            .await
            .map_err(|_| {
                io::Error::new(io::ErrorKind::TimedOut, "instance startup write timed out")
            })??;
        self.startup_phase += 1;
        self.startup_due = (self.startup_phase < scene_wire::STARTUP_PHASES)
            .then(|| tokio::time::Instant::now() + Duration::from_millis(200));

        Ok(())
    }
}
