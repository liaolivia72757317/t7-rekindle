#[test]
fn provider_supports_windows_tls12_and_tls13() {
    let provider = rustls::crypto::aws_lc_rs::default_provider();
    for version in [
        rustls::ProtocolVersion::TLSv1_2,
        rustls::ProtocolVersion::TLSv1_3,
    ] {
        assert!(
            provider
                .cipher_suites
                .iter()
                .any(|suite| suite.version().version == version),
            "no enabled cipher suite for {version:?}"
        );
    }
}
