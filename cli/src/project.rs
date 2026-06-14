//! Per-project preset loaded from a `.ago.yaml` (or `.ago.yml`) file walked up
//! from the current working directory.
//!
//! Resolution order for any given setting (highest priority first):
//!
//! 1. The CLI flag (`--agent`, `--model`, ...)
//! 2. `.ago.yaml` walked up from `cwd` to a project root (stops at the user's
//!    home directory or the filesystem root, whichever comes first).
//! 3. Global config (`~/.config/ago/config.toml`).
//! 4. Built-in defaults.
//!
//! The YAML schema is intentionally tiny so that adding fields is a
//! deliberate, reviewable change. Unknown keys are rejected via
//! `#[serde(deny_unknown_fields)]` — protects against typos that would
//! otherwise be silently ignored.

use crate::error::{AgoError, Result};
use serde::Deserialize;
use std::path::{Path, PathBuf};

pub const PROJECT_FILE_PRIMARY: &str = ".ago.yaml";
pub const PROJECT_FILE_FALLBACK: &str = ".ago.yml";

#[derive(Debug, Default, Clone, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct ProjectPreset {
    pub server: Option<String>,
    pub agent: Option<String>,
    pub model: Option<String>,
    pub provider: Option<String>,
    pub max_steps: Option<u32>,
    /// Tune the `@file` / `@dir` expansion budget. Optional — built-in safe
    /// defaults apply when missing.
    pub context: Option<ContextOverrides>,
    /// Per-project shell-command policy for `--client-tools` runs. Optional —
    /// when absent the global allowlist cache (`~/.cache/ago/shell-allow.json`)
    /// plus the interactive confirm prompt are the only gate.
    pub shell: Option<ShellPolicy>,
    /// Run `--client-tools` sessions inside a container that mounts ONLY this
    /// project dir, so `shell_exec` cannot touch the host filesystem (the shell
    /// policy above only gates by binary name, not by path). Consumed by the
    /// jail-by-default `ago` wrapper; the binary itself only *warns* when on
    /// (or defaults on) but the session is running un-sandboxed. Defaults to
    /// `true` when omitted — see [`ProjectPreset::jail_enabled`].
    pub jail: Option<bool>,
    /// Container image the jail wrapper runs `--client-tools` sessions in. The
    /// default base (`ubuntu:24.04`) is bare, so `shell_exec` of tools the
    /// agent reaches for (`git`, `rg`, `python`, …) fails with
    /// `shell_spawn_failed`. Point this at a richer image to pre-install the
    /// toolchain. Consumed by the `ago` wrapper; precedence is
    /// `AGO_JAIL_IMAGE` env > this key > the built-in default.
    pub jail_image: Option<String>,
    /// Opt-in: bind-mount the host Docker socket into the jail so `shell_exec`
    /// can drive the host daemon (`docker compose up`, …), and mount the project
    /// at its real host path so Compose volume paths resolve. Consumed by the
    /// `ago` wrapper (`AGO_JAIL_DOCKER` env > this key); the binary only needs to
    /// *recognize* the key so strict parsing does not reject `.ago.yaml`.
    ///
    /// DANGER: handing the Docker socket to the sandbox is root-equivalent on the
    /// host and punctures the jail's file isolation. Defaults to `false`.
    pub jail_docker: Option<bool>,
}

/// Project-scoped shell policy layered on top of the global allowlist cache.
///
/// Precedence at the gate (see `LocalToolRunner`):
///   1. `deny`  — a hard block: the binary is refused even if it appears in
///      the global cache or the user would confirm it. Wins over everything,
///      including `allow_all`.
///   2. `allow_all` — when true, flips the gate to default-ALLOW: any binary
///      not in `deny` runs with no prompt. Trades the fail-closed default for
///      convenience — the agent can run arbitrary commands, so always pair it
///      with a `deny` list (at least `rm`, `curl`, `sudo`, and the shells).
///   3. `allow` — pre-approved for THIS project: runs without a prompt and is
///      NOT written to the global cache (stays project-local).
///   4. global cache / interactive confirm (unchanged, fail-closed default).
///
/// Entries are matched by `argv[0]` basename, same as the cache, so a path
/// alias (`/usr/bin/rm`) cannot slip past a `deny: [rm]`.
#[derive(Debug, Default, Clone, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct ShellPolicy {
    /// Flip to default-allow: run anything not in `deny` without prompting.
    /// Opt-in and intentionally insecure-by-design; defaults to false.
    #[serde(default)]
    pub allow_all: bool,
    #[serde(default)]
    pub allow: Vec<String>,
    #[serde(default)]
    pub deny: Vec<String>,
}

