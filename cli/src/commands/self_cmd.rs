//! `ago self check` and `ago self update`.
//!
//! Reads the GitHub Releases API for `jonnycau/agent-orchestrator`, filters
//! tags shaped like `ago-vX.Y.Z` (the CLI namespace, distinct from the
//! orchestrator's own `vX.Y.Z` tags), and either reports the comparison
//! (`check`) or downloads the matching archive for the current target
//! triple and atomically swaps the running binary (`update`).
//!
//! No external `self_update` crate is used — the install path here is a
//! few well-typed HTTP calls + extraction + `std::fs::rename`, and bringing
//! in a heavy dep for that is poor cost/benefit. Cosign signature
//! verification is **not** performed here (would require shelling out to
//! the `cosign` binary or a large pure-Rust verifier dep); users who need
//! supply-chain proof should pull the artifact from the GitHub Release
//! page and run `cosign verify-blob` manually — see docs/cli.md.

use crate::cli::{SelfAction, SelfArgs};
use crate::error::{AgoError, Result};
use crate::runtime::Runtime;

const RELEASE_OWNER: &str = "jonnycau";
const RELEASE_REPO: &str = "agent-orchestrator";
const TAG_PREFIX: &str = "ago-v";

pub async fn run(_rt: &Runtime, args: SelfArgs) -> Result<()> {
    match args.action {
        SelfAction::Check => check().await,
        SelfAction::Update { force } => update(force).await,
    }
}

/// Compile-time target triple, kept in sync with the cli-release.yml matrix.
/// Returns `None` when the binary was built for a target not listed in the
/// release matrix — `ago self update` then refuses to act and tells the
/// user to install manually.
pub fn current_target() -> Option<&'static str> {
    if cfg!(all(target_os = "macos", target_arch = "aarch64")) {
        Some("aarch64-apple-darwin")
    } else if cfg!(all(target_os = "macos", target_arch = "x86_64")) {
        Some("x86_64-apple-darwin")
    } else if cfg!(all(target_os = "linux", target_arch = "x86_64")) {
        // Released as musl static; linux-gnu callers also get this binary
        // since musl is portable. We do not ship a separate gnu archive.
        Some("x86_64-unknown-linux-musl")
    } else if cfg!(all(target_os = "linux", target_arch = "aarch64")) {
        Some("aarch64-unknown-linux-musl")
    } else if cfg!(all(target_os = "windows", target_arch = "x86_64")) {
        Some("x86_64-pc-windows-msvc")
    } else {
        None
    }
}

#[derive(Debug, Clone)]
pub struct Release {
    pub tag: String,
    pub version: SemVer,
    pub assets: Vec<Asset>,
}

#[derive(Debug, Clone)]
pub struct Asset {
    pub name: String,
    pub url: String,
}

/// Three-component version. Pre-releases (anything past the third dot)
/// are ordered BEFORE the matching stable per semver — `0.5.1-rc.1` is
/// less than `0.5.1`. The CLI does not currently ship pre-releases so
/// this rule is theoretical but documented for forward compatibility.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SemVer {
    pub major: u64,
    pub minor: u64,
    pub patch: u64,
    pub pre: String,
}

impl Ord for SemVer {
    fn cmp(&self, other: &Self) -> std::cmp::Ordering {
        // Per semver: a version with a pre-release identifier sorts
        // BEFORE the equivalent stable version (`1.0.0-alpha` < `1.0.0`).
        (self.major, self.minor, self.patch)
            .cmp(&(other.major, other.minor, other.patch))
            .then_with(|| match (self.pre.is_empty(), other.pre.is_empty()) {
                (true, true) => std::cmp::Ordering::Equal,
                (true, false) => std::cmp::Ordering::Greater, // stable > pre
                (false, true) => std::cmp::Ordering::Less,    // pre < stable
                (false, false) => self.pre.cmp(&other.pre),
            })
    }
}

impl PartialOrd for SemVer {
    fn partial_cmp(&self, other: &Self) -> Option<std::cmp::Ordering> {
        Some(self.cmp(other))
    }
}

