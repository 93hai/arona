# Arona 3.0 — Experimental source release status

**3.0.0a1 — Experimental Core**. First public source release. **PUBLIC SOURCE: READY FOR OWNER PUBLICATION APPROVAL**. **PRODUCTION: NOT READY**. Publication requires the owner's final approval; source readiness is separate from production and historical account-security assurance.

| Item | Status / scope |
| --- | --- |
| Runtime baseline | COMPLETE CANDIDATE / PASS WITH KNOWN ISSUES |
| Previous Windows clean wheel / offline workflow | PASS on Python 3.14.6; previous audit, not a fresh full-suite result |
| Previous Windows full regression | 579 passed / 1 failed / exit 1; the Git-dependent test failed in a snapshot without Git |
| Current focused Git-aware security / packaging checks | 92 passed / 0 failed / 0 skipped / exit 0 on 2026-10-11; full suite is not rerun |
| Historical Debian 12 regression | 578 passed / 2 skipped; previous baseline evidence only |
| MIT / attribution | Approved by the owner: MIT, copyright 2026 93hai; dependency licenses remain separate |
| Third-party dependencies | Separate upstream licenses preserved in THIRD_PARTY_NOTICES.md; no vendored assets identified |
| Provenance / contributor review | No concrete third-party rights conflict identified; based on source review and owner declarations, not independent legal certification |
| Historical Gemini / Mistral keys | OWNER-REPORTED REMEDIATED: owner reports old keys deleted; not PROVIDER-CONFIRMED REVOKED |
| Historical Discord bot application | OWNER-REPORTED REMEDIATED: owner reports the old application deleted and replaced; not PROVIDER-CONFIRMED REVOKED |
| Git history | Fresh history from the reviewed clean snapshot only; original private history is excluded |
| Private vulnerability reporting channel | NOT CONFIGURED / NOT VERIFIED; recommended before receiving sensitive reports |
| Public source release | READY FOR OWNER PUBLICATION APPROVAL; no automatic publication |
| Production readiness | NOT READY |

Validation uses bounded offline fixtures. No live provider, long soak or hardware power-loss acceptance is claimed. Core READY does not provide a chat transport or API. Windows instructions cover development and maintenance; Linux production needs independent validation and non-root setup. Historical systemd tests with added network isolation do not establish that the shipped unit defaults to that isolation.

The MIT direction and public identity are approved by the owner. No original Git history or private evidence archive belongs in a public release. The clean source review does not prove provider-side credential revocation. Owner-reported screenshots cover selected account/project/workspace views; the reported Deleted User view does not independently establish every old Discord token's status. Remaining historical account uncertainty must stay disclosed without treating a clean public source snapshot as production-ready. See [snapshot scope](docs/public_snapshot.md) and [security contact](SECURITY.md).
