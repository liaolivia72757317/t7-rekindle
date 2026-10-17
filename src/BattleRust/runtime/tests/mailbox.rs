use t7r_battle_runtime::mailbox::{QueueError, bounded};

fn run(future: impl std::future::Future<Output = ()>) {
    tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()
        .unwrap()
        .block_on(future);
}

#[test]
fn both_byte_and_item_budgets_apply_and_dequeue_releases_capacity() {
    run(async {
        let (send, mut receive) = bounded(2, 10).unwrap();
        send.try_send(1, 6).unwrap();
        assert!(matches!(send.try_send(2, 5), Err(QueueError::Full(2))));
        send.try_send(3, 4).unwrap();
        assert!(matches!(send.try_send(4, 0), Err(QueueError::Full(4))));
        assert_eq!(send.available_bytes(), 0);
        assert_eq!(receive.recv().await, Some(1));
        assert_eq!(send.available_bytes(), 6);
        send.try_send(5, 6).unwrap();
        assert_eq!(receive.recv().await, Some(3));
        assert_eq!(receive.recv().await, Some(5));
        assert_eq!(send.available_bytes(), 10);
    });
}

#[test]
fn pending_send_waits_for_byte_capacity_without_dropping_or_reordering_data() {
    run(async {
        let (send, mut receive) = bounded(10, 10).unwrap();
        send.send(1, 10).await.unwrap();
        let producer = tokio::spawn(async move { send.send(2, 10).await });
        tokio::task::yield_now().await;
        assert!(!producer.is_finished());
        assert_eq!(receive.recv().await, Some(1));
        producer.await.unwrap().unwrap();
        assert_eq!(receive.recv().await, Some(2));
        assert_eq!(receive.recv().await, None);
    });
}

#[test]
fn receiver_close_wakes_blocked_producers_and_returns_their_payload() {
    run(async {
        let (send, mut receive) = bounded(1, 10).unwrap();
        send.send(1, 10).await.unwrap();
        let second = send.clone();
        let producer = tokio::spawn(async move { second.send(2, 10).await });
        tokio::task::yield_now().await;
        receive.close();
        assert!(matches!(
            producer.await.unwrap(),
            Err(QueueError::Closed(2))
        ));
        assert_eq!(receive.recv().await, Some(1));
        assert_eq!(send.available_bytes(), 10);
        assert!(matches!(send.try_send(3, 10), Err(QueueError::Closed(3))));
    });
}

#[test]
fn oversized_payload_and_cancelled_sender_do_not_leak_budget() {
    run(async {
        let (send, mut receive) = bounded(1, 10).unwrap();
        assert!(matches!(
            send.send(1, 11).await,
            Err(QueueError::Oversized(1))
        ));
        send.send(2, 5).await.unwrap();
        let pending = send.clone();
        let producer = tokio::spawn(async move { pending.send(3, 5).await });
        tokio::task::yield_now().await;
        assert_eq!(send.available_bytes(), 0);
        producer.abort();
        assert!(producer.await.unwrap_err().is_cancelled());
        assert_eq!(send.available_bytes(), 5);
        assert_eq!(receive.recv().await, Some(2));
        assert_eq!(send.available_bytes(), 10);
    });
}
