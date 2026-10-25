# 0008. Holdout unseal flag

- Status: Accepted
- Date: 2026-10-25

## Context

In research mode the point-in-time loader refuses any date after 2022-12-31 unless an unseal flag is set (plan §4, decision 0002). The plan does not name the flag. Without a fixed name, the agent hooks can only block every command that mentions the word "unseal", which also blocks commit messages, test names and searches of the documentation. A fixed name lets the loader, the unseal script and the hooks refer to one switch, and lets the hooks block setting it rather than talking about it.

## Decision

- The unseal flag is the environment variable `TIFERET_UNSEAL_HOLDOUT`. Research mode treats the holdout as unsealed only when it is set to `1`; any other value, or no value, keeps it sealed.
- There is no command-line or configuration-file equivalent. The flag is not written to `.env`, to any file under `config/`, to CI workflows or to committed code, apart from the loader that reads it and the unseal script.
- Only the owner sets it, once per release, through the unseal script, which logs the event (§4).
- Agents never set, export or pass it. `vault_guard.py` blocks Bash commands that do, in shell, PowerShell, Python or container syntax. Commands that only read or mention it are allowed.

## Consequences

- The loader and the unseal script read one variable, so their behaviour can be tested with the variable set and unset.
- The hook blocks the act of unsealing instead of the vocabulary, so ordinary work that mentions the holdout or the flag is not interrupted.
- The hook matches known syntaxes only; a command that builds the name at run time is not caught. The loader's refusal and the holdout's encryption remain the binding protections (§8.2).
