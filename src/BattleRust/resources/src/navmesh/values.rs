use super::tag::{MAX_ITEMS, TagReader};
use crate::table::invalid;
use serde_json::Value;
use std::io;

impl TagReader<'_> {
    pub(super) fn values(
        &mut self,
        kind: usize,
        name: Option<&str>,
        count: usize,
        array: bool,
        depth: usize,
    ) -> io::Result<Vec<Value>> {
        self.budget = self
            .budget
            .checked_sub(count)
            .ok_or_else(|| invalid("navigation object budget exceeded"))?;
        if depth > 16 {
            return Err(invalid("navigation object budget exceeded"));
        }
        if kind & 16 != 0 {
            let mut values = Vec::with_capacity(count);
            for _ in 0..count {
                let size = self.count(MAX_ITEMS)?;
                values.push(Value::Array(self.values(
                    kind & 15,
                    name,
                    size,
                    true,
                    depth + 1,
                )?));
            }
            return Ok(values);
        }
        match kind {
            0 => Ok(vec![Value::from(0); count]),
            1 => Ok(self
                .read(count)?
                .iter()
                .map(|&value| Value::from(value))
                .collect()),
            2 => {
                if array && count != 0 && self.integer()? != 4 {
                    return Err(invalid("unsupported navigation integer array encoding"));
                }
                (0..count)
                    .map(|_| self.integer().map(Value::from))
                    .collect()
            }
            3 | 4 => {
                let width = if kind == 3 { 1 } else { 4 };
                if array && count != 0 && kind == 4 && self.integer()? != width {
                    return Err(invalid("unsupported navigation vector dimension"));
                }
                let raw = self.read(count * width as usize * 4)?;
                let mut values = Vec::with_capacity(count);
                for row in raw.chunks_exact(width as usize * 4) {
                    let mut vector = Vec::with_capacity(width as usize);
                    for bytes in row.chunks_exact(4) {
                        let value = f32::from_le_bytes(bytes.try_into().expect("float width"));
                        if !value.is_finite() {
                            return Err(invalid("non-finite navigation geometry"));
                        }
                        vector.push(Value::from(f64::from(value)));
                    }
                    values.push(if kind == 3 {
                        vector.pop().expect("scalar value")
                    } else {
                        Value::Array(vector)
                    });
                }
                Ok(values)
            }
            9 => {
                let ids: Vec<_> = self
                    .classes
                    .iter()
                    .enumerate()
                    .filter(|(_, class)| {
                        class
                            .as_ref()
                            .is_some_and(|class| Some(class.name.as_str()) == name)
                    })
                    .map(|(index, _)| index)
                    .collect();
                if ids.len() != 1 {
                    return Err(invalid("unknown navigation structure"));
                }
                self.objects(ids[0], count, depth + 1, array)
            }
            _ => Err(invalid("unsupported navigation field type")),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn value_decoding_checks_budget_depth_types_and_array_encoding() {
        let mut reader = TagReader::new(&[1]).unwrap();
        reader.budget = 0;
        assert!(reader.values(1, None, 1, false, 0).is_err());
        assert!(
            TagReader::new(&[])
                .unwrap()
                .values(0, None, 0, false, 17)
                .is_err()
        );
        assert!(
            TagReader::new(&[])
                .unwrap()
                .values(5, None, 1, false, 0)
                .is_err()
        );
        assert!(
            TagReader::new(&[6])
                .unwrap()
                .values(2, None, 1, true, 0)
                .is_err()
        );
        assert!(
            TagReader::new(&[6])
                .unwrap()
                .values(4, None, 1, true, 0)
                .is_err()
        );
        assert!(
            TagReader::new(&[])
                .unwrap()
                .values(9, Some("Missing"), 1, false, 0)
                .is_err()
        );
        assert!(
            TagReader::new(&[3])
                .unwrap()
                .values(16, None, 1, false, 0)
                .is_err()
        );
        assert!(
            TagReader::new(&f32::NAN.to_le_bytes())
                .unwrap()
                .values(3, None, 1, false, 0)
                .is_err()
        );
        assert_eq!(
            TagReader::new(&1.5f32.to_le_bytes())
                .unwrap()
                .values(3, None, 1, false, 0)
                .unwrap(),
            [Value::from(1.5)]
        );
        assert_eq!(
            TagReader::new(&[0x7f])
                .unwrap()
                .values(1, None, 1, false, 0)
                .unwrap(),
            [Value::from(127)]
        );
        assert!(
            TagReader::new(&[])
                .unwrap()
                .values(2, None, 0, true, 0)
                .unwrap()
                .is_empty()
        );
    }
}
