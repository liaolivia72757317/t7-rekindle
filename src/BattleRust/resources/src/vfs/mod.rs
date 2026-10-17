mod directory;

use flate2::{Decompress, FlushDecompress, Status};
use std::collections::BTreeMap;
use std::fmt;
use std::fs::File;
use std::io::{Read, Seek, SeekFrom};
use std::path::{Path, PathBuf};

pub const MAX_ENTRY_BYTES: usize = 16 * 1024 * 1024;
const CHUNK_SIZE: usize = 16384;

#[derive(Debug)]
pub struct ResourceError {
    pub code: &'static str,
    pub archive: String,
    pub entry: String,
    detail: String,
}

impl ResourceError {
    pub(crate) fn new(
        code: &'static str,
        archive: &str,
        entry: &str,
        detail: impl fmt::Display,
    ) -> Self {
        Self {
            code,
            archive: archive.to_owned(),
            entry: entry.to_owned(),
            detail: detail.to_string(),
        }
    }
}

impl fmt::Display for ResourceError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(
            formatter,
            "[T7R_RESOURCE:{}] archive={:?} entry={:?}: {}",
            self.code, self.archive, self.entry, self.detail
        )
    }
}
impl std::error::Error for ResourceError {}

#[derive(Clone, Copy, Debug)]
struct Entry {
    flags: u32,
    offset: u32,
    stored_size: u32,
    decoded_size: u32,
    chunk_count: u32,
}

/// Retains the validated index and a read-only archive handle; it never extracts files.
pub struct Archive<R = File> {
    source: R,
    name: String,
    entries: BTreeMap<String, Entry>,
}

impl Archive<File> {
    pub fn open(path: &Path) -> Result<Self, ResourceError> {
        let name = path.file_name().unwrap_or_default().to_string_lossy();
        let source = File::open(path)
            .map_err(|error| ResourceError::new("archive_read", &name, "", error))?;
        Self::from_reader(source, &name)
    }
}

impl<R: Read + Seek> Archive<R> {
    pub fn from_reader(mut source: R, name: &str) -> Result<Self, ResourceError> {
        let entries = directory::read(&mut source, name)?;
        Ok(Self {
            source,
            name: name.to_owned(),
            entries,
        })
    }

    pub fn read_entry(&mut self, resource_path: &str) -> Result<Vec<u8>, ResourceError> {
        let path = directory::canonical_path(resource_path).map_err(|error| {
            ResourceError::new("content_mismatch", &self.name, resource_path, error)
        })?;
        let entry = self.entries.get(&path).ok_or_else(|| {
            ResourceError::new("entry_missing", &self.name, &path, "target not found")
        })?;
        if entry.stored_size as usize > MAX_ENTRY_BYTES
            || entry.decoded_size as usize > MAX_ENTRY_BYTES
        {
            return Err(ResourceError::new(
                "entry_size",
                &self.name,
                &path,
                "entry exceeds size limit",
            ));
        }
        self.source
            .seek(SeekFrom::Start(u64::from(entry.offset)))
            .map_err(|error| ResourceError::new("archive_read", &self.name, &path, error))?;
        let mut stored = vec![0; entry.stored_size as usize];
        self.source
            .read_exact(&mut stored)
            .map_err(|error| ResourceError::new("entry_decode", &self.name, &path, error))?;
        if entry.flags == 1 {
            return Ok(stored);
        }
        decompress(&stored, entry.decoded_size as usize, entry.chunk_count)
            .map_err(|error| ResourceError::new("entry_decode", &self.name, &path, error))
    }

    pub fn paths(&self) -> impl Iterator<Item = &str> {
        self.entries.keys().map(String::as_str)
    }
}

