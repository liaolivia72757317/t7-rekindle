use super::{contacts::ContactBatch, *};

const MAX_CONTACTS: usize = 8192;
const MAX_PENDING_CONTACTS: usize = 16384;

impl State {
    pub(super) fn contacts(
        &mut self,
        batch: ContactBatch,
        config: &Config,
        resources: &BattleResources,
        events: &mut Vec<Output>,
    ) -> io::Result<()> {
        if !self.source_contact_through.is_empty() {
            return Err(invalid("mixed contact sources"));
        }
        self.validate_contact_batch(&batch, config)?;
        if self.contact_cursors.get(&0) == Some(&batch.batch_id) {
            return Ok(());
        }
        if batch.through_at_ms < self.contact_through {
            return Err(invalid("regressing contact time"));
        }
        if self
            .contact_cursors
            .get(&0)
            .is_some_and(|&previous| batch.batch_id < previous)
        {
            return Err(invalid("regressing contact batch"));
        }
        self.contact_cursors.insert(0, batch.batch_id);
        if batch.dropped > 0 {
            events.push(Output::ContactOverflow {
                dropped: batch.dropped,
            });
        }
        self.settle_contact_events(batch.through_at_ms, batch.events, config, resources, events)
    }

    pub(super) fn source_contacts(
        &mut self,
        source_id: u64,
        batch: ContactBatch,
        config: &Config,
        resources: &BattleResources,
        events: &mut Vec<Output>,
    ) -> io::Result<()> {
        if source_id == 0 || self.contact_cursors.contains_key(&0) {
            return Err(invalid("mixed contact sources"));
        }
        self.validate_contact_batch(&batch, config)?;
        if self.contact_cursors.get(&source_id) == Some(&batch.batch_id) {
            return Ok(());
        }
        if self
            .contact_cursors
            .get(&source_id)
            .is_some_and(|&previous| batch.batch_id < previous)
            || self
                .source_contact_through
                .get(&source_id)
                .is_some_and(|&previous| {
                    batch.through_at_ms <= previous
                        || batch
                            .events
                            .iter()
                            .any(|event| event.occurred_at_ms <= previous)
                })
        {
            return Err(invalid("regressing source contact batch"));
        }
        if self
            .pending_contacts
            .len()
            .saturating_add(batch.events.len())
            > MAX_PENDING_CONTACTS
        {
            return Err(invalid("pending contact evidence is full"));
        }
        self.contact_cursors.insert(source_id, batch.batch_id);
        self.source_contact_through
            .insert(source_id, batch.through_at_ms);
        if batch.dropped > 0 {
            events.push(Output::ContactOverflow {
                dropped: batch.dropped,
            });
        }
        Arc::make_mut(&mut self.pending_contacts).extend(batch.events);
        let sources = config
            .members
            .iter()
            .map(|member| member.sample_source_id)
            .collect::<BTreeSet<_>>();
        if sources
            .iter()
            .any(|source| !self.source_contact_through.contains_key(source))
        {
            return Ok(());
        }
        let through = sources
            .iter()
            .map(|source| self.source_contact_through[source])
            .fold(f64::INFINITY, f64::min);
        let (ready, pending) = self
            .pending_contacts
            .iter()
            .cloned()
            .partition(|event| event.occurred_at_ms <= through);
        self.pending_contacts = Arc::new(pending);
        self.settle_contact_events(through, ready, config, resources, events)
    }

    fn validate_contact_batch(&self, batch: &ContactBatch, config: &Config) -> io::Result<()> {
        if batch.epoch != config.epoch
            || !batch.through_at_ms.is_finite()
            || batch.through_at_ms > self.now_ms as f64
            || batch.events.len() > MAX_CONTACTS
            || batch
                .events
                .iter()
                .any(|e| e.validate(batch.through_at_ms, self.now_ms).is_err())
        {
            return Err(invalid("invalid native contact batch"));
        }
        Ok(())
    }
}
