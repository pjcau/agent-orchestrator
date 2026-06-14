//! Client-side thrash guard for `--client-tools` turns.
//!
//! In client-tools mode the agent loop runs on the SERVER; the CLI only
//! shuttles tool calls/results over the WebSocket. The server has its own
//! loop/failure guards, but they can leak: in multi-agent fan-out `max_steps`
//! is per-agent (so a team-lead run easily exceeds it), and the server loop
//! detector hashes the *exact* tool call, so a shell command that varies by a
//! byte slips past. The result is the failure mode we actually observed — a
//! turn that ran ~86 steps re-running near-identical failing commands while the
//! per-step latency climbed with the context.
//!
//! This guard sits at the one choke point the CLI fully controls — the
//! tool-execution boundary — and limits the *local* damage without needing any
//! new protocol frame:
//!
//! 1. **Loop guard (`AGO_LOOP_GUARD`, on by default).** If the same tool call
//!    (name + canonical args) has *failed* `threshold` times within the recent
//!    `window`, the next identical call is NOT executed: it is short-circuited
//!    with a synthetic `loop_blocked` error that nudges the server to change
//!    approach. We count *failures only*, so legitimate polling (e.g. waiting
//!    for `docker compose ps` to report healthy) — which returns success — never
//!    trips it. This is the OpenHands "repeating action + error" lesson:
//!    blocking on bare repetition kills agents that are correctly waiting.
//! 2. **Consecutive-failure breaker (`AGO_FAIL_WARN` / `AGO_FAIL_HALT`).** A
//!    global streak counter over every tool result. At `warn` it prints a
//!    prominent notice; at `halt` (opt-in) it stops running further tools this
//!    turn.
//! 3. **Hard step/cost caps (`AGO_TURN_MAX_STEPS` / `AGO_TURN_MAX_USD`,
//!    opt-in).** Fed from each `Step` frame's cumulative index/cost. Once a cap
//!    trips, the turn is *halted*: every further tool call is refused so no more
//!    commands run on your machine. This is the "the system, not the agent,
//!    guarantees termination" backstop.
//!
//! All thresholds are environment-tunable; the loop guard is the only one on by
//! default because it is precise (failures-only) and low-false-positive.

use std::collections::VecDeque;

use sha2::{Digest, Sha256};

/// Resolved guard configuration. Built once per session from the environment.
#[derive(Debug, Clone, PartialEq)]
pub struct GuardConfig {
    /// Block identical *failing* calls. Default on.
    pub loop_guard: bool,
    /// How many failing repeats within the window trigger a block.
    pub loop_threshold: u32,
    /// Size of the recent-call window the loop guard looks back over.
    pub loop_window: usize,
    /// Consecutive failures that print a warning (0 = off).
    pub fail_warn: u32,
    /// Consecutive failures that halt the turn (0 = off).
    pub fail_halt: u32,
    /// Step index that halts the turn (0 = off).
    pub max_steps: u64,
    /// Cumulative turn cost (USD) that halts the turn (0 = off).
    pub max_usd: f64,
}

impl Default for GuardConfig {
    fn default() -> Self {
        Self {
            loop_guard: true,
            loop_threshold: 3,
            loop_window: 10,
            fail_warn: 8,
            fail_halt: 0,
            max_steps: 0,
            max_usd: 0.0,
        }
    }
}

impl GuardConfig {
    /// Read overrides from the environment, falling back to [`Default`].
    pub fn from_env() -> Self {
        let d = Self::default();
        Self {
            loop_guard: env_bool("AGO_LOOP_GUARD", d.loop_guard),
            loop_threshold: env_u32("AGO_LOOP_THRESHOLD", d.loop_threshold).max(1),
            loop_window: env_u32("AGO_LOOP_WINDOW", d.loop_window as u32).max(1) as usize,
            fail_warn: env_u32("AGO_FAIL_WARN", d.fail_warn),
            fail_halt: env_u32("AGO_FAIL_HALT", d.fail_halt),
            max_steps: env_u64("AGO_TURN_MAX_STEPS", d.max_steps),
            max_usd: env_f64("AGO_TURN_MAX_USD", d.max_usd),
        }
    }
}

/// Decision returned by [`TurnGuard::before`] for a pending tool call.
#[derive(Debug, Clone, PartialEq)]
pub enum Decision {
    /// Run the tool normally.
    Allow,
    /// Do not run the tool; return this synthetic error reason instead.
    Block(String),
}

/// Per-turn thrash detector. Cheap; reset between turns via [`reset`].
///
/// [`reset`]: TurnGuard::reset
pub struct TurnGuard {
    cfg: GuardConfig,
    /// Recent calls as `(hash, was_error)`, capped at `cfg.loop_window`.
    recent: VecDeque<(u64, bool)>,
    /// Global consecutive-failure streak across all tools.
    consec_fail: u32,
    /// Whether the `fail_warn` notice has already fired this streak.
    warned: bool,
    /// `Some(reason)` once the turn is halted; refuses all further tools.
    halted: Option<String>,
}

