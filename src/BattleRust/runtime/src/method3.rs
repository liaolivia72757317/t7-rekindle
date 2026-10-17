use std::{fmt, ptr};
use windows_sys::Win32::Security::Cryptography::*;

#[derive(Debug)]
pub struct CryptoError {
    operation: &'static str,
    status: i32,
}

impl fmt::Display for CryptoError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(
            formatter,
            "{} failed: NTSTATUS {:#010x}",
            self.operation, self.status
        )
    }
}

impl std::error::Error for CryptoError {}

fn checked(operation: &'static str, status: i32) -> Result<(), CryptoError> {
    if status < 0 {
        Err(CryptoError { operation, status })
    } else {
        Ok(())
    }
}

pub struct Cipher {
    algorithm: BCRYPT_ALG_HANDLE,
    key: BCRYPT_KEY_HANDLE,
}

impl Cipher {
    pub fn new() -> Result<Self, CryptoError> {
        let mut cipher = Self {
            algorithm: ptr::null_mut(),
            key: ptr::null_mut(),
        };
        let chaining_mode = b"ChainingModeCBC\0".map(u16::from);
        // SAFETY: all output handles point into this owner. Provider, key and
        // buffers stay alive for each synchronous CNG call; Drop handles failure.
        unsafe {
            checked(
                "BCryptOpenAlgorithmProvider",
                BCryptOpenAlgorithmProvider(
                    &mut cipher.algorithm,
                    BCRYPT_AES_ALGORITHM,
                    ptr::null(),
                    0,
                ),
            )?;
            checked(
                "BCryptSetProperty",
                BCryptSetProperty(
                    cipher.algorithm,
                    BCRYPT_CHAINING_MODE,
                    chaining_mode.as_ptr().cast(),
                    size_of_val(&chaining_mode) as u32,
                    0,
                ),
            )?;
            // Matches the native local handshake's deterministic key.
            let key_bytes = [0; 16];
            checked(
                "BCryptGenerateSymmetricKey",
                BCryptGenerateSymmetricKey(
                    cipher.algorithm,
                    &mut cipher.key,
                    ptr::null_mut(),
                    0,
                    key_bytes.as_ptr(),
                    key_bytes.len() as u32,
                    0,
                ),
            )?;
        }
        Ok(cipher)
    }

    pub fn decrypt(&mut self, ciphertext: &[u8]) -> Result<Vec<u8>, CryptoError> {
        let size = u32::try_from(ciphertext.len()).map_err(|_| CryptoError {
            operation: "ciphertext length",
            status: -1,
        })?;
        let mut result = vec![0; ciphertext.len()];
        let mut iv = std::array::from_fn::<_, 16, _>(|index| index as u8);
        let mut written = 0;
        // SAFETY: this uniquely borrowed key is live; input/output/IV lengths
        // exactly describe their slices. CNG does not retain these buffers.
        checked("BCryptDecrypt", unsafe {
            BCryptDecrypt(
                self.key,
                ciphertext.as_ptr(),
                size,
                ptr::null(),
                iv.as_mut_ptr(),
                iv.len() as u32,
                result.as_mut_ptr(),
                size,
                &mut written,
                0,
            )
        })?;
        if written != size {
            return Err(CryptoError {
                operation: "BCryptDecrypt length",
                status: -1,
            });
        }
        Ok(result)
    }
}

impl Drop for Cipher {
    fn drop(&mut self) {
        // SAFETY: these handles have a single owner; the key is destroyed before
        // its provider, including partially constructed owners on error paths.
        unsafe {
            if !self.key.is_null() {
                BCryptDestroyKey(self.key);
            }
            if !self.algorithm.is_null() {
                BCryptCloseAlgorithmProvider(self.algorithm, 0);
            }
        }
    }
}
