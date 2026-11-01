//! Command-line surface. Kept tiny on purpose.

use clap::{Parser, Subcommand};

#[derive(Parser)]
#[command(
    name = "nizho-agent",
    version,
    about = "On-device AI agent for Nizho (local LLM, approval-gated, audited)."
)]
pub struct Cli {
    /// Path to a config TOML (defaults to ~/.config/nizho-agent/config.toml, else built-in defaults).
    #[arg(long, global = true)]
    pub config: Option<String>,

    #[command(subcommand)]
    pub command: Command,
}

#[derive(Subcommand)]
pub enum Command {
    /// Ask the agent a question about the system.
    Ask {
        /// The request, e.g. "is DNS-over-TLS on?"
        prompt: String,
    },
    /// Audit-log commands.
    Log {
        #[command(subcommand)]
        cmd: LogCmd,
    },
}

#[derive(Subcommand)]
pub enum LogCmd {
    /// Verify the audit hash-chain is intact.
    Verify,
}
