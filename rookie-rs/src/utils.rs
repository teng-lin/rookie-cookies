use std::fs;
use std::path::{Path, PathBuf};

use crate::common::diagnostic::REDACTED_PATH;
use anyhow::Result;
use rand::distr::{Alphanumeric, SampleString};

pub fn random_string(length: usize, prefix: &str, suffix: &str) -> String {
  let random_part = Alphanumeric.sample_string(&mut rand::rng(), length);

  format!("{}{}{}", prefix, random_part, suffix)
}

/// A private directory under the system temp directory, removed on drop.
///
/// Callers copy browser databases here, so on Unix the directory is created
/// with `0700` to keep cookie material out of reach of other local users.
pub struct TempDir {
  path: PathBuf,
}

impl TempDir {
  pub fn new() -> Result<Self> {
    let path = std::env::temp_dir().join(random_string(10, ".tmp", ""));
    create_private_dir(&path)?;
    log::trace!("created private directory {REDACTED_PATH}");
    Ok(Self { path })
  }

  pub fn path(&self) -> &Path {
    &self.path
  }
}

impl Drop for TempDir {
  fn drop(&mut self) {
    if let Err(err) = fs::remove_dir_all(&self.path) {
      log::warn!(
        "failed to remove temporary directory {REDACTED_PATH}: {err}. It may hold a copy of \
         browser cookie data",
      );
    }
  }
}

#[cfg(unix)]
fn create_private_dir(path: &Path) -> Result<()> {
  use anyhow::Context;
  use std::os::unix::fs::DirBuilderExt;

  fs::DirBuilder::new()
    .mode(0o700)
    .create(path)
    .with_context(|| format!("Can't create temporary directory {REDACTED_PATH}"))
}

#[cfg(not(unix))]
fn create_private_dir(path: &Path) -> Result<()> {
  use anyhow::Context;

  fs::create_dir(path).with_context(|| format!("Can't create temporary directory {REDACTED_PATH}"))
}

#[cfg(test)]
pub(crate) mod tests {
  use super::{fs, PathBuf, TempDir};
  use std::sync::atomic::{AtomicU64, Ordering};

  /// Reserves a fresh fixture directory, including when a previous test
  /// process left files behind and the OS reused its process ID.
  pub(crate) fn unique_tmpdir(tag: &str) -> PathBuf {
    static COUNTER: AtomicU64 = AtomicU64::new(0);
    reserve_tmpdir(tag, &COUNTER)
  }

  fn reserve_tmpdir(tag: &str, counter: &AtomicU64) -> PathBuf {
    loop {
      let n = counter.fetch_add(1, Ordering::Relaxed);
      let dir = std::env::temp_dir().join(format!("rookie-test-{tag}-{}-{n}", std::process::id()));
      match fs::create_dir(&dir) {
        Ok(()) => return dir,
        Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => continue,
        Err(error) => panic!("create test fixture directory: {error}"),
      }
    }
  }

  #[test]
  fn fixture_directory_skips_stale_files_after_counter_reset() {
    let counter = AtomicU64::new(0);
    let first = reserve_tmpdir("reused-process-id", &counter);
    let marker = first.join("existing-fixture");
    fs::write(&marker, b"previous process").expect("seed stale fixture");

    // A new test executable starts its counter at zero; Windows can give it
    // the PID of the preceding executable before those fixtures are removed.
    counter.store(0, Ordering::Relaxed);
    let second = reserve_tmpdir("reused-process-id", &counter);
    assert_ne!(first, second);
    assert!(!second.join("existing-fixture").exists());
    assert_eq!(
      fs::read(marker).expect("read stale fixture"),
      b"previous process"
    );

    fs::remove_dir_all(first).expect("remove stale fixture");
    fs::remove_dir_all(second).expect("remove fresh fixture");
  }

  #[test]
  fn temp_dir_is_removed_on_drop() {
    let path = {
      let temp_dir = TempDir::new().expect("create temp dir");
      let path = temp_dir.path().to_path_buf();
      assert!(path.exists());
      path
    };

    assert!(!path.exists());
  }

  #[cfg(unix)]
  #[test]
  fn temp_dir_is_not_readable_by_other_users() {
    use std::os::unix::fs::PermissionsExt;

    let temp_dir = TempDir::new().expect("create temp dir");
    let mode = std::fs::metadata(temp_dir.path())
      .expect("stat temp dir")
      .permissions()
      .mode();

    // The umask can only clear bits, so assert the invariant that matters
    // rather than an exact 0700 that a stricter umask would fail.
    assert_eq!(mode & 0o077, 0, "mode was {:o}", mode & 0o777);
  }
}
