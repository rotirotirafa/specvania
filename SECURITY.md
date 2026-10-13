# Security policy

## Supported versions

Security fixes target the latest patch release in the 0.3.x series. Upgrade to
that patch before checking whether a reported problem still occurs.

| Version | Security support |
|---|---|
| 0.3.x | Supported |
| < 0.3 | Not supported |

## Report privately

Use **GitHub private vulnerability reporting**: on the repository, choose
**Security → Report a vulnerability** (GitHub may label the tab “Security and
quality”), or open the
[private report form](https://github.com/rotirotirafa/specvania/security/advisories/new).
Do not open a public issue or pull request with vulnerability details.

If private vulnerability reporting is not yet enabled, use a private contact
method listed on the maintainer's [GitHub profile, @rotirotirafa](https://github.com/rotirotirafa).
If none is listed, ask the maintainer for a private reporting channel without
disclosing the vulnerability; keep all details private until a channel is available.
GitHub describes the process in its
[private reporting guide](https://docs.github.com/en/code-security/how-tos/report-and-fix-vulnerabilities/report-privately).

## What to include

- The output of `specvania --version`, desktop app version if relevant, operating
  system and architecture.
- The affected component (CLI, studio, desktop window, browser tab or ledger),
  configuration with secrets removed, and the suspected impact.
- Minimal steps to reproduce using fake data, expected and actual behavior, and
  a proof of concept if available.
- Relevant redacted logs or screenshots, any workaround, and whether you can
  reproduce on the latest supported patch.

Do not include live API keys, passwords, credentials, view/control tokens,
private repository contents or personal data. Replace secrets with placeholders
even in a private report. Do not attach `.env` files or unredacted studio
handshakes, URLs, logs or recordings. Use fake credentials in reproductions.

## Response and disclosure

The maintainer aims to acknowledge reports within **7 days** and provide an
initial assessment or request for more information within **14 days**. These
windows are **best effort**, not a guarantee or a service-level agreement.
If no response arrives, follow up in the same private report.

We will discuss impact, a fix or mitigation, and coordinated disclosure through
the private report. Fix timing depends on severity and available maintainer
time; there is no promised release deadline.
