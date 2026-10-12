# Public staging snapshot scope

This experimental Arona 3.0 staging derives from the validated clean candidate. Runtime source, SQL migrations, prompt resources, dependency locks and tests are preserved byte-for-byte. Only public documentation, ignore/packaging metadata and license/notice files are updated. No feature is added.

A reviewed file allowlist is used to copy files and stage a fresh local Git repository. Original Git history, remotes, private keys, actual env files, databases, personal data, logs, caches, build output, internal planning documents and test evidence archives are excluded. The internal 3.0–4.0 planning document is not shipped; future ideas are not implemented capabilities.

`.env.example` contains empty provider-key templates. Credential markers and fake Windows paths in tests are synthetic fixtures, not personal credentials or paths. One historical Discord file remains only because an unchanged security regression inspects its AST to assert an empty credential default. It is not imported, executed or installed as Arona runtime.

The public identity is 93hai. The proposed MIT grant applies only where the maintainer has the right to license the source. Third-party permissions and prior genuine-credential revocation must be confirmed before publication; see [status](../RELEASE_STATUS.md). Neither AI assistance nor absence of a copied-code marker proves all rights are cleared.

The local repository has no original commits or remote. It is prepared for review, not published. External audit logs, hash manifests, allowlist and release artifacts are handed to the owner separately and do not belong in this public source tree. Git-aware regression requires tracked files; it is retained unchanged.