fn decompress(
    stored: &[u8],
    decoded_size: usize,
    chunk_count: u32,
) -> Result<Vec<u8>, &'static str> {
    let mut remaining = stored;
    let mut result = Vec::with_capacity(decoded_size);
    let mut count = 0;
    while !remaining.is_empty() {
        let limit = CHUNK_SIZE.min(decoded_size - result.len()) + 1;
        let mut block = vec![0; limit];
        let mut decoder = Decompress::new(true);
        let status = decoder
            .decompress(remaining, &mut block, FlushDecompress::Finish)
            .map_err(|_| "invalid compressed stream")?;
        let consumed = decoder.total_in() as usize;
        let produced = decoder.total_out() as usize;
        if status != Status::StreamEnd || consumed == 0 || produced == 0 || produced > CHUNK_SIZE {
            return Err("compressed stream extent mismatch");
        }
        if result.len() + produced > decoded_size {
            return Err("decoded size exceeds entry");
        }
        result.extend_from_slice(&block[..produced]);
        remaining = &remaining[consumed..];
        count += 1;
    }
    if result.len() != decoded_size || count != chunk_count {
        return Err("decoded size or chunk count mismatch");
    }
    Ok(result)
}

/// A selected client installation, rejecting loose overlays just like the original preflight.
pub struct ClientResources {
    root: PathBuf,
    archives: BTreeMap<String, Archive>,
}

impl ClientResources {
    pub fn new(client_directory: &Path) -> Result<Self, ResourceError> {
        if !client_directory.is_absolute() {
            return Err(ResourceError::new(
                "archive_read",
                "",
                "",
                "selected absolute Bin directory required",
            ));
        }
        let root = client_directory.parent().ok_or_else(|| {
            ResourceError::new("archive_read", "", "", "client directory has no parent")
        })?;
        Ok(Self {
            root: root.to_owned(),
            archives: BTreeMap::new(),
        })
    }

    pub fn check_layout(&self) -> Result<(), ResourceError> {
        let read_error = |error| ResourceError::new("archive_read", "vfs", "", error);
        let mut paths = std::fs::read_dir(self.root.join("vfs"))
            .map_err(read_error)?
            .map(|entry| entry.map(|entry| entry.path()))
            .collect::<Result<Vec<_>, _>>()
            .map_err(read_error)?;
        paths.sort();
        for path in paths {
            let name = path.file_name().unwrap_or_default().to_string_lossy();
            let lower = name.to_lowercase();
            if (lower.ends_with(".vfs")
                && !matches!(
                    lower.as_str(),
                    "actor.vfs" | "data1.vfs" | "map.vfs" | "model.vfs"
                ))
                || lower.ends_with("remove.txt")
            {
                return Err(ResourceError::new(
                    "unsupported_overlay",
                    &name,
                    "",
                    "overlay layout is not supported",
                ));
            }
        }
        for path in [
            "data/config/vfsconfig.cfg",
            crate::spawn::SPAWN_PATH,
            crate::navmesh::NAV_PATH,
            crate::bot_names::NAME_PATH,
        ] {
            match self.root.join(path).metadata() {
                Ok(_) => {
                    return Err(ResourceError::new(
                        "unsupported_overlay",
                        "",
                        path,
                        "overlay layout is not supported",
                    ));
                }
                Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
                Err(error) => return Err(read_error(error)),
            }
        }
        Ok(())
    }

    pub fn read(&mut self, archive: &str, resource_path: &str) -> Result<Vec<u8>, ResourceError> {
        if archive.is_empty() || archive.contains(['/', '\\', ':']) || matches!(archive, "." | "..")
        {
            return Err(ResourceError::new(
                "archive_read",
                archive,
                resource_path,
                "archive name must be a filename",
            ));
        }
        let path = directory::canonical_path(resource_path).map_err(|error| {
            ResourceError::new("content_mismatch", archive, resource_path, error)
        })?;
        match self.root.join(&path).metadata() {
            Ok(_) => {
                return Err(ResourceError::new(
                    "unsupported_overlay",
                    archive,
                    &path,
                    "loose resource is not supported",
                ));
            }
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
            Err(error) => return Err(ResourceError::new("archive_read", archive, &path, error)),
        }
        if !self.archives.contains_key(archive) {
            let source = Archive::open(&self.root.join("vfs").join(archive))?;
            self.archives.insert(archive.to_owned(), source);
        }
        self.archives
            .get_mut(archive)
            .expect("inserted archive")
            .read_entry(&path)
    }
}
