use std::time::Duration;

use futures_util::{SinkExt, StreamExt};
use serde_json::{Value, json};
use t7_game_server::maps::MapCatalog;
use t7_game_server::transport::{GameServer, wait_for_shutdown};
use tokio::{
    io::{AsyncReadExt, AsyncWriteExt},
    net::{TcpListener, TcpStream},
    task::JoinHandle,
};
use tokio_tungstenite::{
    MaybeTlsStream, WebSocketStream, connect_async,
    tungstenite::{Error, Message},
};

type Client = WebSocketStream<MaybeTlsStream<TcpStream>>;

#[tokio::test]
async fn melee_events_reach_both_members_once_with_server_owned_actor_identity() {
    let server = TestServer::start(true).await;
    let mut a = server.connect().await;
    let mut b = server.connect().await;
    request(
        &mut a,
        1,
        json!({"type":"hello","payload":{"accountId":"action-owner"}}),
    )
    .await;
    request(
        &mut b,
        1,
        json!({"type":"hello","payload":{"accountId":"action-guest"}}),
    )
    .await;
    let room = request(
        &mut a,
        2,
        json!({"type":"createRoom","payload":{"name":"room","mapId":1003}}),
    )
    .await;
    request(
        &mut b,
        2,
        json!({"type":"joinRoom","payload":{"roomId":room["payload"]["room"]["roomId"]}}),
    )
    .await;
    request(&mut b, 3, json!({"type":"ready","payload":{"ready":true}})).await;
    let loading = request(&mut a, 3, json!({"type":"start"})).await;
    let id = loading["payload"]["room"]["currentMatch"]["matchId"].clone();
    let attack =
        json!({"type":"actionInput","payload":{"matchId":id,"inputSeq":1,"intent":"attackUp"}});
    assert_eq!(
        request(&mut a, 4, attack.clone()).await["payload"]["code"],
        "wrongPhase"
    );
    request(&mut a, 5, json!({"type":"loaded","payload":{"matchId":id}})).await;
    request(&mut b, 4, json!({"type":"loaded","payload":{"matchId":id}})).await;
    room_phase(&mut a, "running").await;
    room_phase(&mut b, "running").await;
    let accepted = request(&mut a, 6, attack.clone()).await;
    assert_eq!(accepted["type"], "actionAccepted");
    let local = receive(&mut a).await;
    let remote = receive(&mut b).await;
    assert_eq!(local, remote);
    assert_eq!(local["event"]["type"], "actionChanged");
    let action = &local["event"]["payload"]["action"];
    assert_eq!(action["actorId"], 1);
    assert_eq!(action["state"], 235);
    assert_eq!(action["eventSeq"], 1);
    assert_eq!(request(&mut a, 6, attack.clone()).await, accepted);
    assert_eq!(
        request(&mut a, 7, attack).await["payload"]["code"],
        "staleInput"
    );
    assert!(
        tokio::time::timeout(Duration::from_millis(80), b.next())
            .await
            .is_err()
    );
    let block =
        json!({"type":"actionInput","payload":{"matchId":id,"inputSeq":1,"intent":"blockRight"}});
    request(&mut b, 5, block).await;
    let remote = receive(&mut a).await;
    assert_eq!(remote["event"]["payload"]["action"]["actorId"], 2);
    assert_eq!(remote["event"]["payload"]["action"]["state"], 308);
    request(&mut a, 8, json!({"type":"leaveRoom"})).await;
    room_phase(&mut b, "waiting").await;
    request(&mut b, 6, json!({"type":"leaveRoom"})).await;
    a.close(None).await.unwrap();
    b.close(None).await.unwrap();
    server.stop().await;
}

