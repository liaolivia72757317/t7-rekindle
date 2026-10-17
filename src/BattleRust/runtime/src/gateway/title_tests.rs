use super::*;

fn catalogue(equipped: u32) -> Vec<u8> {
    serde_json::to_vec(&serde_json::json!({
        "titles": [{"titleId":1007,"owned":true,"enabled":true}],
        "equippedTitleId": equipped,
    }))
    .unwrap()
}

#[test]
fn publication_confirms_pending_before_catalogue_and_rejects_stale_epoch() {
    let mut state = Titles::default();
    state.publish(1, &catalogue(0), 0, true).unwrap();
    let (id, mut events) = state.subscribe().unwrap();
    assert!(matches!(events.try_recv().unwrap(), Event::Catalogue(_)));
    state.equip(id, 1007).unwrap();
    assert!(matches!(events.try_recv().unwrap(), Event::Deadline(_, _)));
    let request = state.request();
    state
        .publish(1, &catalogue(1007), request.request_id, true)
        .unwrap();
    assert!(matches!(events.try_recv().unwrap(), Event::Result(true)));
    assert!(matches!(events.try_recv().unwrap(), Event::Catalogue(c) if c.equipped == 1007));
    assert_eq!(state.request(), TitleRequest::default());
    state.publish(2, &catalogue(0), 0, true).unwrap();
    state.publish(1, &catalogue(1007), 0, true).unwrap();
    assert_eq!(state.catalogue.equipped, 0);
}

#[test]
fn invalid_unowned_duplicate_pending_timeout_and_disconnect() {
    let mut state = Titles::default();
    state.publish(1, &catalogue(0), 0, true).unwrap();
    let (id, mut events) = state.subscribe().unwrap();
    events.try_recv().unwrap();
    state.equip(id, 999).unwrap();
    assert!(matches!(events.try_recv().unwrap(), Event::Result(false)));
    state.equip(id, 1007).unwrap();
    events.try_recv().unwrap();
    let request = state.request();
    state.equip(id, 0).unwrap();
    assert!(matches!(events.try_recv().unwrap(), Event::Result(false)));
    assert_eq!(state.request(), request);
    state.expire(request.request_id);
    assert!(matches!(events.try_recv().unwrap(), Event::Result(false)));
    assert_eq!(state.request(), TitleRequest::default());
    state.equip(id, 1007).unwrap();
    state.disconnect(id);
    assert_eq!(state.request(), TitleRequest::default());
    assert!(state.publish(1, &catalogue(999), 0, true).is_err());
    assert_eq!(state.catalogue.equipped, 0);
}

#[test]
fn epoch_change_rejects_pending_and_slow_subscriber_is_removed() {
    let mut state = Titles::default();
    state.publish(1, &catalogue(0), 0, true).unwrap();
    let (id, mut events) = state.subscribe().unwrap();
    events.try_recv().unwrap();
    state.equip(id, 1007).unwrap();
    events.try_recv().unwrap();
    state.publish(2, &catalogue(0), 0, true).unwrap();
    assert!(matches!(events.try_recv().unwrap(), Event::Result(false)));
    for _ in 0..33 {
        state.publish(2, &catalogue(0), 0, true).unwrap();
    }
    assert!(!state.clients.contains_key(&id));
}

#[test]
fn original_user_info_layout_and_unknown_user() {
    let c = parse(1, &catalogue(1007)).unwrap();
    let info = user_info(b"player", USER_ID, &c);
    assert_eq!(&info[..14], &[0, 2, 0, 0, 0, 0, 0, 0, 39, 16, 0, 0, 0, 7]);
    assert_eq!(&info[14..21], b"player\0");
    assert_eq!(&info[21 + 64..21 + 68], &1007_u32.to_be_bytes());
    let stats = stat_info(USER_ID, &c);
    assert_eq!(stats.len(), 10 + 296 + 4460 + 4 + 4 + 90);
    assert_eq!(&stats[4766..4774], &[0, 0, 0, 1, 0, 0, 3, 239]);
    let unknown = user_info(b"player", 1, &c);
    assert_eq!(&unknown[2..6], &[0, 0, 0, 1]);
    assert_eq!(unknown.len(), 15 + 296);
    assert_eq!(set_result(false), [0, 6, 0, 8, 178, 149]);
}
