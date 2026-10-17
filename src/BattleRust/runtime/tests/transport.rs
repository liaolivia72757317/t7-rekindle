use std::io::ErrorKind;
use t7r_battle_core::protocol::plain_frame;
use t7r_battle_runtime::transport::split;
use tokio::io::AsyncWriteExt;
use tokio::net::{TcpListener, TcpStream};

fn run(future: impl std::future::Future<Output = ()>) {
    tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()
        .unwrap()
        .block_on(future);
}

#[test]
fn loopback_transport_handles_split_frames_and_clean_eof() {
    run(async {
        let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let mut client = TcpStream::connect(listener.local_addr().unwrap())
            .await
            .unwrap();
        let (server, _) = listener.accept().await.unwrap();
        let (mut reader, mut writer) = split(server).unwrap();
        let frame = plain_frame(4, 100, &[0, 1, 2]).unwrap();
        client.write_all(&frame[..7]).await.unwrap();
        client.write_all(&frame[7..]).await.unwrap();
        assert_eq!(reader.read_frame().await.unwrap(), Some(frame.clone()));
        writer.send(&frame).await.unwrap();
        let (mut remote, _) = split(client).unwrap();
        assert_eq!(remote.read_frame().await.unwrap(), Some(frame));
        assert_eq!(reader.read_frame().await.unwrap(), None);
    });
}

#[test]
fn truncated_eof_is_not_a_successful_disconnect() {
    run(async {
        let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let mut client = TcpStream::connect(listener.local_addr().unwrap())
            .await
            .unwrap();
        let (server, _) = listener.accept().await.unwrap();
        let (mut reader, _) = split(server).unwrap();
        client.write_all(&[0x55, 0x0e, 0]).await.unwrap();
        client.shutdown().await.unwrap();
        assert_eq!(
            reader.read_frame().await.unwrap_err().kind(),
            ErrorKind::UnexpectedEof
        );
    });
}
