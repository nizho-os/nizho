//! nizho-agent binary — thin shell over the `nizho_agent` library.

use clap::Parser;

use nizho_agent::approval::TerminalApprover;
use nizho_agent::backend::dbus::DbusBackend;
use nizho_agent::cli::{Cli, Command, LogCmd};
use nizho_agent::config::Config;
use nizho_agent::ollama::OllamaClient;
use nizho_agent::{agent, audit};

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    let cli = Cli::parse();
    let cfg = Config::load(cli.config.as_deref())?;

    match cli.command {
        Command::Ask { prompt } => {
            let llm = OllamaClient::new(&cfg)?;
            let backend = DbusBackend::new();
            let approver = TerminalApprover;
            let outcome = agent::run(&llm, &backend, &approver, &cfg, &prompt).await?;
            match outcome.final_answer {
                Some(answer) => println!("{answer}"),
                None => println!(
                    "(no final answer — stopped after {} step(s))",
                    outcome.steps_used
                ),
            }
        }
        Command::Log { cmd } => match cmd {
            LogCmd::Verify => println!("{}", audit::verify(&cfg.audit_path)?),
        },
    }
    Ok(())
}