impl TurnGuard {
    pub fn new(cfg: GuardConfig) -> Self {
        Self {
            cfg,
            recent: VecDeque::new(),
            consec_fail: 0,
            warned: false,
            halted: None,
        }
    }

    /// Stable 64-bit hash of `(name, canonical-args)`. `serde_json` serialises
    /// object keys in sorted order by default (no `preserve_order` feature), so
    /// `to_string` is already canonical for our argument shapes.
    fn hash(name: &str, args: &serde_json::Value) -> u64 {
        let mut h = Sha256::new();
        h.update(name.as_bytes());
        h.update([0u8]);
        h.update(serde_json::to_string(args).unwrap_or_default().as_bytes());
        let digest = h.finalize();
        u64::from_be_bytes(digest[..8].try_into().expect("sha256 >= 8 bytes"))
    }

    /// Call BEFORE executing a tool. Returns whether to run it.
    pub fn before(&mut self, name: &str, args: &serde_json::Value) -> Decision {
        if let Some(reason) = &self.halted {
            return Decision::Block(reason.clone());
        }
        if !self.cfg.loop_guard {
            return Decision::Allow;
        }
        let h = Self::hash(name, args);
        let failures = self
            .recent
            .iter()
            .filter(|(hh, was_err)| *hh == h && *was_err)
            .count() as u32;
        if failures >= self.cfg.loop_threshold {
            return Decision::Block(format!(
                "loop_blocked: this exact `{name}` call has already failed {failures}× recently \
                 — change approach instead of retrying the same command \
                 (disable with AGO_LOOP_GUARD=false)"
            ));
        }
        Decision::Allow
    }

    /// Call AFTER a tool result (executed or blocked). Records the outcome and
    /// returns a one-line warning to print, if any.
    pub fn after(
        &mut self,
        name: &str,
        args: &serde_json::Value,
        is_error: bool,
    ) -> Option<String> {
        let h = Self::hash(name, args);
        self.recent.push_back((h, is_error));
        while self.recent.len() > self.cfg.loop_window {
            self.recent.pop_front();
        }

        if is_error {
            self.consec_fail += 1;
        } else {
            self.consec_fail = 0;
            self.warned = false;
            return None;
        }

        // Halt takes precedence over the (lighter) warning.
        if self.cfg.fail_halt > 0 && self.consec_fail >= self.cfg.fail_halt && self.halted.is_none()
        {
            let n = self.consec_fail;
            return Some(self.halt(format!(
                "{n} tool calls failed in a row — halting the turn to stop the thrash"
            )));
        }
        if self.cfg.fail_warn > 0 && self.consec_fail == self.cfg.fail_warn && !self.warned {
            self.warned = true;
            let n = self.consec_fail;
            return Some(format!(
                "⚠ {n} tool calls have failed in a row — the agent may be stuck; \
                 press Ctrl-C or type :quit to stop"
            ));
        }
        None
    }

    /// Call on each `Step` frame with the cumulative index and turn cost.
    /// Returns a message the first time a hard cap trips.
    pub fn on_step(&mut self, index: u64, cost_usd: f64) -> Option<String> {
        if self.halted.is_some() {
            return None;
        }
        if self.cfg.max_steps > 0 && index >= self.cfg.max_steps {
            return Some(self.halt(format!(
                "step cap reached ({index} ≥ AGO_TURN_MAX_STEPS={})",
                self.cfg.max_steps
            )));
        }
        if self.cfg.max_usd > 0.0 && cost_usd >= self.cfg.max_usd {
            return Some(self.halt(format!(
                "cost cap reached (${cost_usd:.4} ≥ AGO_TURN_MAX_USD=${:.4})",
                self.cfg.max_usd
            )));
        }
        None
    }

    /// Mark the turn halted and return the user-facing message.
    fn halt(&mut self, why: String) -> String {
        let msg = format!(
            "⊘ turn halted by client: {why}. No further tools will run this turn — \
             press Ctrl-C or type :quit (resume later with --resume)."
        );
        self.halted = Some(format!("turn_halted_by_client: {why}"));
        msg
    }

    /// Whether the turn is currently halted.
    pub fn is_halted(&self) -> bool {
        self.halted.is_some()
    }

    /// Reset all per-turn state. Call on `TurnEnd`.
    pub fn reset(&mut self) {
        self.recent.clear();
        self.consec_fail = 0;
        self.warned = false;
        self.halted = None;
    }
}

fn env_bool(key: &str, default: bool) -> bool {
    match std::env::var(key) {
        Ok(v) => !matches!(
            v.trim().to_ascii_lowercase().as_str(),
            "0" | "false" | "no" | "off"
        ),
        Err(_) => default,
    }
}

fn env_u32(key: &str, default: u32) -> u32 {
    std::env::var(key)
        .ok()
        .and_then(|v| v.trim().parse::<u32>().ok())
        .unwrap_or(default)
}

fn env_u64(key: &str, default: u64) -> u64 {
    std::env::var(key)
        .ok()
        .and_then(|v| v.trim().parse::<u64>().ok())
        .unwrap_or(default)
}