impl std::fmt::Display for SemVer {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        if self.pre.is_empty() {
            write!(f, "{}.{}.{}", self.major, self.minor, self.patch)
        } else {
            write!(f, "{}.{}.{}-{}", self.major, self.minor, self.patch, self.pre)
        }
    }
}

/// Parse `ago-vX.Y.Z[-suffix]` into a SemVer. Returns None on anything
/// that doesn't fit the shape, so non-CLI tags (`v0.x.x` orchestrator
/// tags, RC branches, etc.) are silently filtered out by callers.
pub fn parse_tag(tag: &str) -> Option<SemVer> {
    let rest = tag.strip_prefix(TAG_PREFIX)?;
    parse_version(rest)
}

pub fn parse_version(s: &str) -> Option<SemVer> {
    let (core, pre) = match s.split_once('-') {
        Some((c, p)) => (c, p.to_string()),
        None => (s, String::new()),
    };
    let mut parts = core.split('.');
    let major = parts.next()?.parse().ok()?;
    let minor = parts.next()?.parse().ok()?;
    let patch = parts.next()?.parse().ok()?;
    if parts.next().is_some() {
        return None;
    }
    Some(SemVer {
        major,
        minor,
        patch,
        pre,
    })
}

async fn check() -> Result<()> {
    let current = parse_version(env!("CARGO_PKG_VERSION")).ok_or_else(|| {
        AgoError::Other(format!(
            "internal: own CARGO_PKG_VERSION '{}' is not a valid semver",
            env!("CARGO_PKG_VERSION")
        ))
    })?;
    let release = latest_release().await?;
    if release.version > current {
        println!(
            "ago {} → {} available — run `ago self update` to install",
            current, release.version
        );
    } else if release.version == current {
        println!("ago {} is the latest release", current);
    } else {
        println!(
            "ago {} is newer than the latest release on GitHub ({}) — local build?",
            current, release.version
        );
    }
    Ok(())
}

async fn update(force: bool) -> Result<()> {
    let current = parse_version(env!("CARGO_PKG_VERSION")).ok_or_else(|| {
        AgoError::Other(format!(
            "internal: own CARGO_PKG_VERSION '{}' is not a valid semver",
            env!("CARGO_PKG_VERSION")
        ))
    })?;
    let target = current_target().ok_or_else(|| {
        AgoError::Other(
            "self update: this binary was built for a target not in the release matrix — \
             install manually from https://github.com/jonnycau/agent-orchestrator/releases"
                .into(),
        )
    })?;
    let release = latest_release().await?;
    if !force && release.version <= current {
        println!("ago {} is already the latest release — nothing to do", current);
        return Ok(());
    }
    let asset = pick_asset(&release, target)?;
    eprintln!(
        "\x1b[2m· downloading {} ({}…)\x1b[0m",
        asset.name,
        &asset.url[..asset.url.len().min(80)]
    );
    let bytes = download(&asset.url).await?;
    eprintln!("\x1b[2m· extracting binary\x1b[0m");
    let staged = extract_binary_to_tmp(&bytes, &asset.name)?;
    let target_path = std::env::current_exe()
        .map_err(|e| AgoError::Other(format!("could not locate current exe: {e}")))?;
    eprintln!("\x1b[2m· replacing {}\x1b[0m", target_path.display());
    replace_binary(&staged, &target_path)?;
    println!(
        "✓ installed ago {} → {}",
        release.version,
        target_path.display()
    );
    println!("re-run `ago --version` to confirm.");
    Ok(())
}

fn pick_asset(release: &Release, target: &str) -> Result<Asset> {
    // Archive names follow `ago-vX.Y.Z-<target>.<ext>`.
    let suffix_tar = format!("-{target}.tar.gz");
    let suffix_zip = format!("-{target}.zip");
    release
        .assets
        .iter()
        .find(|a| a.name.ends_with(&suffix_tar) || a.name.ends_with(&suffix_zip))
        .cloned()
        .ok_or_else(|| {
            AgoError::Other(format!(
                "no asset for target {target} in release {} — only: {}",
                release.tag,
                release
                    .assets
                    .iter()
                    .map(|a| a.name.as_str())
                    .collect::<Vec<_>>()
                    .join(", ")
            ))
        })
}

