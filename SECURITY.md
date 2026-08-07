# Security policy

Do not open a public issue containing API keys, transcript text, audio, local
configuration, or diagnostic logs that have not been reviewed.

For a suspected vulnerability, contact the repository owner privately through
the security-advisory form on GitHub. Include the affected version, operating
system, reproduction steps, and the expected security boundary.

WhisperTray treats these as release blockers:

- audio leaving the Privacy profile;
- credentials appearing in configuration, logs, diagnostics, or Git history;
- transcript text appearing in technical logs;
- a cloud fallback that was not explicitly enabled;
- an installer that writes outside its declared scope or leaves owned files on removal.

Only the latest released version receives fixes. Pre-release builds are for
testing and should not be used with sensitive audio.
