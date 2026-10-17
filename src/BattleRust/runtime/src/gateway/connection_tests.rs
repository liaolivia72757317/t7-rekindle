use super::*;

#[test]
fn time_sync_messages_match_original_handlers_and_keep_outer_timestamp_zero() {
    let rows = include_str!("../../../../../tests/fixtures/protocol/gateway-clock-v1.txt")
        .lines()
        .map(|line| line.split_whitespace().collect::<Vec<_>>())
        .collect::<Vec<_>>();
    let message = Message {
        sequence: 1,
        command: 8,
        server_time_ms: 0,
        body: vec![0, 9, 0],
    };
    let reply = compat::reply(&message, false, 5000, &mut None)
        .unwrap()
        .unwrap();
    let periodic = compat::periodic_time_sync(5000);
    for (row, (command, body)) in rows
        .iter()
        .zip([(8, reply), (periodic.command, periodic.body)])
    {
        assert_eq!(command, row[1].parse::<u16>().unwrap());
        assert_eq!(
            body.iter().map(|b| format!("{b:02x}")).collect::<String>(),
            row[3]
        );
        let frame = downlink_frame(command, &body).unwrap();
        assert_eq!(&frame[15..23], &[0; 8]);
    }
}

#[test]
fn downlink_frames_preserve_zero_outer_timestamp_and_original_body_clocks() {
    let mut messages = scene_wire::instance_start(5000, 1028, 1).unwrap().to_vec();
    messages.extend(
        scene_wire::reply(
            35,
            &[0, 1, 0, 0, 0, 1],
            5000,
            &scene_wire::SceneBinding {
                started_at_ms: 5000,
                map_id: 1028,
                camp: 1,
                actor_rids: &std::collections::BTreeMap::from([(1, 1)]),
            },
        )
        .unwrap()
        .unwrap(),
    );
    let rows = include_str!("../../../../../tests/fixtures/protocol/gateway-downlink-v1.txt")
        .lines()
        .filter(|line| !line.starts_with('#') && !line.is_empty())
        .collect::<Vec<_>>();
    assert_eq!(rows.len(), messages.len());
    for (row, message) in rows.into_iter().zip(messages) {
        let fields = row.split_whitespace().collect::<Vec<_>>();
        assert_eq!(fields[1].parse::<u16>().unwrap(), message.command);
        let expected = fields[2]
            .as_bytes()
            .chunks_exact(2)
            .map(|pair| u8::from_str_radix(std::str::from_utf8(pair).unwrap(), 16).unwrap())
            .collect::<Vec<_>>();
        assert_eq!(
            downlink_frame(message.command, &message.body).unwrap(),
            expected
        );
    }
}

fn runtime() -> tokio::runtime::Runtime {
    tokio::runtime::Builder::new_current_thread()
        .enable_time()
        .build()
        .unwrap()
}

#[test]
fn queued_send_waits_for_capacity_and_preserves_order_and_byte_budget() {
    runtime().block_on(async {
        let (sender, mut receiver) = mailbox::bounded(1, 4).unwrap();
        send_queued(&sender, 1, 4, "test queue").await.unwrap();
        let mut pending = pin!(send_queued(&sender, 2, 4, "test queue"));
        poll_fn(|context| {
            assert!(pending.as_mut().poll(context).is_pending());
            Poll::Ready(())
        })
        .await;
        assert_eq!(sender.available_bytes(), 0);
        assert_eq!(receiver.recv().await, Some(1));
        pending.await.unwrap();
        assert_eq!(receiver.recv().await, Some(2));
        assert_eq!(sender.available_bytes(), 4);
    });
}

#[test]
fn queued_send_reports_oversized_and_closed_queues() {
    runtime().block_on(async {
        let (sender, receiver) = mailbox::bounded(1, 4).unwrap();
        let error = send_queued(&sender, 1, 5, "test queue").await.unwrap_err();
        assert_eq!(error.kind(), io::ErrorKind::InvalidData);
        assert_eq!(sender.available_bytes(), 4);
        drop(receiver);
        let error = send_queued(&sender, 1, 4, "test queue").await.unwrap_err();
        assert_eq!(error.kind(), io::ErrorKind::BrokenPipe);
    });
}

#[test]
fn queued_send_times_out_without_publishing_or_leaking_byte_budget() {
    runtime().block_on(async {
        let (sender, mut receiver) = mailbox::bounded(1, 8).unwrap();
        send_queued(&sender, 1, 4, "test queue").await.unwrap();
        let error = send_queued(&sender, 2, 4, "test queue").await.unwrap_err();
        assert_eq!(error.kind(), io::ErrorKind::TimedOut);
        assert_eq!(sender.available_bytes(), 4);
        assert_eq!(receiver.recv().await, Some(1));
        assert!(matches!(
            receiver.try_recv(),
            Err(tokio::sync::mpsc::error::TryRecvError::Empty)
        ));
        assert_eq!(sender.available_bytes(), 8);
    });
}