async fn latest_release() -> Result<Release> {
    let url = format!(
        "https://api.github.com/repos/{RELEASE_OWNER}/{RELEASE_REPO}/releases?per_page=20"
    );
    let http = build_http()?;
    let resp = http
        .get(&url)
        .header("user-agent", concat!("ago/", env!("CARGO_PKG_VERSION")))
        .header("accept", "application/vnd.github+json")
        .send()
        .await
        .map_err(|e| AgoError::Other(format!("github releases: {e}")))?;
    if !resp.status().is_success() {
        return Err(AgoError::Other(format!(
            "github releases: HTTP {}",
            resp.status()
        )));
    }
    let raw: serde_json::Value = resp
        .json()
        .await
        .map_err(|e| AgoError::Other(format!("github releases: parse: {e}")))?;
    let arr = raw
        .as_array()
        .ok_or_else(|| AgoError::Other("github releases: not an array".into()))?;
    let mut releases: Vec<Release> = arr
        .iter()
        .filter_map(|r| {
            let tag = r.get("tag_name")?.as_str()?.to_string();
            let version = parse_tag(&tag)?;
            let assets = r
                .get("assets")?
                .as_array()?
                .iter()
                .filter_map(|a| {
                    Some(Asset {
                        name: a.get("name")?.as_str()?.to_string(),
                        url: a.get("browser_download_url")?.as_str()?.to_string(),
                    })
                })
                .collect();
            Some(Release {
                tag,
                version,
                assets,
            })
        })
        .collect();
    releases.sort_by(|a, b| b.version.cmp(&a.version));
    releases
        .into_iter()
        .next()
        .ok_or_else(|| AgoError::Other("no ago-v* releases found on GitHub".into()))
}

fn build_http() -> Result<reqwest::Client> {
    reqwest::Client::builder()
        .user_agent(concat!("ago/", env!("CARGO_PKG_VERSION")))
        .build()
        .map_err(|e| AgoError::Other(format!("reqwest builder: {e}")))
}

async fn download(url: &str) -> Result<Vec<u8>> {
    let http = build_http()?;
    let resp = http
        .get(url)
        .send()
        .await
        .map_err(|e| AgoError::Other(format!("download: {e}")))?;
    if !resp.status().is_success() {
        return Err(AgoError::Other(format!(
            "download: HTTP {}",
            resp.status()
        )));
    }
    let bytes = resp
        .bytes()
        .await
        .map_err(|e| AgoError::Other(format!("download body: {e}")))?;
    Ok(bytes.to_vec())
}

/// Extract the `ago[.exe]` binary from the downloaded archive into a
/// temp file. Returns the temp file path; the caller does the swap.
fn extract_binary_to_tmp(bytes: &[u8], asset_name: &str) -> Result<std::path::PathBuf> {
    let staging_dir = std::env::temp_dir().join(format!(
        "ago-update-{}",
        uuid::Uuid::new_v4().as_simple()
    ));
    std::fs::create_dir_all(&staging_dir).map_err(AgoError::from)?;
    let staged = if asset_name.ends_with(".zip") {
        extract_zip(bytes, &staging_dir)?
    } else if asset_name.ends_with(".tar.gz") {
        extract_tar_gz(bytes, &staging_dir)?
    } else {
        return Err(AgoError::Other(format!(
            "unsupported asset format: {asset_name}"
        )));
    };
    set_executable(&staged)?;
    Ok(staged)
}

