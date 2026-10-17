use std::collections::BTreeMap;
use std::ffi::OsString;
use std::fs::OpenOptions;
use std::io::{self, Write};
use std::path::PathBuf;
use std::process::ExitCode;
use t7r_battle_resources::ResourceBundle;
use t7r_battle_resources::frozen::FrozenData;

const USAGE: &str = "usage: t7r-battle-resources BUNDLE.json\n       t7r-battle-resources export --client-directory TARGET/bin --output BUNDLE.json --rules-version 1 --resource-version 1 --client-version CLIENT_VERSION";

#[derive(Debug, PartialEq, Eq)]
enum Command {
    Validate(PathBuf),
    Export {
        client: PathBuf,
        output: PathBuf,
        rules_version: u32,
        resource_version: u32,
        client_version: String,
    },
}

fn invalid(message: &str) -> io::Error {
    io::Error::new(io::ErrorKind::InvalidInput, message)
}

fn parse(args: Vec<OsString>) -> io::Result<Command> {
    if args.len() == 1 && args[0] != "export" {
        return Ok(Command::Validate(PathBuf::from(&args[0])));
    }
    if args.len() != 11 || args[0] != "export" {
        return Err(invalid(USAGE));
    }
    let keys = [
        "--client-directory",
        "--output",
        "--rules-version",
        "--resource-version",
        "--client-version",
    ];
    let mut options = BTreeMap::new();
    for pair in args[1..].chunks_exact(2) {
        let key = pair[0].to_str().ok_or_else(|| invalid(USAGE))?;
        if !keys.contains(&key) || pair[1].is_empty() || options.insert(key, &pair[1]).is_some() {
            return Err(invalid(USAGE));
        }
    }
    let version = |key: &str| -> io::Result<u32> {
        let value: u32 = options[key]
            .to_str()
            .ok_or_else(|| invalid("invalid version"))?
            .parse()
            .map_err(|_| invalid("invalid version"))?;
        if value == 0 {
            return Err(invalid("version must be positive"));
        }
        Ok(value)
    };
    Ok(Command::Export {
        client: PathBuf::from(options["--client-directory"]),
        output: PathBuf::from(options["--output"]),
        rules_version: version("--rules-version")?,
        resource_version: version("--resource-version")?,
        client_version: options["--client-version"]
            .to_str()
            .ok_or_else(|| invalid("invalid client version"))?
            .into(),
    })
}

fn execute(command: Command) -> Result<ResourceBundle, Box<dyn std::error::Error>> {
    match command {
        Command::Validate(path) => Ok(ResourceBundle::load(&path)?),
        Command::Export {
            client,
            output,
            rules_version,
            resource_version,
            client_version,
        } => {
            let bundle = FrozenData::embedded()?.load_client(
                &client,
                rules_version,
                resource_version,
                &client_version,
            )?;
            write_new(&output, &bundle)?;
            Ok(bundle)
        }
    }
}

fn write_new(path: &std::path::Path, bundle: &ResourceBundle) -> io::Result<()> {
    let mut bytes = serde_json::to_vec(bundle.data())?;
    bytes.push(b'\n');
    if bytes.len() > t7r_battle_resources::MAX_BYTES {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            "bundle exceeds size limit",
        ));
    }
    let mut output = OpenOptions::new().write(true).create_new(true).open(path)?;
    output.write_all(&bytes)?;
    output.sync_all()
}

fn main() -> ExitCode {
    let args: Vec<_> = std::env::args_os().skip(1).collect();
    if args.len() == 1 && args[0] == "--help" {
        println!("{USAGE}");
        return ExitCode::SUCCESS;
    }
    match parse(args)
        .map_err(Box::<dyn std::error::Error>::from)
        .and_then(execute)
    {
        Ok(bundle) => {
            println!(
                "map={} rules={} resources={}",
                bundle.map_id(),
                bundle.rules_version(),
                bundle.resource_version()
            );
            ExitCode::SUCCESS
        }
        Err(error) => {
            eprintln!("resource preflight failed: {error}");
            ExitCode::FAILURE
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn args() -> Vec<OsString> {
        [
            "export",
            "--client-directory",
            "TARGET/bin",
            "--output",
            "BUNDLE.json",
            "--rules-version",
            "3",
            "--resource-version",
            "7",
            "--client-version",
            "fixture-v1",
        ]
        .into_iter()
        .map(Into::into)
        .collect()
    }

    #[test]
    fn preserves_validation_and_accepts_export_options_in_any_order() {
        assert_eq!(
            parse(vec!["BUNDLE.json".into()]).unwrap(),
            Command::Validate("BUNDLE.json".into())
        );
        let expected = Command::Export {
            client: "TARGET/bin".into(),
            output: "BUNDLE.json".into(),
            rules_version: 3,
            resource_version: 7,
            client_version: "fixture-v1".into(),
        };
        assert_eq!(parse(args()).unwrap(), expected);
        let original = args();
        let mut reordered = vec!["export".into()];
        for pair in original[1..].chunks_exact(2).rev() {
            reordered.extend_from_slice(pair);
        }
        assert_eq!(parse(reordered).unwrap(), expected);
    }

    #[test]
    fn malformed_options_fail_before_file_access() {
        for values in [vec![], vec!["export".into()], args()[..9].to_vec()] {
            assert!(parse(values).is_err());
        }
        for (index, value) in [
            (1, "--unknown"),
            (1, "--output"),
            (2, ""),
            (6, "0"),
            (6, "4294967296"),
            (8, "1.0"),
            (8, "-1"),
        ] {
            let mut values = args();
            values[index] = value.into();
            assert!(parse(values).is_err());
        }
    }

    #[test]
    fn output_is_a_new_bundle_and_never_overwrites_existing_content() {
        let directory =
            std::env::temp_dir().join(format!("t7r-resource-output-{}", std::process::id()));
        std::fs::create_dir(&directory).unwrap();
        let path = directory.join("bundle.json");
        let bundle = ResourceBundle::from_slice(include_bytes!(
            "../../../../tests/fixtures/resources/bundle-v1.json"
        ))
        .unwrap();
        write_new(&path, &bundle).unwrap();
        assert_eq!(ResourceBundle::load(&path).unwrap().data(), bundle.data());
        let before = std::fs::read(&path).unwrap();
        assert_eq!(
            write_new(&path, &bundle).unwrap_err().kind(),
            io::ErrorKind::AlreadyExists
        );
        assert_eq!(std::fs::read(&path).unwrap(), before);
        std::fs::remove_file(path).unwrap();
        std::fs::remove_dir(directory).unwrap();
    }
}
