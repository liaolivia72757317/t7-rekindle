fn main() {
    if std::env::var("CARGO_CFG_TARGET_ENV").as_deref() == Ok("msvc") {
        // Callers resolve exports dynamically; import artifacts are unused.
        println!("cargo:rustc-cdylib-link-arg=/NOIMPLIB");
        println!("cargo:rustc-cdylib-link-arg=/NOEXP");
    }
}
