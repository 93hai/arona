# Security and contact

Arona 3.0 (`3.0.0a1`) is an experimental source-release candidate, not a production service. There is no supported stable release or vulnerability response-time commitment yet.

Public maintainer identity: [93hai](https://github.com/93hai). This profile identifies the project maintainer; it is not a private vulnerability submission endpoint. No private security inbox has been verified or supplied. The maintainer should configure GitHub private vulnerability reporting or document another verified private channel before receiving sensitive reports. Do not post credentials, private data or exploit details in public issues or comments.

For routine non-sensitive setup or documentation questions, use the eventual public repository's issue tracker once it exists. No public repository or issue URL is promised by this staging snapshot.

Keep actual API keys in the process environment or an explicitly selected local development env file outside shared source. Never commit env files, databases, backups, logs or user data. Production configuration rejects dotenv and requires Linux/non-root operation; Windows instructions cover development and offline maintenance. Arona 3.0 implements no public HTTP/WebSocket listener or Discord transport. Legacy reference code is not a supported executable.

Secret scans are heuristic. Prior private-history credential-like findings require owner review and revocation/rotation if genuine; clean public files cannot establish that revocation happened. No old Git history is included. See [release status](RELEASE_STATUS.md) and [security design](docs/SECURITY.md).
