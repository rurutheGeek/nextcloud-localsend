# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Optional receiving: the relay can act as a LocalSend receiver and store
  incoming files in a Nextcloud user's folder. It is off by default and is
  turned on by an administrator (for 15 minutes, 1 hour, 1 day or until turned
  off) under *Administration settings → LocalSend Share*.
- Every transfer needs the PIN: `prepare-upload` without the right `?pin=`
  answers 401, so LocalSend apps ask the sender for it. Receiving cannot be
  turned on without a PIN, and repeated wrong PINs lock the address out for
  five minutes.
- Settings for the target user and folder, the maximum file size, the number of
  transfers at the same time, and a log of received files.
- Relay settings `LOCALSEND_RECEIVE_NEXTCLOUD_URL`, `LOCALSEND_RECEIVE_PORT`
  and `LOCALSEND_RECEIVE_INSECURE`. Without the URL the relay behaves as before.

## [1.0.3] - 2026-09-30

### Fixed

- Ship `l10n/ja.js` so the Files action and its dialog are shown in Japanese;
  until now only the server-side messages were translated.
- The release archive no longer contains an empty `build/` directory.

## [1.0.2] - 2026-09-26

### Fixed

- The device list request now allows local addresses like the send request, so
  the action works on instances without `allow_local_remote_servers`.
- Send the file size explicitly (`Content-Length`), so files on storages that
  cannot report a seekable stream size still send reliably.
- Show the relay's error message instead of a generic one.
- Compare the relay token with `hmac.compare_digest` like the print relay.

### Documentation

- Note that `POST /api/localsend/v2/register` is unauthenticated by protocol
  design and that the relay belongs on a trusted LAN.

## [1.0.1] - 2026-09-26

### Added

- HTTP legacy discovery in the relay: it now probes `LOCALSEND_SEND_SCAN`
  (default: the local /24) outbound with `POST /api/localsend/v2/register`, so
  devices are found even when a firewall blocks their reply to the relay port.

## [1.0.0] - 2026-09-26

### Added

- `localsend_share` Nextcloud app: a **Send via LocalSend** action in the Files
  app with a device picker, plus an admin settings panel for the relay URL and
  token.
- LocalSend relay (`server/`): standard-library Python service implementing the
  sending half of LocalSend protocol v2 (discover, prepare-upload, upload),
  with Dockerfile, host-network `compose.yaml` and bearer-token authentication.
- Japanese translations (`l10n/ja.json`).
- CI (bundle build, PHP lint, relay tests, container build) and a signed
  release workflow for the Nextcloud App Store.