/// Per-project overrides for `@`-reference expansion. Any field that is
/// `None` keeps the built-in default. `exclude_extra` is *appended* to the
/// hard-coded deny-list (secrets / lockfiles / heavy artifacts) — there is
/// no way to weaken the built-in safety patterns from .ago.yaml.
#[derive(Debug, Default, Clone, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct ContextOverrides {
    pub max_file_bytes: Option<usize>,
    pub max_total_bytes: Option<usize>,
    pub max_refs: Option<usize>,
    /// Cap on the number of files a single `@dir/**` recursive reference is
    /// allowed to inline. Independent from `max_refs` (which counts user-typed
    /// `@<path>` tokens) — one recursive ref can still fan out to N files.
    /// Built-in default: 64.
    pub max_dir_files: Option<usize>,
    #[serde(default)]
    pub exclude_extra: Vec<String>,
}

impl ProjectPreset {
    /// Whether `--client-tools` sessions for this project should run jailed in
    /// a container. Defaults to `true` (jail-by-default) when `jail:` is omitted
    /// from `.ago.yaml`; set `jail: false` to opt out.
    pub fn jail_enabled(&self) -> bool {
        self.jail.unwrap_or(true)
    }

    /// Whether the host Docker socket should be passed through into the jail.
    /// Defaults to `false` (off) when `jail_docker:` is omitted. Consumed by the
    /// `ago` wrapper; exposed here so callers/tests can read the parsed value.
    pub fn jail_docker_enabled(&self) -> bool {
        self.jail_docker.unwrap_or(false)
    }

    /// Walk up from `start_dir` looking for the project file. Stops at
    /// `stop_at` (exclusive — that directory is not searched).
    pub fn discover(start_dir: &Path, stop_at: Option<&Path>) -> Result<Option<(PathBuf, Self)>> {
        let mut cursor = start_dir.to_path_buf();
        loop {
            for name in [PROJECT_FILE_PRIMARY, PROJECT_FILE_FALLBACK] {
                let candidate = cursor.join(name);
                if candidate.is_file() {
                    let preset = Self::load(&candidate)?;
                    return Ok(Some((candidate, preset)));
                }
            }
            if let Some(stop) = stop_at {
                if cursor == stop {
                    return Ok(None);
                }
            }
            if !cursor.pop() {
                return Ok(None);
            }
        }
    }