fn env_f64(key: &str, default: f64) -> f64 {
    std::env::var(key)
        .ok()
        .and_then(|v| v.trim().parse::<f64>().ok())
        .filter(|v| v.is_finite() && *v >= 0.0)
        .unwrap_or(default)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn cfg() -> GuardConfig {
        GuardConfig {
            loop_guard: true,
            loop_threshold: 3,
            loop_window: 10,
            fail_warn: 0,
            fail_halt: 0,
            max_steps: 0,
            max_usd: 0.0,
        }
    }

    #[test]
    fn allows_until_threshold_then_blocks_repeated_failures() {
        let mut g = TurnGuard::new(cfg());
        let args = json!({"argv": ["docker", "compose", "up"]});
        // 3 failures recorded → the 4th identical call is blocked.
        for _ in 0..3 {
            assert_eq!(g.before("shell_exec", &args), Decision::Allow);
            g.after("shell_exec", &args, true);
        }
        match g.before("shell_exec", &args) {
            Decision::Block(r) => assert!(r.starts_with("loop_blocked"), "got: {r}"),
            Decision::Allow => panic!("expected block after 3 failures"),
        }
    }

    #[test]
    fn successful_polling_never_blocks() {
        let mut g = TurnGuard::new(cfg());
        let args = json!({"argv": ["docker", "compose", "ps"]});
        // 20 identical *successful* calls (legit polling) must never trip.
        for _ in 0..20 {
            assert_eq!(g.before("shell_exec", &args), Decision::Allow);
            g.after("shell_exec", &args, false);
        }
    }

    #[test]
    fn different_args_tracked_separately() {
        let mut g = TurnGuard::new(cfg());
        let a = json!({"argv": ["a"]});
        let b = json!({"argv": ["b"]});
        for _ in 0..3 {
            g.before("shell_exec", &a);
            g.after("shell_exec", &a, true);
        }
        // `a` is now blocked, but a fresh `b` is still allowed.
        assert!(matches!(g.before("shell_exec", &a), Decision::Block(_)));
        assert_eq!(g.before("shell_exec", &b), Decision::Allow);
    }

    #[test]
    fn loop_guard_off_never_blocks() {
        let mut c = cfg();
        c.loop_guard = false;
        let mut g = TurnGuard::new(c);
        let args = json!({"argv": ["x"]});
        for _ in 0..10 {
            assert_eq!(g.before("shell_exec", &args), Decision::Allow);
            g.after("shell_exec", &args, true);
        }
    }

    #[test]
    fn consecutive_failures_warn_once_then_reset_on_success() {
        let mut c = cfg();
        c.loop_guard = false; // isolate the breaker from the loop guard
        c.fail_warn = 3;
        let mut g = TurnGuard::new(c);
        let a = json!({"argv": ["1"]});
        assert!(g.after("t", &a, true).is_none());
        assert!(g.after("t", &a, true).is_none());
        let w = g.after("t", &a, true);
        assert!(w.is_some_and(|m| m.contains("failed in a row")));
        // Already warned → no repeat at 4.
        assert!(g.after("t", &a, true).is_none());
        // Success resets; the streak can warn again later.
        assert!(g.after("t", &a, false).is_none());
        assert_eq!(g.consec_fail, 0);
    }

    #[test]
    fn fail_halt_stops_all_further_tools() {
        let mut c = cfg();
        c.loop_guard = false;
        c.fail_halt = 2;
        let mut g = TurnGuard::new(c);
        let a = json!({"a": 1});
        g.after("t", &a, true);
        let msg = g.after("t", &a, true);
        assert!(msg.is_some_and(|m| m.contains("halted")));
        assert!(g.is_halted());
        // Once halted, even a brand-new tool call is refused.
        assert!(matches!(
            g.before("other", &json!({"z": 9})),
            Decision::Block(_)
        ));
    }

    #[test]
    fn step_cap_halts_turn() {
        let mut c = cfg();
        c.max_steps = 40;
        let mut g = TurnGuard::new(c);
        assert!(g.on_step(39, 0.0).is_none());
        assert!(g.on_step(40, 0.0).is_some_and(|m| m.contains("step cap")));
        assert!(g.is_halted());
        // Subsequent steps do not re-fire.
        assert!(g.on_step(41, 0.0).is_none());
    }

    #[test]
    fn cost_cap_halts_turn() {
        let mut c = cfg();
        c.max_usd = 0.50;
        let mut g = TurnGuard::new(c);
        assert!(g.on_step(5, 0.49).is_none());
        assert!(g.on_step(6, 0.51).is_some_and(|m| m.contains("cost cap")));
        assert!(g.is_halted());
    }

    #[test]
    fn reset_clears_halt_and_streak() {
        let mut c = cfg();
        c.max_steps = 10;
        let mut g = TurnGuard::new(c);
        g.on_step(10, 0.0);
        assert!(g.is_halted());
        g.reset();
        assert!(!g.is_halted());
        assert_eq!(g.before("t", &json!({})), Decision::Allow);
    }
}
