use std::{env, io, net::SocketAddr, path::PathBuf, sync::Arc};
use t7_game_server::auth::Admission;

use t7_game_server::maps::MapCatalog;
use t7_game_server::transport::{GameServer, wait_for_shutdown};
use tracing_subscriber::EnvFilter;

#[tokio::main]
async fn main() -> io::Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(
            EnvFilter::try_from_default_env().unwrap_or_else(|_| EnvFilter::new("info")),
        )
        .init();

    let mut address: SocketAddr = "127.0.0.1:8090".parse().expect("literal socket address");
    let mut dev_lobby = false;
    let mut rules = t7_game_server::limits::RoomRules::default();
    let mut guest_lobby = false;
    let mut certificate = None;
    let mut private_key = None;
    let mut maps = MapCatalog::default();
    let mut maps_configured = false;
    let mut args = env::args().skip(1);
    while let Some(arg) = args.next() {
        match arg.as_str() {
            "--listen" => {
                address = args
                    .next()
                    .ok_or_else(|| io::Error::other("--listen requires IP:PORT"))?
                    .parse()
                    .map_err(|_| io::Error::other("invalid listen address"))?;
            }
            "--dev-lobby" => dev_lobby = true,
            "--room-capacity" => {
                let capacity = args
                    .next()
                    .ok_or_else(|| io::Error::other("--room-capacity requires NUMBER"))?
                    .parse()
                    .map_err(|_| io::Error::other("invalid room capacity"))?;
                rules =
                    t7_game_server::limits::RoomRules::new(capacity).map_err(io::Error::other)?;
            }
            "--guest-lobby" => guest_lobby = true,
            "--tls-cert" => {
                certificate =
                    Some(PathBuf::from(args.next().ok_or_else(|| {
                        io::Error::other("--tls-cert requires FILE")
                    })?))
            }
            "--tls-key" => {
                private_key =
                    Some(PathBuf::from(args.next().ok_or_else(|| {
                        io::Error::other("--tls-key requires FILE")
                    })?))
            }
            "--lobby-maps" => {
                if maps_configured {
                    return Err(io::Error::other("--lobby-maps must be specified once"));
                }
                let path = args
                    .next()
                    .ok_or_else(|| io::Error::other("--lobby-maps requires a JSON file"))?;
                let mut data = Vec::new();
                use std::io::Read;
                std::fs::File::open(path)?
                    .take(32769)
                    .read_to_end(&mut data)?;
                maps = MapCatalog::from_json(&data).map_err(io::Error::other)?;
                maps_configured = true;
            }
            "--help" | "-h" => {
                println!(
                    "t7-game-server [--listen IP:PORT] [--dev-lobby] [--lobby-maps JSON_FILE] [--room-capacity NUMBER] [--guest-lobby --tls-cert PEM --tls-key PEM]"
                );
                return Ok(());
            }
            _ => return Err(io::Error::other("unknown argument; use --help")),
        }
    }
    if dev_lobby && guest_lobby {
        return Err(io::Error::other(
            "development and guest admission are mutually exclusive",
        ));
    }
    if certificate.is_some() != private_key.is_some() || guest_lobby != certificate.is_some() {
        return Err(io::Error::other(
            "guest admission requires --guest-lobby, --tls-cert and --tls-key together",
        ));
    }
    if !address.ip().is_loopback() && !guest_lobby {
        return Err(io::Error::other(
            "public listeners require authenticated TLS admission",
        ));
    }
    let admission = guest_lobby.then(|| Arc::new(Admission::default()));
    let server = GameServer::with_configuration(dev_lobby, maps, admission, rules);
    let shutdown = server.shutdown_receiver();
    let shutdown_handle = server.shutdown_handle();
    tracing::info!(%address, dev_lobby, "game service started");
    let stop = async move {
        tokio::select! {
            signal = tokio::signal::ctrl_c() => {
                if let Err(error) = signal { tracing::error!(%error, "shutdown signal unavailable"); }
                shutdown_handle.send_replace(true);
            }
            () = wait_for_shutdown(shutdown) => {}
        }
    };
    let result = if let (Some(certificate), Some(private_key)) = (certificate, private_key) {
        let config =
            axum_server::tls_rustls::RustlsConfig::from_pem_file(&certificate, &private_key)
                .await?;
        let reload_config = config.clone();
        let reload_certificate = certificate.clone();
        let reload_key = private_key.clone();
        let reload = tokio::spawn(async move {
            let mut previous = std::fs::metadata(&reload_certificate)
                .and_then(|value| value.modified())
                .ok();
            let mut interval = tokio::time::interval(std::time::Duration::from_secs(60));
            interval.tick().await;
            loop {
                interval.tick().await;
                let current = std::fs::metadata(&reload_certificate)
                    .and_then(|value| value.modified())
                    .ok();
                if current != previous && current.is_some() {
                    match reload_config
                        .reload_from_pem_file(&reload_certificate, &reload_key)
                        .await
                    {
                        Ok(()) => {
                            previous = current;
                            tracing::info!("TLS certificate reloaded");
                        }
                        Err(_) => tracing::warn!(
                            "TLS certificate reload failed; retaining current certificate"
                        ),
                    }
                }
            }
        });
        let handle = axum_server::Handle::new();
        let shutdown_handle = handle.clone();
        let signal = tokio::spawn(async move {
            stop.await;
            shutdown_handle.graceful_shutdown(Some(std::time::Duration::from_secs(5)));
        });
        // Send small combat events immediately instead of waiting for TCP acknowledgements.
        let acceptor = axum_server::tls_rustls::RustlsAcceptor::new(config)
            .acceptor(axum_server::accept::NoDelayAcceptor::new());
        let result = axum_server::bind(address)
            .acceptor(acceptor)
            .handle(handle)
            .serve(
                server
                    .router()
                    .into_make_service_with_connect_info::<SocketAddr>(),
            )
            .await;
        signal.abort();
        reload.abort();
        result
    } else {
        use axum::serve::ListenerExt;
        let listener = tokio::net::TcpListener::bind(address)
            .await?
            .tap_io(|stream| {
                if let Err(error) = stream.set_nodelay(true) {
                    tracing::warn!(%error, "could not enable TCP_NODELAY");
                }
            });
        axum::serve(
            listener,
            server
                .router()
                .into_make_service_with_connect_info::<SocketAddr>(),
        )
        .with_graceful_shutdown(stop)
        .await
    };
    server.shutdown();
    server.stopped().await?;
    result
}
