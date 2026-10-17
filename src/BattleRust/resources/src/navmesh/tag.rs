use crate::table::invalid;
use serde_json::{Map, Value};
use std::io;

pub(super) const MAX_ITEMS: usize = 100000;

#[derive(Clone)]
pub(super) struct Field {
    pub name: String,
    pub kind: usize,
    pub reference: Option<String>,
}

pub(super) struct Class {
    pub name: String,
    pub version: usize,
    parent: usize,
    fields: Vec<Field>,
}

pub struct TagReader<'a> {
    pub(super) data: &'a [u8],
    pub(super) offset: usize,
    strings: Vec<Option<String>>,
    pub(super) classes: Vec<Option<Class>>,
    pub(super) budget: usize,
}

impl<'a> TagReader<'a> {
    pub fn new(data: &'a [u8]) -> io::Result<Self> {
        if data.len() > crate::vfs::MAX_ENTRY_BYTES {
            return Err(invalid("navigation resource exceeds size limit"));
        }
        Ok(Self {
            data,
            offset: 0,
            strings: vec![Some(String::new()), None],
            classes: vec![None],
            budget: 1000000,
        })
    }

    pub(super) fn read(&mut self, size: usize) -> io::Result<&'a [u8]> {
        if size > self.data.len() - self.offset {
            return Err(invalid(format!(
                "truncated navigation resource at {}",
                self.offset
            )));
        }
        let value = &self.data[self.offset..self.offset + size];
        self.offset += size;
        Ok(value)
    }

    pub fn integer(&mut self) -> io::Result<i64> {
        let mut value = 0u64;
        for shift in (0..35).step_by(7) {
            let byte = self.read(1)?[0];
            value |= u64::from(byte & 127) << shift;
            if byte < 128 {
                if value > 0x1ffffffff {
                    break;
                }
                return Ok(if value & 1 != 0 {
                    -((value >> 1) as i64)
                } else {
                    (value >> 1) as i64
                });
            }
        }
        Err(invalid("invalid navigation integer"))
    }

    pub(super) fn count(&mut self, maximum: usize) -> io::Result<usize> {
        let value = self.integer()?;
        if value < 0 || value as u64 > maximum as u64 {
            return Err(invalid("navigation count outside supported bounds"));
        }
        Ok(value as usize)
    }

    fn string(&mut self) -> io::Result<String> {
        let size = self.integer()?;
        if size < 0 {
            return self
                .strings
                .get((-size) as usize)
                .and_then(|value| value.clone())
                .ok_or_else(|| invalid("invalid or null navigation string reference"));
        }
        if size > 256 {
            return Err(invalid("navigation string exceeds limit"));
        }
        let raw = self.read(size as usize)?;
        if !raw.is_ascii() {
            return Err(invalid("non-ASCII navigation schema name"));
        }
        let value = std::str::from_utf8(raw).expect("ASCII").to_owned();
        self.strings.push(Some(value.clone()));
        Ok(value)
    }

    fn fields(&self, class_id: usize, depth: usize) -> io::Result<Vec<Field>> {
        if depth > 16 {
            return Err(invalid("invalid navigation class hierarchy"));
        }
        let class = self
            .classes
            .get(class_id)
            .and_then(Option::as_ref)
            .ok_or_else(|| invalid("invalid navigation class hierarchy"))?;
        let mut result = if class.parent != 0 {
            self.fields(class.parent, depth + 1)?
        } else {
            Vec::new()
        };
        result.extend(class.fields.iter().cloned());
        Ok(result)
    }

    pub(super) fn objects(
        &mut self,
        class_id: usize,
        count: usize,
        depth: usize,
        columnar: bool,
    ) -> io::Result<Vec<Value>> {
        if count == 0 {
            return Ok(Vec::new());
        }
        let fields = self.fields(class_id, 0)?;
        let mask = self.read(fields.len().div_ceil(8))?.to_vec();
        if !fields.len().is_multiple_of(8)
            && mask
                .last()
                .is_some_and(|value| value >> (fields.len() % 8) != 0)
        {
            return Err(invalid("invalid navigation presence mask"));
        }
        let mut rows = vec![Map::new(); count];
        for (index, field) in fields.iter().enumerate() {
            let values = if mask[index / 8] & (1 << (index % 8)) != 0 {
                self.values(
                    field.kind,
                    field.reference.as_deref(),
                    count,
                    columnar,
                    depth,
                )?
            } else {
                vec![
                    if field.kind & 16 != 0 {
                        Value::Array(Vec::new())
                    } else {
                        Value::from(0)
                    };
                    count
                ]
            };
            for (row, value) in rows.iter_mut().zip(values) {
                row.insert(field.name.clone(), value);
            }
        }
        Ok(rows.into_iter().map(Value::Object).collect())
    }

    pub fn document(mut self) -> io::Result<Value> {
        if self.read(8)? != [0x1e, 0x0d, 0xb0, 0xca, 0xce, 0xfa, 0x11, 0xd0]
            || self.integer()? != 1
            || self.integer()? != 5
        {
            return Err(invalid("unsupported navigation tagfile header"));
        }
        if self.string()? != "hk_2014.2.5-r1" {
            return Err(invalid("unsupported navigation SDK version"));
        }
        for expected in [-10, 0, 0, 0] {
            if self.integer()? != expected {
                return Err(invalid("unsupported navigation v5 header layout"));
            }
        }
        let mut tag = self.integer()?;
        while tag == 2 {
            if self.classes.len() > 32 {
                return Err(invalid("too many navigation classes"));
            }
            let name = self.string()?;
            let version = self.count(100)?;
            let parent = self.count(self.classes.len() - 1)?;
            let mut fields = Vec::new();
            for _ in 0..self.count(32)? {
                let name = self.string()?;
                let kind = self.count(31)?;
                let reference = if kind & 15 == 9 {
                    Some(self.string()?)
                } else {
                    None
                };
                fields.push(Field {
                    name,
                    kind,
                    reference,
                });
            }
            self.classes.push(Some(Class {
                name,
                version,
                parent,
                fields,
            }));
            tag = self.integer()?;
        }
        if tag != 4 {
            return Err(invalid("navigation root object missing"));
        }
        let class_id = self.count(self.classes.len() - 1)?;
        let class = self.classes[class_id]
            .as_ref()
            .ok_or_else(|| invalid("unsupported navigation root class"))?;
        if class.name != "hkaiNavMesh" || class.version != 14 {
            return Err(invalid("unsupported navigation root class"));
        }
        let root = self
            .objects(class_id, 1, 0, false)?
            .pop()
            .expect("one root object");
        if self.integer()? != 7 || self.offset != self.data.len() {
            return Err(invalid(
                "navigation resource has unconsumed objects or bytes",
            ));
        }
        Ok(root)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn schema_references_hierarchy_and_presence_masks_are_bounded() {
        for bytes in [&[3][..], &[7][..], &[2, 0xff][..], &[0x82, 4][..]] {
            assert!(TagReader::new(bytes).unwrap().string().is_err());
        }
        let mut reader = TagReader::new(&[0x80]).unwrap();
        reader.classes.push(Some(Class {
            name: "Root".into(),
            version: 0,
            parent: 0,
            fields: vec![Field {
                name: "value".into(),
                kind: 0,
                reference: None,
            }],
        }));
        assert!(reader.objects(1, 1, 0, false).is_err());
        assert!(reader.fields(0, 0).is_err());
        assert!(reader.fields(99, 0).is_err());
        for parent in 1..=18 {
            reader.classes.push(Some(Class {
                name: "Parent".into(),
                version: 0,
                parent,
                fields: Vec::new(),
            }));
        }
        assert!(reader.fields(19, 0).is_err());
    }
}
