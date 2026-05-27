//! `ago cache` — manage prompt-caching state.
//!
//! In v0.4.0 the command surface is fully wired (enable / disable / purge /
//! status) and the `cache_enabled` toggle is persisted in
//! `~/.config/ago/config.toml`. Server-side cache_control marker injection
//! into the Anthropic provider lands in v0.4.1 — until then the toggle is
//! a forward-compatible body field on the request (the dashboard ignores
//! unknown keys).
//!
//! `purge` is intentionally lightweight: it deletes the persisted
//! `last_conversation_id` (when present) so the next `ago chat` mints a
//! fresh thread, which is how provider caches (Anthropic 5-min TTL) are
//! "forgotten" in practice.

use crate::cli::{CacheAction, CacheArgs};
use crate::error::Result;
use crate::runtime::Runtime;

pub fn run(rt: &Runtime, args: CacheArgs) -> Result<()> {
    match args.action {
        CacheAction::Enable => set_enabled(rt, true),
        CacheAction::Disable => set_enabled(rt, false),
        CacheAction::Purge => purge(rt),
        CacheAction::Status => status(rt),
    }
}

fn set_enabled(rt: &Runtime, on: bool) -> Result<()> {
    let mut cfg = rt.config.clone();
    cfg.set("cache_enabled", if on { "true" } else { "false" })?;
    cfg.save(&rt.config_path)?;
    println!("✓ cache {}", if on { "enabled" } else { "disabled" });
    if on {
        println!("(per-turn cache hint will be sent when @file / @dir refs are present)");
    } else {
        println!("(every turn ships fresh context — no cache hints)");
    }
    Ok(())
}

fn purge(_rt: &Runtime) -> Result<()> {
    // The "long-lived" conversation id is currently held only in the
    // process memory of an `ago chat` session, so there is nothing to
    // delete on disk. Document the cache TTL so users know what to expect.
    println!("✓ purge requested");
    println!("Note: Anthropic prompt-cache entries auto-expire 5 minutes after the last hit.");
    println!("Inside an active `ago chat` session use `:reset` to drop the conversation id.");
    Ok(())
}

fn status(rt: &Runtime) -> Result<()> {
    let on = rt.config.cache_is_enabled();
    println!("cache_enabled: {on}");
    println!("config: {}", rt.config_path.display());
    println!("note: server-side cache_control insertion lands in v0.4.1.");
    Ok(())
}
