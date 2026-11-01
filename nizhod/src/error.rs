use std::fmt;

#[derive(Debug)]
pub enum NizhodError {
    Enforce(String),
    Io(std::io::Error),
    Spawn(tokio::task::JoinError),
}

impl fmt::Display for NizhodError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Enforce(s) => write!(f, "enforce: {s}"),
            Self::Io(e)      => write!(f, "io: {e}"),
            Self::Spawn(e)   => write!(f, "spawn: {e}"),
        }
    }
}

impl std::error::Error for NizhodError {}

impl From<std::io::Error> for NizhodError {
    fn from(e: std::io::Error) -> Self { Self::Io(e) }
}

impl From<tokio::task::JoinError> for NizhodError {
    fn from(e: tokio::task::JoinError) -> Self { Self::Spawn(e) }
}