fn extract_zip(bytes: &[u8], dest: &std::path::Path) -> Result<std::path::PathBuf> {
    use std::io::Read;
    let reader = std::io::Cursor::new(bytes);
    let mut archive = zip::ZipArchive::new(reader)
        .map_err(|e| AgoError::Other(format!("invalid zip: {e}")))?;
    for i in 0..archive.len() {
        let mut entry = archive
            .by_index(i)
            .map_err(|e| AgoError::Other(format!("zip entry {i}: {e}")))?;
        let Some(rel) = entry.enclosed_name() else {
            continue;
        };
        let name = rel.file_name().and_then(|n| n.to_str()).unwrap_or("");
        if name != "ago.exe" && name != "ago" {
            continue;
        }
        let target = dest.join(name);
        let mut buf = Vec::with_capacity(entry.size() as usize);
        entry.read_to_end(&mut buf).map_err(AgoError::from)?;
        std::fs::write(&target, &buf).map_err(AgoError::from)?;
        return Ok(target);
    }
    Err(AgoError::Other(
        "zip archive did not contain an `ago` binary".into(),
    ))
}

fn extract_tar_gz(bytes: &[u8], dest: &std::path::Path) -> Result<std::path::PathBuf> {
    // Minimal tar.gz reader. Avoids pulling in the `tar` + `flate2` crates
    // explicitly — we already get flate2 transitively via zip, and tar is
    // simple enough to walk by hand for the one entry we care about.
    use std::io::Read;
    let mut decoder = flate2::read::GzDecoder::new(bytes);
    let mut decompressed = Vec::new();
    decoder
        .read_to_end(&mut decompressed)
        .map_err(|e| AgoError::Other(format!("gzip decode: {e}")))?;
    // tar entry header is 512 bytes. We look for an `ago` regular-file
    // entry, then read the next `size` bytes (rounded up to 512).
    let mut pos = 0usize;
    while pos + 512 <= decompressed.len() {
        let header = &decompressed[pos..pos + 512];
        // All-zero header marks end of archive.
        if header.iter().all(|&b| b == 0) {
            break;
        }
        let name = parse_tar_name(header);
        let size = parse_tar_size(header).ok_or_else(|| {
            AgoError::Other("tar: corrupt size field in header".into())
        })?;
        let typeflag = header[156];
        pos += 512;
        let file_start = pos;
        let file_end = pos + size;
        // Advance past the file body, rounded up to a 512 boundary.
        pos = file_end + (512 - (size % 512)) % 512;
        if pos > decompressed.len() {
            return Err(AgoError::Other("tar: truncated".into()));
        }
        // typeflag '0' or '\0' = regular file.
        if typeflag != b'0' && typeflag != 0 {
            continue;
        }
        // Match either `ago` at any depth or the conventional
        // `<release-name>/ago` layout the cli-release workflow produces.
        let basename = name.rsplit('/').next().unwrap_or(&name);
        if basename == "ago" {
            let target = dest.join("ago");
            std::fs::write(&target, &decompressed[file_start..file_end])
                .map_err(AgoError::from)?;
            return Ok(target);
        }
    }
    Err(AgoError::Other(
        "tar.gz archive did not contain an `ago` binary".into(),
    ))
}

fn parse_tar_name(header: &[u8]) -> String {
    // Field at offset 0, 100 bytes, NUL-padded.
    let raw = &header[..100];
    let end = raw.iter().position(|&b| b == 0).unwrap_or(raw.len());
    String::from_utf8_lossy(&raw[..end]).into_owned()
}

fn parse_tar_size(header: &[u8]) -> Option<usize> {
    // Field at offset 124, 12 bytes, octal ASCII, NUL/space terminated.
    let raw = &header[124..136];
    let s = std::str::from_utf8(raw).ok()?.trim_end_matches(['\0', ' ']);
    usize::from_str_radix(s.trim(), 8).ok()
}

#[cfg(unix)]
fn set_executable(path: &std::path::Path) -> Result<()> {
    use std::os::unix::fs::PermissionsExt;
    let mut perm = std::fs::metadata(path).map_err(AgoError::from)?.permissions();
    perm.set_mode(0o755);
    std::fs::set_permissions(path, perm).map_err(AgoError::from)?;
    Ok(())
}

#[cfg(not(unix))]
fn set_executable(_path: &std::path::Path) -> Result<()> {
    Ok(())
}