#[tokio::test]
async fn solo_owner_can_start_without_ready_and_confirm_its_own_loading() {
    let server = TestServer::start(true).await;
    let mut client = server.connect().await;
    request(
        &mut client,
        1,
        json!({"type":"hello", "payload":{"accountId":"solo-owner"}}),
    )
    .await;
    request(
        &mut client,
        2,
        json!({"type":"createRoom", "payload":{"name":"solo", "mapId":1028}}),
    )
    .await;
    let loading = request(&mut client, 3, json!({"type":"start"})).await;
    assert_eq!(loading["payload"]["room"]["phase"], "loading");
    assert_eq!(loading["payload"]["room"]["members"][0]["ready"], false);
    let match_id = loading["payload"]["room"]["currentMatch"]["matchId"].clone();
    let view = request(
        &mut client,
        4,
        json!({"type":"getClientView", "payload":{"matchId":match_id}}),
    )
    .await;
    let actors = view["payload"]["view"]["actors"].as_array().unwrap();
    assert_eq!(actors.len(), 1);
    assert_eq!(actors[0]["localRid"], 1);
    let countdown = request(
        &mut client,
        5,
        json!({"type":"loaded", "payload":{"matchId":match_id}}),
    )
    .await;
    assert_eq!(countdown["payload"]["room"]["phase"], "countdown");
    request(&mut client, 6, json!({"type":"leaveRoom"})).await;
    let listed = request(&mut client, 7, json!({"type":"listRooms"})).await;
    assert!(listed["payload"]["rooms"].as_array().unwrap().is_empty());
    client.close(None).await.unwrap();
    server.stop().await;
}

