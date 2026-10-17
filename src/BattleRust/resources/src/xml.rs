use crate::table::invalid;
use quick_xml::events::{BytesStart, Event};
use quick_xml::{Reader, XmlVersion};
use std::collections::BTreeMap;
use std::io;

fn valid_character(character: char) -> bool {
    matches!(character, '\t' | '\n' | '\r')
        || (character >= ' ' && character != '\u{fffe}' && character != '\u{ffff}')
}

pub(crate) struct Element {
    pub name: String,
    pub attributes: BTreeMap<String, String>,
    pub children: Vec<Element>,
}

impl Element {
    pub fn find(&self, name: &str) -> io::Result<&Self> {
        self.children
            .iter()
            .find(|child| child.name == name)
            .ok_or_else(|| invalid(format!("missing spawn element {name}")))
    }
    pub fn value(&self) -> io::Result<&str> {
        self.attributes
            .get("value")
            .map(String::as_str)
            .ok_or_else(|| invalid("missing spawn value"))
    }
}

fn element(start: &BytesStart<'_>) -> io::Result<Element> {
    let name = start.name().as_ref().to_owned();
    let mut attributes = BTreeMap::new();
    for attribute in start.attributes() {
        let attribute = attribute.map_err(|error| invalid(error.to_string()))?;
        let key = attribute.key.as_ref();
        if key == "xmlns" || key.starts_with("xmlns:") {
            return Err(invalid("spawn resource namespaces are not supported"));
        }
        let value = attribute
            .normalized_value(XmlVersion::Implicit1_0)
            .map_err(|error| invalid(error.to_string()))?;
        if !value.chars().all(valid_character) {
            return Err(invalid("invalid spawn XML character"));
        }
        attributes.insert(key.to_owned(), value.into_owned());
    }
    Ok(Element {
        name,
        attributes,
        children: Vec::new(),
    })
}

pub(crate) fn parse(text: &str) -> io::Result<Element> {
    if !text.chars().all(valid_character) {
        return Err(invalid("invalid spawn XML character"));
    }
    let mut reader = Reader::from_str(text);
    reader.config_mut().check_comments = true;
    let (mut stack, mut root, mut count) = (Vec::<Element>::new(), None, 0);
    loop {
        let event = reader
            .read_event()
            .map_err(|error| invalid(error.to_string()))?;
        match event {
            Event::Start(start) => {
                count += 1;
                if count > 100000 || stack.len() >= 64 {
                    return Err(invalid("spawn XML exceeds structural limits"));
                }
                stack.push(element(&start)?);
            }
            Event::Empty(start) => {
                count += 1;
                if count > 100000 {
                    return Err(invalid("spawn XML exceeds structural limits"));
                }
                append(element(&start)?, &mut stack, &mut root)?;
            }
            Event::End(_) => {
                let value = stack.pop().ok_or_else(|| invalid("unbalanced spawn XML"))?;
                append(value, &mut stack, &mut root)?;
            }
            Event::DocType(_) => return Err(invalid("spawn XML DTD is not supported")),
            Event::GeneralRef(reference) => {
                let character = reference
                    .resolve_char_ref()
                    .map_err(|error| invalid(error.to_string()))?;
                if character.is_some_and(|character| !valid_character(character)) {
                    return Err(invalid("invalid spawn XML character reference"));
                }
                if character.is_none()
                    && !matches!(reference.as_ref(), "lt" | "gt" | "amp" | "apos" | "quot")
                {
                    return Err(invalid("unknown spawn XML entity"));
                }
                if stack.is_empty() {
                    return Err(invalid("spawn XML text outside root"));
                }
            }
            Event::Text(text)
                if stack.is_empty()
                    && !text.as_ref().bytes().all(|byte| byte.is_ascii_whitespace()) =>
            {
                return Err(invalid("spawn XML text outside root"));
            }
            Event::CData(_) if stack.is_empty() => {
                return Err(invalid("spawn XML CDATA outside root"));
            }
            Event::Eof => break,
            _ => {}
        }
    }
    if !stack.is_empty() {
        return Err(invalid("truncated spawn XML"));
    }
    root.ok_or_else(|| invalid("missing spawn resource root"))
}

fn append(value: Element, stack: &mut [Element], root: &mut Option<Element>) -> io::Result<()> {
    if let Some(parent) = stack.last_mut() {
        parent.children.push(value);
    } else if root.replace(value).is_some() {
        return Err(invalid("multiple spawn XML roots"));
    }
    Ok(())
}