    pub fn load(path: &Path) -> Result<Self> {
        let raw = std::fs::read_to_string(path)
            .map_err(|e| AgoError::Config(format!("read {}: {e}", path.display())))?;
        let parsed: ProjectPreset = serde_yaml::from_str(&raw)
            .map_err(|e| AgoError::Config(format!("{}: {e}", path.display())))?;
        if let Some(server) = parsed.server.as_deref() {
            crate::config::validate_server_url(server)?;
        }
        if let Some(ms) = parsed.max_steps {
            if !(1..=200).contains(&ms) {
                return Err(AgoError::Config(format!(
                    "{}: max_steps must be between 1 and 200, got {ms}",
                    path.display()
                )));
            }
        }
        if let Some(ctx) = parsed.context.as_ref() {
            // Reasonable upper bounds — protect against typos like
            // `max_total_bytes: 1_000_000_000` that would auto-cap a single
            // turn at 1 GB. The hard ceiling is 1 MB per file / 5 MB total.
            if let Some(n) = ctx.max_file_bytes {
                if n == 0 || n > 1_000_000 {
                    return Err(AgoError::Config(format!(
                        "{}: context.max_file_bytes must be in 1..=1_000_000, got {n}",
                        path.display()
                    )));
                }
            }
            if let Some(n) = ctx.max_total_bytes {
                if n == 0 || n > 5_000_000 {
                    return Err(AgoError::Config(format!(
                        "{}: context.max_total_bytes must be in 1..=5_000_000, got {n}",
                        path.display()
                    )));
                }
            }
            if let Some(n) = ctx.max_refs {
                if n == 0 || n > 256 {
                    return Err(AgoError::Config(format!(
                        "{}: context.max_refs must be in 1..=256, got {n}",
                        path.display()
                    )));
                }
            }
            if let Some(n) = ctx.max_dir_files {
                if n == 0 || n > 2048 {
                    return Err(AgoError::Config(format!(
                        "{}: context.max_dir_files must be in 1..=2048, got {n}",
                        path.display()
                    )));
                }
            }
        }
        Ok(parsed)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::tempdir;

    fn write(path: &Path, contents: &str) {
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent).unwrap();
        }
        std::fs::write(path, contents).unwrap();
    }

    #[test]
    fn discover_finds_file_in_cwd() {
        let dir = tempdir().unwrap();
        write(
            &dir.path().join(".ago.yaml"),
            "agent: backend\nmodel: claude-sonnet-4-6\n",
        );
        let (p, preset) = ProjectPreset::discover(dir.path(), None).unwrap().unwrap();
        assert_eq!(p, dir.path().join(".ago.yaml"));
        assert_eq!(preset.agent.as_deref(), Some("backend"));
        assert_eq!(preset.model.as_deref(), Some("claude-sonnet-4-6"));
    }

    #[test]
    fn discover_walks_up_to_ancestor() {
        let dir = tempdir().unwrap();
        let nested = dir.path().join("a").join("b").join("c");
        std::fs::create_dir_all(&nested).unwrap();
        write(&dir.path().join(".ago.yaml"), "agent: a\n");
        let (_, preset) = ProjectPreset::discover(&nested, None).unwrap().unwrap();
        assert_eq!(preset.agent.as_deref(), Some("a"));
    }

    #[test]
    fn discover_prefers_yaml_over_yml() {
        let dir = tempdir().unwrap();
        write(&dir.path().join(".ago.yaml"), "agent: primary\n");
        write(&dir.path().join(".ago.yml"), "agent: fallback\n");
        let (_, preset) = ProjectPreset::discover(dir.path(), None).unwrap().unwrap();
        assert_eq!(preset.agent.as_deref(), Some("primary"));
    }

    #[test]
    fn discover_returns_none_when_absent() {
        let dir = tempdir().unwrap();
        let res = ProjectPreset::discover(dir.path(), Some(dir.path())).unwrap();
        assert!(res.is_none());
    }

    #[test]
    fn stop_at_blocks_further_ascent() {
        let dir = tempdir().unwrap();
        write(&dir.path().join(".ago.yaml"), "agent: outer\n");
        let inner = dir.path().join("inner");
        std::fs::create_dir(&inner).unwrap();
        // stop_at is `inner` — walk should not see the file in `dir`.
        let res = ProjectPreset::discover(&inner, Some(&inner)).unwrap();
        assert!(res.is_none());
    }

    #[test]
    fn unknown_keys_are_rejected() {
        let dir = tempdir().unwrap();
        write(&dir.path().join(".ago.yaml"), "agent: a\nbogus: 1\n");
        let err = ProjectPreset::discover(dir.path(), None).unwrap_err();
        assert!(matches!(err, AgoError::Config(_)));
    }

    #[test]
    fn invalid_server_url_rejected() {
        let dir = tempdir().unwrap();
        write(&dir.path().join(".ago.yaml"), "server: http://evil.com\n");
        let err = ProjectPreset::discover(dir.path(), None).unwrap_err();
        assert!(matches!(err, AgoError::InsecureServerUrl));
    }

    #[test]
    fn out_of_range_max_steps_rejected() {
        let dir = tempdir().unwrap();
        write(&dir.path().join(".ago.yaml"), "max_steps: 0\n");
        let err = ProjectPreset::discover(dir.path(), None).unwrap_err();
        assert!(matches!(err, AgoError::Config(_)));
    }

    #[test]
    fn context_overrides_parse() {
        let dir = tempdir().unwrap();
        write(
            &dir.path().join(".ago.yaml"),
            "agent: a\nmodel: m\ncontext:\n  max_file_bytes: 4000\n  exclude_extra:\n    - \"**/foo/**\"\n",
        );
        let (_, preset) = ProjectPreset::discover(dir.path(), None).unwrap().unwrap();
        let ctx = preset.context.as_ref().unwrap();
        assert_eq!(ctx.max_file_bytes, Some(4000));
        assert_eq!(ctx.exclude_extra, vec!["**/foo/**".to_string()]);
    }

    #[test]
    fn shell_policy_parses() {
        let dir = tempdir().unwrap();
        write(
            &dir.path().join(".ago.yaml"),
            "agent: a\nmodel: m\nshell:\n  allow:\n    - npm\n    - tsc\n  deny:\n    - rm\n    - curl\n",
        );
        let (_, preset) = ProjectPreset::discover(dir.path(), None).unwrap().unwrap();
        let shell = preset.shell.as_ref().unwrap();
        assert_eq!(shell.allow, vec!["npm".to_string(), "tsc".to_string()]);
        assert_eq!(shell.deny, vec!["rm".to_string(), "curl".to_string()]);
    }

    #[test]
    fn jail_defaults_true_when_omitted() {
        let dir = tempdir().unwrap();
        write(&dir.path().join(".ago.yaml"), "agent: a\n");
        let (_, preset) = ProjectPreset::discover(dir.path(), None).unwrap().unwrap();
        assert_eq!(preset.jail, None);
        assert!(preset.jail_enabled());
    }

    #[test]
    fn jail_false_opts_out() {
        let dir = tempdir().unwrap();
        write(&dir.path().join(".ago.yaml"), "agent: a\njail: false\n");
        let (_, preset) = ProjectPreset::discover(dir.path(), None).unwrap().unwrap();
        assert_eq!(preset.jail, Some(false));
        assert!(!preset.jail_enabled());
    }

    #[test]
    fn jail_true_parses() {
        let dir = tempdir().unwrap();
        write(&dir.path().join(".ago.yaml"), "jail: true\n");
        let (_, preset) = ProjectPreset::discover(dir.path(), None).unwrap().unwrap();
        assert!(preset.jail_enabled());
    }

    #[test]
    fn jail_image_omitted_is_none() {
        let dir = tempdir().unwrap();
        write(&dir.path().join(".ago.yaml"), "agent: a\n");
        let (_, preset) = ProjectPreset::discover(dir.path(), None).unwrap().unwrap();
        assert_eq!(preset.jail_image, None);
    }

    #[test]
    fn jail_image_parses() {
        let dir = tempdir().unwrap();
        write(
            &dir.path().join(".ago.yaml"),
            "jail: true\njail_image: ghcr.io/acme/ago-jail:latest\n",
        );
        let (_, preset) = ProjectPreset::discover(dir.path(), None).unwrap().unwrap();
        assert_eq!(
            preset.jail_image.as_deref(),
            Some("ghcr.io/acme/ago-jail:latest")
        );
    }

    #[test]
    fn jail_docker_omitted_is_off() {
        let dir = tempdir().unwrap();
        write(&dir.path().join(".ago.yaml"), "agent: a\n");
        let (_, preset) = ProjectPreset::discover(dir.path(), None).unwrap().unwrap();
        assert_eq!(preset.jail_docker, None);
        assert!(!preset.jail_docker_enabled());
    }

    #[test]
    fn jail_docker_true_parses() {
        let dir = tempdir().unwrap();
        write(
            &dir.path().join(".ago.yaml"),
            "jail: true\njail_docker: true\n",
        );
        let (_, preset) = ProjectPreset::discover(dir.path(), None).unwrap().unwrap();
        assert_eq!(preset.jail_docker, Some(true));
        assert!(preset.jail_docker_enabled());
    }

    #[test]
    fn shell_policy_allow_all_parses() {
        let dir = tempdir().unwrap();
        write(
            &dir.path().join(".ago.yaml"),
            "shell:\n  allow_all: true\n  deny:\n    - rm\n    - bash\n",
        );
        let (_, preset) = ProjectPreset::discover(dir.path(), None).unwrap().unwrap();
        let shell = preset.shell.as_ref().unwrap();
        assert!(shell.allow_all);
        assert_eq!(shell.deny, vec!["rm".to_string(), "bash".to_string()]);
    }

    #[test]
    fn shell_policy_allow_all_defaults_false() {
        let dir = tempdir().unwrap();
        write(
            &dir.path().join(".ago.yaml"),
            "shell:\n  allow:\n    - npm\n",
        );
        let (_, preset) = ProjectPreset::discover(dir.path(), None).unwrap().unwrap();
        assert!(!preset.shell.as_ref().unwrap().allow_all);
    }

    #[test]
    fn shell_policy_defaults_empty_lists() {
        // `shell:` with only one side present — the other defaults to empty.
        let dir = tempdir().unwrap();
        write(&dir.path().join(".ago.yaml"), "shell:\n  deny:\n    - rm\n");
        let (_, preset) = ProjectPreset::discover(dir.path(), None).unwrap().unwrap();
        let shell = preset.shell.as_ref().unwrap();
        assert!(shell.allow.is_empty());
        assert_eq!(shell.deny, vec!["rm".to_string()]);
    }

    #[test]
    fn shell_policy_unknown_key_rejected() {
        let dir = tempdir().unwrap();
        write(&dir.path().join(".ago.yaml"), "shell:\n  bogus: 1\n");
        let err = ProjectPreset::discover(dir.path(), None).unwrap_err();
        assert!(matches!(err, AgoError::Config(_)));
    }

    #[test]
    fn context_out_of_range_rejected() {
        let dir = tempdir().unwrap();
        write(
            &dir.path().join(".ago.yaml"),
            "context:\n  max_file_bytes: 5000000\n",
        );
        let err = ProjectPreset::discover(dir.path(), None).unwrap_err();
        assert!(matches!(err, AgoError::Config(_)));
    }

    #[test]
    fn https_server_accepted() {
        let dir = tempdir().unwrap();
        write(&dir.path().join(".ago.yaml"), "server: https://orch.io\n");
        let (_, preset) = ProjectPreset::discover(dir.path(), None).unwrap().unwrap();
        assert_eq!(preset.server.as_deref(), Some("https://orch.io"));
    }
}