#[tokio::test]
async fn configured_catalog_reaches_the_room_service_and_keeps_map_numbers() {
    let maps = MapCatalog::from_json(br#"{"mapIds":[7001,7002]}"#).unwrap();
    let server = TestServer::start_with_maps(true, maps).await;
    let mut a = server.connect().await;
    request(
        &mut a,
        1,
        json!({"type":"hello", "payload":{"accountId":"map-owner"}}),
    )
    .await;
    let created = request(
        &mut a,
        2,
        json!({"type":"createCustomRoom", "payload": {
        "mapId":7002, "name":"room", "channelId":1}}),
    )
    .await;
    assert_eq!(created["type"], "room");
    assert_eq!(created["payload"]["room"]["mapId"], 7002);
    let page = request(
        &mut a,
        3,
        json!({"type":"listRoomPage", "payload": {
        "channelId":1,"start":0,"limit":20}}),
    )
    .await;
    assert_eq!(page["payload"]["page"]["rooms"][0]["mapId"], 7002);
    request(&mut a, 4, json!({"type":"leaveRoom"})).await;
    let rejected = request(
        &mut a,
        5,
        json!({"type":"createCustomRoom", "payload": {
        "mapId":7003, "name":"room", "channelId":1}}),
    )
    .await;
    assert_eq!(rejected["payload"]["code"], "unsupportedMap");
    a.close(None).await.unwrap();
    server.stop().await;
}

#[tokio::test]
async fn gateway_player_ids_survive_room_changes_and_reidentification() {
    let server = TestServer::start(true).await;
    let mut a = server.connect().await;
    let mut b = server.connect().await;
    let hello = |account: &str, name: &str| {
        json!({"type":"gatewayHello", "payload": {
        "accountId": account, "displayName": name}})
    };
    let welcome_a = request(&mut a, 1, hello("gateway-a", "玩家甲")).await;
    let welcome_b = request(&mut b, 1, hello("gateway-b", "玩家乙")).await;
    let user_a = welcome_a["payload"]["userId"].as_u64().unwrap();
    let user_b = welcome_b["payload"]["userId"].as_u64().unwrap();
    assert_ne!(user_a, user_b);
    let created = request(
        &mut a,
        2,
        json!({"type":"createCustomRoom", "payload": {
        "name":"room", "mapId":1028, "channelId":1}}),
    )
    .await;
    let guid = created["payload"]["room"]["roomGuid"].clone();
    let join = json!({"type":"joinCustomRoom", "payload": {"roomGuid":guid}});
    let joined = request(&mut b, 2, join.clone()).await;
    let members = joined["payload"]["room"]["members"].as_array().unwrap();
    assert_eq!(members[0]["userId"], user_a);
    assert_eq!(members[1]["userId"], user_b);
    assert_eq!(members[1]["displayName"], "玩家乙");
    // Drain the join notification before using the next update as a barrier.
    let _ = room_phase(&mut b, "waiting").await;
    request(&mut a, 3, json!({"type":"leaveRoom"})).await;
    let moved = room_phase(&mut b, "waiting").await;
    assert_eq!(moved["ownerAccountId"], "gateway-b");
    assert_eq!(moved["members"][0]["userId"], user_b);
    let rejoined = request(&mut a, 4, join).await;
    let member = rejoined["payload"]["room"]["members"]
        .as_array()
        .unwrap()
        .iter()
        .find(|member| member["accountId"] == "gateway-a")
        .unwrap();
    assert_eq!(member["userId"], user_a);
    assert_eq!(member["actorId"], 3);
    let _ = room_phase(&mut b, "waiting").await;
    a.close(None).await.unwrap();
    let _ = room_phase(&mut b, "waiting").await;
    let mut again = server.connect().await;
    let changed_name = request(&mut again, 1, hello("gateway-a", "different")).await;
    assert_eq!(changed_name["payload"]["code"], "invalidDisplayName");
    let identified = request(&mut again, 2, hello("gateway-a", "玩家甲")).await;
    assert_eq!(identified["payload"]["userId"], user_a);
    again.close(None).await.unwrap();
    b.close(None).await.unwrap();
    server.stop().await;
}

struct TestServer {
    service: GameServer,
    address: std::net::SocketAddr,
    task: JoinHandle<std::io::Result<()>>,
}

impl TestServer {
    async fn start(dev_lobby: bool) -> Self {
        Self::start_with_maps(dev_lobby, MapCatalog::default()).await
    }

    async fn start_with_maps(dev_lobby: bool, maps: MapCatalog) -> Self {
        Self::start_service(GameServer::with_maps(dev_lobby, maps)).await
    }

    async fn start_service(service: GameServer) -> Self {
        let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let address = listener.local_addr().unwrap();
        let router = service.router();
        let shutdown = service.shutdown_receiver();
        let task = tokio::spawn(async move {
            axum::serve(
                listener,
                router.into_make_service_with_connect_info::<std::net::SocketAddr>(),
            )
            .with_graceful_shutdown(wait_for_shutdown(shutdown))
            .await
        });
        Self {
            service,
            address,
            task,
        }
    }

    async fn connect(&self) -> Client {
        connect_async(format!("ws://{}/ws", self.address))
            .await
            .unwrap()
            .0
    }

    async fn stop(self) {
        self.service.shutdown();
        tokio::time::timeout(Duration::from_secs(5), self.task)
            .await
            .unwrap()
            .unwrap()
            .unwrap();
        self.service.stopped().await.unwrap();
    }
}

async fn receive(client: &mut Client) -> Value {
    loop {
        let message = tokio::time::timeout(Duration::from_secs(5), client.next())
            .await
            .unwrap()
            .unwrap()
            .unwrap();
        if let Message::Text(text) = message {
            return serde_json::from_str(&text).unwrap();
        }
    }
}

async fn request(client: &mut Client, id: u64, command: Value) -> Value {
    let request_id = id.to_string();
    client
        .send(Message::Text(
            json!({
                "protocolVersion": 1, "requestId": id.to_string(), "command": command
            })
            .to_string()
            .into(),
        ))
        .await
        .unwrap();
    loop {
        let response = receive(client).await;
        if response["requestId"].as_str() == Some(request_id.as_str()) {
            return response["event"].clone();
        }
    }
}

async fn room_phase(client: &mut Client, phase: &str) -> Value {
    loop {
        let message = receive(client).await;
        if message["event"]["type"] == "roomChanged"
            && message["event"]["payload"]["room"]["phase"] == phase
        {
            return message["event"]["payload"]["room"].clone();
        }
    }
}

async fn get(server: &TestServer, path: &str) -> String {
    let mut http = TcpStream::connect(server.address).await.unwrap();
    http.write_all(
        format!(
            "GET {path} HTTP/1.1\r\nHost: {}\r\nConnection: close\r\n\r\n",
            server.address
        )
        .as_bytes(),
    )
    .await
    .unwrap();
    let mut response = String::new();
    tokio::time::timeout(Duration::from_secs(5), http.read_to_string(&mut response))
        .await
        .unwrap()
        .unwrap();
    response
}

#[tokio::test]
async fn custom_directory_supports_discovery_join_replay_and_cleanup_on_one_connection() {
    let server = TestServer::start(true).await;
    let mut a = server.connect().await;
    let mut b = server.connect().await;
    for (client, account) in [(&mut a, "player-a"), (&mut b, "player-b")] {
        request(
            client,
            1,
            json!({"type":"hello","payload":{"accountId":account}}),
        )
        .await;
    }
    let create =
        json!({"type":"createCustomRoom","payload":{"name":"custom","mapId":1028,"channelId":1}});
    let created = request(&mut a, 2, create.clone()).await;
    assert_eq!(created["type"], "room");
    let room = &created["payload"]["room"];
    let guid = room["roomGuid"].as_str().unwrap();
    let uuid = room["roomId"].as_str().unwrap();
    assert_eq!(room["phase"], "waiting");
    assert!(room["currentMatch"].is_null());
    assert_eq!(request(&mut a, 2, create).await, created);
    let channels = request(&mut b, 2, json!({"type":"listChannels"})).await;
    assert_eq!(channels["payload"]["channels"].as_array().unwrap().len(), 1);
    assert_eq!(channels["payload"]["channels"][0]["channelId"], 1);
    let page_query = json!({"type":"listRoomPage","payload":{"channelId":1,"start":0,"limit":20}});
    let page = request(&mut b, 3, page_query.clone()).await;
    assert_eq!(page["type"], "roomPage");
    assert_eq!(page["payload"]["page"]["total"], 1);
    let listed = &page["payload"]["page"]["rooms"][0];
    assert_eq!(listed["roomGuid"], guid);
    assert_eq!(listed["roomId"], uuid);
    assert!(listed.get("members").is_none());
    let join = json!({"type":"joinCustomRoom","payload":{"roomGuid":guid}});
    let joined = request(&mut b, 4, join.clone()).await;
    assert_eq!(joined["payload"]["room"]["roomId"], uuid);
    assert_eq!(joined["payload"]["room"]["roomGuid"], guid);
    assert_eq!(
        joined["payload"]["room"]["members"]
            .as_array()
            .unwrap()
            .len(),
        2
    );
    assert_eq!(request(&mut b, 4, join.clone()).await, joined);
    request(&mut a, 3, json!({"type":"ready","payload":{"ready":true}})).await;
    request(&mut b, 5, json!({"type":"ready","payload":{"ready":true}})).await;
    let loading = request(&mut a, 4, json!({"type":"start"})).await;
    assert_eq!(loading["payload"]["room"]["phase"], "loading");
    assert_eq!(loading["payload"]["room"]["roomGuid"], guid);
    let page = request(&mut b, 6, page_query.clone()).await;
    assert_eq!(page["payload"]["page"]["rooms"][0]["phase"], "loading");
    assert_eq!(page["payload"]["page"]["rooms"][0]["joinable"], false);
    request(&mut b, 7, json!({"type":"leaveRoom"})).await;
    request(&mut a, 5, json!({"type":"leaveRoom"})).await;
    assert_eq!(
        request(&mut b, 8, join).await["payload"]["code"],
        "roomNotFound"
    );
    assert_eq!(
        request(&mut b, 9, page_query.clone()).await["payload"]["page"]["total"],
        0
    );
    let replacement = request(
        &mut a,
        6,
        json!({"type":"createRoom","payload":{"name":"dev","mapId":1028}}),
    )
    .await;
    assert_ne!(replacement["payload"]["room"]["roomGuid"], guid);
    let page = request(&mut b, 10, page_query).await;
    assert_eq!(
        page["payload"]["page"]["rooms"][0]["roomId"],
        replacement["payload"]["room"]["roomId"]
    );
    let queried = request(&mut b, 11, json!({"type":"getRoomListing","payload":{"channelId":1,"roomNumber":replacement["payload"]["room"]["roomNumber"]}})).await;
    assert_eq!(queried["type"], "roomListing");
    assert_eq!(
        queried["payload"]["room"],
        page["payload"]["page"]["rooms"][0]
    );
    assert_eq!(request(&mut b, 12, json!({"type":"getRoomListing","payload":{"channelId":1,"roomNumber":room["roomNumber"]}})).await["payload"]["code"], "roomNotFound");
    assert_eq!(request(&mut b, 13, json!({"type":"getRoomListing","payload":{"channelId":2,"roomNumber":replacement["payload"]["room"]["roomNumber"]}})).await["payload"]["code"], "unsupportedChannel");
    server.stop().await;
}

#[tokio::test]
async fn custom_directory_uses_normal_admission_and_rejects_unsupported_options() {
    let server = TestServer::start(true).await;
    let mut client = server.connect().await;
    for (i, command) in [
        json!({"type":"listChannels"}),
        json!({"type":"listRoomPage","payload":{"channelId":1,"start":0,"limit":1}}),
        json!({"type":"createCustomRoom","payload":{"name":"room","mapId":1028,"channelId":1}}),
        json!({"type":"joinCustomRoom","payload":{"roomGuid":"1"}}),
    ]
    .into_iter()
    .enumerate()
    {
        assert_eq!(
            request(&mut client, i as u64 + 1, command).await["payload"]["code"],
            "helloRequired"
        );
    }
    request(
        &mut client,
        5,
        json!({"type":"hello","payload":{"accountId":"player-a"}}),
    )
    .await;
    assert_eq!(
        request(
            &mut client,
            6,
            json!({"type":"createCustomRoom","payload":{"name":"room","mapId":1028,"channelId":2}})
        )
        .await["payload"]["code"],
        "unsupportedChannel"
    );
    assert_eq!(request(&mut client, 7, json!({"type":"createCustomRoom","payload":{"name":"room","mapId":1028,"channelId":1,"password":"secret"}})).await["payload"]["code"], "passwordUnsupported");
    assert_eq!(
        request(
            &mut client,
            8,
            json!({"type":"listRoomPage","payload":{"channelId":1,"start":0,"limit":0}})
        )
        .await["payload"]["code"],
        "invalidRoomPage"
    );
    let created = request(
        &mut client,
        9,
        json!({"type":"createCustomRoom","payload":{"name":"room","mapId":1028,"channelId":1}}),
    )
    .await;
    assert_eq!(created["payload"]["room"]["roomNumber"], 1);
    assert_eq!(
        created["payload"]["room"]["members"]
            .as_array()
            .unwrap()
            .len(),
        1
    );
    server.stop().await;
}

#[tokio::test]
async fn client_views_follow_identified_connection_membership_and_match() {
    let server = TestServer::start(true).await;
    let mut a = server.connect().await;
    let mut b = server.connect().await;
    for (client, account) in [(&mut a, "player-a"), (&mut b, "player-b")] {
        request(
            client,
            1,
            json!({"type":"hello","payload":{"accountId":account}}),
        )
        .await;
    }
    let created = request(
        &mut a,
        2,
        json!({"type":"createRoom","payload":{"name":"room","mapId":1028}}),
    )
    .await;
    let id = created["payload"]["room"]["roomId"].as_str().unwrap();
    assert_eq!(
        request(
            &mut a,
            3,
            json!({"type":"getClientView","payload":{"matchId":"none"}})
        )
        .await["payload"]["code"],
        "wrongPhase"
    );
    request(
        &mut b,
        2,
        json!({"type":"joinRoom","payload":{"roomId":id}}),
    )
    .await;
    request(&mut a, 4, json!({"type":"ready","payload":{"ready":true}})).await;
    request(&mut b, 3, json!({"type":"ready","payload":{"ready":true}})).await;
    let loading = request(&mut a, 5, json!({"type":"start"})).await;
    let match_id = loading["payload"]["room"]["currentMatch"]["matchId"]
        .as_str()
        .unwrap();
    let query = json!({"type":"getClientView","payload":{"matchId":match_id}});
    let a_view = request(&mut a, 6, query.clone()).await;
    let b_view = request(&mut b, 4, query.clone()).await;
    assert_eq!(a_view["type"], "clientView");
    assert_eq!(a_view["payload"]["view"]["localActorId"], 1);
    assert_eq!(b_view["payload"]["view"]["localActorId"], 2);
    assert_eq!(a_view["payload"]["view"]["actors"][0]["localRid"], 1);
    assert_eq!(b_view["payload"]["view"]["actors"][0]["localRid"], 1);
    assert_eq!(b_view["payload"]["view"]["actors"][1]["actorId"], 1);
    assert_eq!(
        request(
            &mut b,
            5,
            json!({"type":"getClientView","payload":{"matchId":"other"}})
        )
        .await["payload"]["code"],
        "staleMatch"
    );
    let mut outsider = server.connect().await;
    request(
        &mut outsider,
        1,
        json!({"type":"hello","payload":{"accountId":"outsider"}}),
    )
    .await;
    assert_eq!(
        request(&mut outsider, 2, query.clone()).await["payload"]["code"],
        "notInRoom"
    );
    request(&mut b, 6, json!({"type":"leaveRoom"})).await;
    assert_eq!(
        request(&mut a, 7, query.clone()).await["payload"]["code"],
        "wrongPhase"
    );
    assert_eq!(
        request(&mut b, 7, query).await["payload"]["code"],
        "notInRoom"
    );
    server.stop().await;
}

#[tokio::test]
async fn development_page_is_opt_in_and_browser_origins_are_checked() {
    use tokio_tungstenite::tungstenite::client::IntoClientRequest;
    let disabled = TestServer::start(false).await;
    for path in ["/dev", "/dev/lobby.js", "/dev/lobby.css"] {
        assert!(get(&disabled, path).await.starts_with("HTTP/1.1 404"));
    }
    disabled.stop().await;

    let enabled = TestServer::start(true).await;
    let page = get(&enabled, "/dev").await;
    assert!(page.starts_with("HTTP/1.1 200"));
    assert!(page.contains("content-security-policy:"));
    assert!(page.contains("cache-control: no-store"));
    assert!(page.contains("text/html; charset=utf-8"));
    for (path, mime) in [
        ("/dev/lobby.js", "text/javascript"),
        ("/dev/lobby.css", "text/css"),
    ] {
        let asset = get(&enabled, path).await;
        assert!(asset.starts_with("HTTP/1.1 200"));
        assert!(asset.contains(mime));
    }
    let mut allowed = format!("ws://{}/ws", enabled.address)
        .into_client_request()
        .unwrap();
    allowed.headers_mut().insert(
        "origin",
        format!("http://{}", enabled.address).parse().unwrap(),
    );
    let (mut socket, _) = connect_async(allowed).await.unwrap();
    socket.close(None).await.unwrap();
    for origin in ["https://example.invalid", "null"] {
        let mut denied = format!("ws://{}/ws", enabled.address)
            .into_client_request()
            .unwrap();
        denied
            .headers_mut()
            .insert("origin", origin.parse().unwrap());
        assert!(
            matches!(connect_async(denied).await, Err(Error::Http(response)) if response.status().as_u16() == 403)
        );
    }
    enabled.stop().await;
}

#[tokio::test]
async fn two_connections_share_a_room_and_disconnect_aborts_the_match() {
    let server = TestServer::start(true).await;
    let mut a = server.connect().await;
    let mut b = server.connect().await;
    assert_eq!(
        request(
            &mut a,
            1,
            json!({"type":"hello","payload":{"accountId":"player-a"}})
        )
        .await["type"],
        "welcome"
    );
    assert_eq!(
        request(
            &mut b,
            1,
            json!({"type":"hello","payload":{"accountId":"player-b"}})
        )
        .await["type"],
        "welcome"
    );
    let create = json!({"type":"createRoom","payload":{"name":"测试房间","mapId":1028}});
    let original = request(&mut a, 2, create.clone()).await;
    let id = original["payload"]["room"]["roomId"]
        .as_str()
        .unwrap()
        .to_owned();
    let joined = request(
        &mut b,
        2,
        json!({"type":"joinRoom","payload":{"roomId":id}}),
    )
    .await;
    assert_eq!(
        joined["payload"]["room"]["members"]
            .as_array()
            .unwrap()
            .len(),
        2
    );
    assert_eq!(request(&mut a, 2, create).await, original);
    assert_eq!(
        request(&mut a, 2, json!({"type":"leaveRoom"})).await["payload"]["code"],
        "requestConflict"
    );
    let list = request(&mut a, 3, json!({"type":"listRooms"})).await;
    assert_eq!(list["payload"]["rooms"].as_array().unwrap().len(), 1);
    assert_eq!(
        request(&mut a, 4, json!({"type":"start"})).await["payload"]["code"],
        "notReady"
    );
    assert_eq!(
        request(&mut b, 3, json!({"type":"start"})).await["payload"]["code"],
        "notOwner"
    );
    request(&mut a, 5, json!({"type":"ready","payload":{"ready":true}})).await;
    request(&mut b, 4, json!({"type":"ready","payload":{"ready":true}})).await;
    let loading = request(&mut a, 6, json!({"type":"start"})).await;
    let match_id = loading["payload"]["room"]["currentMatch"]["matchId"]
        .as_str()
        .unwrap();
    assert_eq!(loading["payload"]["room"]["phase"], "loading");
    assert_eq!(
        request(&mut b, 5, json!({"type":"ready","payload":{"ready":false}})).await["payload"]["code"],
        "wrongPhase"
    );
    request(
        &mut a,
        7,
        json!({"type":"loaded","payload":{"matchId":match_id}}),
    )
    .await;
    let countdown = request(
        &mut b,
        6,
        json!({"type":"loaded","payload":{"matchId":match_id}}),
    )
    .await;
    assert_eq!(countdown["payload"]["room"]["phase"], "countdown");
    room_phase(&mut a, "countdown").await;
    b.close(None).await.unwrap();
    let waiting = room_phase(&mut a, "waiting").await;
    assert_eq!(waiting["lastAbortReason"], "disconnected");
    assert_eq!(waiting["members"].as_array().unwrap().len(), 1);
    assert!(waiting["currentMatch"].is_null());
    let mut replacement = server.connect().await;
    assert_eq!(
        request(
            &mut replacement,
            1,
            json!({"type":"hello","payload":{"accountId":"player-b"}})
        )
        .await["type"],
        "welcome"
    );
    let rejoined = request(
        &mut replacement,
        2,
        json!({"type":"joinRoom","payload":{"roomId":id}}),
    )
    .await;
    assert!(
        rejoined["payload"]["room"]["members"][1]["actorId"]
            .as_u64()
            .unwrap()
            > 2
    );
    server.stop().await;
}

#[tokio::test]
async fn admission_requires_hello_and_enforces_version_identity_and_replay_limits() {
    let server = TestServer::start(true).await;
    let mut a = server.connect().await;
    assert_eq!(
        request(&mut a, 1, json!({"type":"listRooms"})).await["payload"]["code"],
        "helloRequired"
    );
    a.send(Message::Text(json!({"protocolVersion":2,"requestId":"2","command":{"type":"hello","payload":{"accountId":"player-a"}}}).to_string().into())).await.unwrap();
    assert_eq!(
        receive(&mut a).await["event"]["payload"]["code"],
        "unsupportedVersion"
    );
    request(
        &mut a,
        3,
        json!({"type":"hello","payload":{"accountId":"player-a"}}),
    )
    .await;
    let mut b = server.connect().await;
    assert_eq!(
        request(
            &mut b,
            1,
            json!({"type":"hello","payload":{"accountId":"player-a"}})
        )
        .await["payload"]["code"],
        "accountConnected"
    );
    for id in 4..38 {
        assert_eq!(
            request(&mut a, id, json!({"type":"ping"})).await["type"],
            "pong"
        );
    }
    assert_eq!(
        request(
            &mut a,
            3,
            json!({"type":"hello","payload":{"accountId":"player-a"}})
        )
        .await["payload"]["code"],
        "staleRequest"
    );
    a.send(Message::Text("{invalid json".into())).await.unwrap();
    assert_eq!(
        receive(&mut a).await["event"]["payload"]["code"],
        "badRequest"
    );
    server.stop().await;
}

#[tokio::test]
async fn default_mode_has_health_but_rejects_development_admission() {
    let server = TestServer::start(false).await;
    let mut http = TcpStream::connect(server.address).await.unwrap();
    http.write_all(b"GET /ready HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n")
        .await
        .unwrap();
    let mut response = String::new();
    tokio::time::timeout(Duration::from_secs(5), http.read_to_string(&mut response))
        .await
        .unwrap()
        .unwrap();
    assert!(response.starts_with("HTTP/1.1 200"));
    assert!(response.contains("\"productionAdmission\":false"));
    assert!(response.contains("healthOnly"));
    let result = connect_async(format!("ws://{}/ws", server.address)).await;
    assert!(matches!(result, Err(Error::Http(response)) if response.status().as_u16() == 503));
    server.stop().await;
}

#[tokio::test]
async fn guest_admission_rejects_missing_replayed_and_wrong_identity_tickets() {
    use t7_game_server::auth::Admission;
    use tokio_tungstenite::tungstenite::client::IntoClientRequest;
    let admission = Admission::default();
    let server = TestServer::start_service(GameServer::with_admission(
        false,
        MapCatalog::default(),
        Some(std::sync::Arc::new(admission)),
    ))
    .await;
    let missing = connect_async(format!("ws://{}/ws", server.address))
        .await
        .unwrap_err();
    assert!(matches!(missing, Error::Http(response) if response.status() == 401));
    async fn login(server: &TestServer, name: &str) -> (String, String) {
        let body = json!({"displayName":name}).to_string();
        let mut stream = TcpStream::connect(server.address).await.unwrap();
        stream.write_all(format!("POST /auth/guest HTTP/1.1\r\nHost: {}\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}", server.address, body.len(), body).as_bytes()).await.unwrap();
        let mut response = String::new();
        stream.read_to_string(&mut response).await.unwrap();
        let (headers, body) = response.split_once("\r\n\r\n").unwrap();
        (headers.to_owned(), body.to_owned())
    }
    assert!(login(&server, "").await.0.contains("400"));
    let (headers, body) = login(&server, "Player").await;
    assert!(headers.starts_with("HTTP/1.1 200"));
    assert!(headers.to_lowercase().contains("cache-control: no-store"));
    let ticket: Value = serde_json::from_str(&body).unwrap();
    let mut upgrade = format!("ws://{}/ws", server.address)
        .into_client_request()
        .unwrap();
    upgrade.headers_mut().insert(
        "Authorization",
        format!("Bearer {}", ticket["ticket"].as_str().unwrap())
            .parse()
            .unwrap(),
    );
    let (mut client, _) = connect_async(upgrade.clone()).await.unwrap();
    assert_eq!(
        request(
            &mut client,
            1,
            json!({"type":"hello", "payload":{"accountId":"someone-else"}})
        )
        .await["payload"]["code"],
        "invalidAccountId"
    );
    assert_eq!(request(&mut client, 2, json!({"type":"gatewayHello", "payload":{"accountId":ticket["accountId"], "displayName":"Player"}})).await["type"], "welcome");
    assert_eq!(
        request(
            &mut client,
            3,
            json!({"type":"createRoom", "payload":{"name":"room", "mapId":1003}})
        )
        .await["type"],
        "room"
    );
    assert!(
        matches!(connect_async(upgrade).await.unwrap_err(), Error::Http(response) if response.status() == 401)
    );
    client.close(None).await.unwrap();
    server.stop().await;
}