/// Atomically replace `target` with `new`. On Unix this is a single
/// `rename(2)` once the staged file is on the same filesystem (we move
/// it next to the target first). On Windows the running executable is
/// locked, so we rename it to `<target>.old` first; the OS releases the
/// `.old` lock when the current process exits.
fn replace_binary(new: &std::path::Path, target: &std::path::Path) -> Result<()> {
    let target_dir = target.parent().ok_or_else(|| {
        AgoError::Other(format!("target has no parent dir: {}", target.display()))
    })?;
    let staged = target_dir.join(format!(
        ".ago-staged-{}",
        uuid::Uuid::new_v4().as_simple()
    ));
    std::fs::copy(new, &staged).map_err(AgoError::from)?;
    let _ = std::fs::remove_file(new); // best effort, tmp file
    #[cfg(unix)]
    {
        std::fs::rename(&staged, target).map_err(AgoError::from)?;
    }
    #[cfg(not(unix))]
    {
        let backup = target_dir.join(format!(
            ".ago-old-{}",
            uuid::Uuid::new_v4().as_simple()
        ));
        std::fs::rename(target, &backup).map_err(AgoError::from)?;
        std::fs::rename(&staged, target).map_err(AgoError::from)?;
        // Best-effort cleanup: deleting the locked .old usually fails on
        // Windows until the process exits — ignore.
        let _ = std::fs::remove_file(&backup);
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parse_tag_recognises_ago_prefix() {
        assert_eq!(
            parse_tag("ago-v1.2.3"),
            Some(SemVer {
                major: 1,
                minor: 2,
                patch: 3,
                pre: String::new()
            })
        );
        assert_eq!(parse_tag("v1.2.3"), None, "orchestrator tag must be filtered");
        assert_eq!(parse_tag("ago-vbad"), None);
    }

    #[test]
    fn parse_version_handles_pre_release() {
        let v = parse_version("0.5.1-rc.1").unwrap();
        assert_eq!(v.major, 0);
        assert_eq!(v.minor, 5);
        assert_eq!(v.patch, 1);
        assert_eq!(v.pre, "rc.1");
    }

    #[test]
    fn pre_release_orders_before_stable() {
        let stable = parse_version("0.5.1").unwrap();
        let rc = parse_version("0.5.1-rc.1").unwrap();
        assert!(rc < stable, "rc.1 must be less than stable 0.5.1");
    }

    #[test]
    fn version_ordering_obeys_semver() {
        let a = parse_version("0.4.9").unwrap();
        let b = parse_version("0.5.0").unwrap();
        let c = parse_version("0.5.1").unwrap();
        assert!(a < b);
        assert!(b < c);
        assert!(a < c);
    }

    #[test]
    fn pick_asset_matches_target_suffix() {
        let r = Release {
            tag: "ago-v0.5.2".into(),
            version: parse_version("0.5.2").unwrap(),
            assets: vec![
                Asset {
                    name: "ago-v0.5.2-aarch64-apple-darwin.tar.gz".into(),
                    url: "u1".into(),
                },
                Asset {
                    name: "ago-v0.5.2-x86_64-pc-windows-msvc.zip".into(),
                    url: "u2".into(),
                },
            ],
        };
        let mac = pick_asset(&r, "aarch64-apple-darwin").unwrap();
        assert_eq!(mac.url, "u1");
        let win = pick_asset(&r, "x86_64-pc-windows-msvc").unwrap();
        assert_eq!(win.url, "u2");
    }

    #[test]
    fn pick_asset_errors_when_no_match() {
        let r = Release {
            tag: "ago-v0.5.2".into(),
            version: parse_version("0.5.2").unwrap(),
            assets: vec![Asset {
                name: "ago-v0.5.2-aarch64-apple-darwin.tar.gz".into(),
                url: "u".into(),
            }],
        };
        let err = pick_asset(&r, "i386-foo-bar").unwrap_err();
        let msg = format!("{err}");
        assert!(msg.contains("no asset for target"));
    }

    #[test]
    fn parse_tar_size_decodes_octal() {
        let mut header = [0u8; 512];
        header[124..132].copy_from_slice(b"00000010"); // 8 in octal
        assert_eq!(parse_tar_size(&header), Some(8));
    }
}
