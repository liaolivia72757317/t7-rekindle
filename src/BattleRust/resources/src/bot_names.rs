use crate::gbk;
use crate::table::{invalid, rows};
use crate::vfs::{ClientResources, ResourceError};
use std::collections::BTreeSet;
use std::io;

pub const NAME_PATH: &str = "data/table/sh_res_ai_name_cli.bin";
const PREFIXES: [&str; 4] = ["[进攻]", "[全能]", "[防守]", "[疯狗]"];

pub fn parse_names(data: &[u8]) -> io::Result<Vec<String>> {
    let rows = rows(data, 68)?;
    if !(1..=4096).contains(&rows.len()) {
        return Err(invalid("unsupported AI name table count"));
    }
    let (mut names, mut unique) = (Vec::new(), BTreeSet::new());
    for row in rows {
        let category = &row[4..36];
        let category_end = category
            .iter()
            .position(|&value| value == 0)
            .unwrap_or(category.len());
        if &category[..category_end] != b"npc" {
            continue;
        }
        let raw = &row[36..68];
        let end = raw
            .iter()
            .position(|&value| value == 0)
            .ok_or_else(|| invalid("unterminated AI name"))?;
        let text = gbk::decode(&raw[..end])?;
        // Python str.strip also removes ASCII information separators.
        let name =
            text.trim_matches(|c: char| c.is_whitespace() || ('\u{1c}'..='\u{1f}').contains(&c));
        if name.is_empty()
            || name
                .chars()
                .any(|c| c < ' ' || ('\u{7f}'..='\u{9f}').contains(&c))
        {
            return Err(invalid(
                "AI name must be nonempty and contain no control characters",
            ));
        }
        if unique.insert(name.to_owned()) {
            names.push(name.to_owned());
        }
    }
    if names.is_empty() {
        return Err(invalid("AI name table has no NPC names"));
    }
    Ok(names)
}

pub fn load_names(resources: &mut ClientResources) -> Result<Vec<String>, ResourceError> {
    let data = resources.read("data1.vfs", NAME_PATH)?;
    parse_names(&data)
        .map_err(|error| ResourceError::new("content_mismatch", "data1.vfs", NAME_PATH, error))
}

pub fn validate_roster(names: &[String], player_name: &str) -> io::Result<()> {
    let mut available = 0;
    for name in names {
        if name == player_name {
            continue;
        }
        let mut eligible = true;
        for prefix in PREFIXES {
            let prefixed = format!("{prefix}{name}");
            let (encoded, _, errors) = encoding_rs::GBK.encode(&prefixed);
            if errors || gbk::decode(&encoded)? != prefixed {
                return Err(invalid("invalid GBK AI name"));
            }
            eligible &= prefixed != player_name && encoded.len() <= 31;
        }
        if eligible {
            available += 1;
        }
    }
    if available < 19 {
        return Err(invalid(
            "not enough distinct client NPC names for this roster",
        ));
    }
    Ok(())
}
