use super::*;

impl InstanceSession {
    pub(super) async fn handle_message(&mut self, message: Message) -> io::Result<()> {
        if let Some(trace) = &self.trace {
            trace.event(2, "uplink", message.command, &message.body)?;
        }
        if self
            .last_sequence
            .is_some_and(|previous| message.sequence <= previous)
        {
            return Err(io::Error::new(
                io::ErrorKind::InvalidData,
                "stale instance frame sequence",
            ));
        }
        self.last_sequence = Some(message.sequence);
        let native_now_ms = scene_wire::native_uptime_ms();
        if message.command == 0x2c && message.body.starts_with(&[0, 2]) {
            let back_data = leave_wire::decode_sync_logout(&message.body)?;
            if self.leave_back_data.is_none() {
                self.leave_back_data = Some(back_data);
                if self.is_training() && self.game_started_at_ms.is_none() {
                    self.startup_due = None;
                    self.actor_select_due = None;
                    self.round_game_due = None;
                    send_instance_messages(
                        &mut self.writer,
                        vec![leave_wire::instance_leave(native_now_ms)],
                    )
                    .await?;
                    self.leave_due = Some(tokio::time::Instant::now() + Duration::from_millis(250));
                } else if let Some((due, _)) = self.pending_finish.as_mut() {
                    *due = tokio::time::Instant::now();
                } else if self.settlement_seen {
                    send_instance_messages(
                        &mut self.writer,
                        vec![leave_wire::instance_leave(native_now_ms)],
                    )
                    .await?;
                    self.leave_due = Some(tokio::time::Instant::now() + Duration::from_millis(250));
                } else {
                    self.ingress.submit_quit().await?;
                }
            }
        } else if message.command == 0x37 && message.body.starts_with(&[0, 1]) {
            let reply = leave_wire::user_group_quit_response(&message.body)?;
            send_instance_messages(&mut self.writer, vec![reply]).await?;
            self.ingress.mark_room_quit_sent();
        } else if self.leave_back_data.is_some() {
            return Ok(());
        } else if self.is_training() && matches!(message.command, 0x43 | 0x44) {
            let command =
                training_wire::decode(message.command, &message.body)?.expect("training command");
            if self.game_started_at_ms.is_some() {
                self.ingress.submit_training(command).await?;
            }
        } else if let Some(command) = squad_wire::decode(message.command, &message.body)? {
            if self.game_started_at_ms.is_none() || self.settlement_seen {
                send_instance_messages(
                    &mut self.writer,
                    vec![squad_wire::response(command, 810001)],
                )
                .await?;
            } else {
                self.ingress.submit_squad(command).await?;
            }
        } else if movement_report::accept(message.command, &message.body)? {
            // NativeBridge supplies the authoritative pose; these reports need no echo.
        } else if let Some(command) =
            client_battle_wire::decode_action(message.command, &message.body)?
        {
            self.ingress.submit(command).await?;
        } else if let Some(command) =
            client_battle_wire::weapon::decode_change(message.command, &message.body)?
        {
            self.ingress.submit(command).await?;
        } else if message.command == 0x23 && message.body.starts_with(&[0, 3]) {
            if self.entry_phase < EntryPhase::CampSelected || message.body.len() != 3 {
                return Err(io::Error::new(
                    io::ErrorKind::InvalidData,
                    "camp confirmation precedes selection",
                ));
            }
            let identity = read_identity(self.requests.clone()).await?;
            let replies = scene_actor_wire::camp_confirmation(
                native_now_ms,
                self.ingress.map_id(),
                self.ingress.roster(),
                &identity,
            )?;
            send_instance_messages(&mut self.writer, replies).await?;
            self.ingress.bind_identity(identity.clone())?;
            self.scene_identity = Some(identity);
        } else if message.command == 0x0e && message.body.starts_with(&[0, 7]) {
            let identity = self.scene_identity.as_ref().ok_or_else(|| {
                io::Error::new(
                    io::ErrorKind::InvalidData,
                    "VISION objects precede actor import",
                )
            })?;
            if self.settlement_seen {
                return Ok(());
            }
            let hidden = matches!(
                self.respawn_phase,
                RespawnPhase::Dead | RespawnPhase::Selecting | RespawnPhase::Waiting
            );
            let (objects_body, instructor) = if self.is_training() {
                training_wire::split_objects(&message.body)?
            } else {
                (message.body.clone(), false)
            };
            let mut replies = self.refresh.objects(
                &objects_body,
                identity,
                native_now_ms,
                hidden,
                self.game_started_at_ms.is_some() && self.respawn_phase == RespawnPhase::Playing,
            )?;
            if instructor {
                replies.push(training_wire::instructor(native_now_ms));
            }
            send_instance_messages(&mut self.writer, replies).await?;
        } else if message.command == 0x36 && message.body.starts_with(&[0, 0x14]) {
            if self.scene_identity.is_none() {
                return Err(io::Error::new(
                    io::ErrorKind::InvalidData,
                    "actor load confirmation precedes actor import",
                ));
            }
            let reply = scene_wire::actor_load_ok(&message.body, native_now_ms)?;
            if self.entry_phase < EntryPhase::Loaded {
                self.entry_phase = EntryPhase::Loaded;
                if !self.is_training() {
                    send_instance_messages(&mut self.writer, vec![reply]).await?;
                    self.actor_select_due =
                        Some(tokio::time::Instant::now() + Duration::from_millis(1000));
                }
            }
        } else if message.command == 0x36 && message.body.starts_with(&[0, 0x32]) {
            if self.entry_phase < EntryPhase::Loaded {
                return Err(io::Error::new(
                    io::ErrorKind::InvalidData,
                    "hero selection precedes actor load confirmation",
                ));
            }
            if message.body.len() != 6
                || i32::from_be_bytes(message.body[2..6].try_into().expect("checked length")) <= 0
            {
                return Err(io::Error::new(
                    io::ErrorKind::InvalidData,
                    "invalid actor hero-selection request",
                ));
            }
            let requested_position =
                i32::from_be_bytes(message.body[2..6].try_into().expect("checked length"));
            if self.entry_phase == EntryPhase::Entered
                && self.respawn_phase != RespawnPhase::Selecting
            {
                if let Some(trace) = &self.trace {
                    trace.hero_selection(requested_position, self.selected_hero_position, 1)?;
                }
                send_instance_messages(
                    &mut self.writer,
                    vec![scene_wire::actor_choose_hero_result(1)],
                )
                .await?;
                return Ok(());
            }
            let formation = {
                let position = self
                    .ingress
                    .formation(&self.catalogue, self.requests.clone());
                let mut position = pin!(position);
                if let Some(due) = self.actor_select_due {
                    let mut timer = pin!(tokio::time::sleep_until(due));
                    let ready = poll_fn(|context| {
                        if let Poll::Ready(result) = position.as_mut().poll(context) {
                            Poll::Ready(Some(result))
                        } else if timer.as_mut().poll(context).is_ready() {
                            Poll::Ready(None)
                        } else {
                            Poll::Pending
                        }
                    })
                    .await;
                    match ready {
                        Some(result) => result?,
                        None => {
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
                            position.await?
                        }
                    }
                } else {
                    position.await?
                }
            };
            let position = formation.position(self.ingress.roster().local.hero_id)?;
            let selected = formation.choice(requested_position);
            let accepted = match &selected {
                Some(selected) => self.ingress.select_hero(selected.clone()).await?,
                None => false,
            };
            if !accepted {
                if let Some(trace) = &self.trace {
                    trace.hero_selection(requested_position, Some(position), 1)?;
                }
                send_instance_messages(
                    &mut self.writer,
                    vec![scene_wire::actor_choose_hero_result(1)],
                )
                .await?;
                return Ok(());
            }
            let selected = selected.expect("accepted hero selection");
            let replies = scene_wire::actor_choose_hero(
                &message.body,
                self.ingress.client_rid(),
                selected.member.hero_id,
                requested_position,
            )?;
            if let Some(trace) = &self.trace {
                trace.hero_selection(requested_position, Some(requested_position), 0)?;
            }
            send_instance_messages(&mut self.writer, replies.to_vec()).await?;
            self.hero_chosen = true;
            self.selected_hero_position = Some(requested_position);
            self.refresh.roster.local.hero_id = selected.member.hero_id;
            self.refresh.roster.local.max_health = selected.max_health();
            self.refresh.roster.local.equipment = selected.member.equipment.clone();
            self.refresh.roster.local.weapon_id = selected.member.weapon_id;
            self.refresh.roster.local.offhand_id = selected.offhand_id();
            self.refresh.roster.local_shields = selected.shields();
        } else if message.command == 0x36 && message.body.starts_with(&[0, 0x64]) {
            let spawn_request = squad_wire::decode_spawn(&message.body, self.ingress.actor_rids())?;
            if matches!(spawn_request, squad_wire::SpawnRequest::Squad { .. })
                && (self.entry_phase != EntryPhase::Entered
                    || !self.hero_chosen
                    || self.respawn_phase != RespawnPhase::Selecting
                    || self.settlement_seen)
            {
                send_instance_messages(
                    &mut self.writer,
                    vec![scene_wire::actor_play_result(810014)],
                )
                .await?;
                return Ok(());
            }
            if !self.hero_chosen && self.entry_phase != EntryPhase::Entered {
                return Err(io::Error::new(
                    io::ErrorKind::InvalidData,
                    "actor PLAY precedes hero selection",
                ));
            }
            if self.entry_phase != EntryPhase::Entered {
                let play_reply = scene_wire::actor_play(&message.body)?;
                send_instance_messages(&mut self.writer, vec![play_reply]).await?;
                let game_due = tokio::time::Instant::now()
                    + Duration::from_millis(if self.is_training() {
                        50
                    } else {
                        scene_wire::START_MS
                    });
                let identity = self
                    .scene_identity
                    .as_ref()
                    .expect("hero selection requires import");
                let mut entry = scene_entry_wire::battle_entry(
                    native_now_ms,
                    self.started_at_ms,
                    &self.refresh.roster,
                    identity,
                )?;
                if !self.is_training() {
                    entry.push(scene_wire::round_start(native_now_ms)?);
                }
                send_instance_messages(&mut self.writer, entry).await?;
                self.entry_phase = EntryPhase::Entered;
                self.actor_select_due = None;
                self.round_game_due = Some(game_due);
            } else {
                if let squad_wire::SpawnRequest::Squad {
                    target_id,
                    position,
                } = spawn_request
                {
                    self.ingress.submit_squad_spawn(target_id, position).await?;
                    return Ok(());
                }
                scene_wire::actor_play(&message.body)?;
                if self.hero_chosen && self.respawn_phase == RespawnPhase::Selecting {
                    self.ingress.submit_spawn().await?;
                    self.respawn_phase = RespawnPhase::Waiting;
                } else {
                    let ready = matches!(
                        self.respawn_phase,
                        RespawnPhase::Spawning | RespawnPhase::Playing
                    );
                    send_instance_messages(
                        &mut self.writer,
                        vec![scene_wire::actor_play_result(i32::from(!ready))],
                    )
                    .await?;
                }
            }
        } else if message.command == 0x29 && message.body.starts_with(&[0, 0x65]) {
            let replies = if message.body.len() == 10 && message.body[2..6] == 2_i32.to_be_bytes() {
                self.ingress
                    .formation(&self.catalogue, self.requests.clone())
                    .await?
                    .container(&message.body, &self.catalogue)?
            } else {
                loadout::instance_container(&message.body, &self.catalogue, self.requests.clone())
                    .await?
            };
            send_instance_messages(&mut self.writer, replies).await?;
        } else if self.is_training() && message.command == 0x0e && message.body.starts_with(&[0, 5])
        {
            let mut rids = self.ingress.actor_rids().clone();
            rids.insert(61, crate::battle_session::training::INSTRUCTOR_RID as u16);
            let reply = scene_wire::vision_list(&message.body, &rids)?;
            send_instance_messages(&mut self.writer, vec![reply]).await?;
        } else if let Some(mut replies) = scene_wire::reply(
            message.command,
            &message.body,
            native_now_ms,
            &scene_wire::SceneBinding {
                started_at_ms: self.started_at_ms,
                map_id: self.ingress.map_id(),
                camp: self.ingress.camp(),
                actor_rids: self.ingress.actor_rids(),
            },
        )? {
            if message.command == 10 {
                if self.settlement_seen {
                    return Ok(());
                }
                if message.body.starts_with(&[0, 11])
                    && self.entry_phase == EntryPhase::Entered
                    && self.respawn_phase != RespawnPhase::Selecting
                {
                    return Ok(());
                }
                if message.body.starts_with(&[0, 106]) {
                    replies[0] = squad_wire::enable(
                        replies[0].clone(),
                        &self.refresh.squads,
                        self.ingress.actor_rids(),
                    )?;
                    if let Some(started) = self.game_started_at_ms.filter(|_| !self.is_training()) {
                        replies.push(scene_wire::refresh_game(
                            native_now_ms,
                            started,
                            self.ingress.duration_ms(),
                        )?);
                    }
                    replies.push(self.refresh.camp_score(native_now_ms)?);
                }
            }
            send_instance_messages(&mut self.writer, replies).await?;
            if let Some(trace) = &self.trace {
                trace.event(2, "scene-reply-sent", message.command, &message.body)?;
            }
            if message.command == 0x23 && message.body.starts_with(&[0, 1]) {
                self.entry_phase = self.entry_phase.max(EntryPhase::CampSelected);
            }
        } else if let Some(body) =
            compat::reply(&message, false, native_now_ms, &mut self.unify_at)?
        {
            let response = downlink_frame(message.command, &body)?;
            tokio::time::timeout(Duration::from_secs(5), self.writer.send(&response))
                .await
                .map_err(|_| {
                    io::Error::new(io::ErrorKind::TimedOut, "instance write timed out")
                })??;
        } else if message.command == 8 && message.body.starts_with(&[0, 9]) {
            // A validated UNIFY_TIME request inside 250 ms has no reply.
        } else {
            self.unhandled.record(self.trace.as_deref(), 2, &message)?;
        }
        Ok(())
    }
}
