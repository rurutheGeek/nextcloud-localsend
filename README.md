# LocalSend Share for Nextcloud

Send files from the Nextcloud Files app to [LocalSend](https://localsend.org/)
devices on the same LAN.

This repository contains two parts that belong together:

| Directory | Part |
| --- | --- |
| repository root | the **localsend_share** Nextcloud app (PHP + a bundled file action) |
| [`server/`](server/) | the **LocalSend relay** (Python standard library, Docker image) |

A short Japanese summary is at the end of this file.

## How it works

```
Files app ──"Send via LocalSend"──▶ localsend_share (Nextcloud app)
                                        │  GET /devices, POST /send
                                        ▼
                                  localsend relay ──▶ LocalSend app on the device
```

The relay implements the sending half of LocalSend protocol v2. It announces
itself over UDP multicast/broadcast (`224.0.0.167:53317`) and, because
firewalls often block the peers' reply to our HTTP port, it also probes every
address of `LOCALSEND_SEND_SCAN` (default: the local /24) outbound with
`POST /api/localsend/v2/register` — the protocol's built-in fallback. The
devices that answer are remembered, and the user picks one in the Files app.
Only the selected file is transmitted.

## Quick start

### 1. Start the relay

The relay must run on the same LAN (L2 network) as the receiving devices, so
`compose.yaml` uses host networking.

```bash
cd server
cp .env.example .env
# edit .env: LOCALSEND_SEND_TOKEN and LOCALSEND_SEND_FINGERPRINT (openssl rand -hex 32)
docker compose up -d --build
curl http://localhost:53200/healthz
```

Or run it directly:

```bash
LOCALSEND_SEND_TOKEN=change-me LOCALSEND_SEND_FINGERPRINT="$(openssl rand -hex 32)" \
  python3 server/localsend_send.py
```

The HTTP API can be used without Nextcloud as well:

```bash
curl -H "Authorization: Bearer $LOCALSEND_SEND_TOKEN" http://relay.example.net:53200/devices
```

### 2. Install the app

From the [Nextcloud App Store](https://apps.nextcloud.com/apps/localsend_share)
(search for *LocalSend Share*), or manually:

```bash
tar -xzf localsend_share.tar.gz -C /var/www/html/custom_apps/
occ app:enable localsend_share
```

The archive is published on the [releases page](../../releases) of this
repository for every version.

### 3. Configure

As an administrator open *Administration settings → LocalSend Share* and set:

- **Relay URL** — e.g. `http://192.168.1.10:53200`
- **Relay token** — the same value as `LOCALSEND_SEND_TOKEN` in the relay's `.env`

Saving runs a health check against the relay and reports the result on the
same page. No `occ config:app:set` needed.

## Relay configuration

| Variable | Required | Default | Meaning |
| --- | --- | --- | --- |
| `LOCALSEND_SEND_TOKEN` | yes | – | shared secret (`Authorization: Bearer …`) |
| `LOCALSEND_SEND_FINGERPRINT` | yes | – | stable sender fingerprint (generate once) |
| `LOCALSEND_SEND_PORT` | no | `53200` | listen port |
| `LOCALSEND_SEND_ALIAS` | no | `nextcloud` | sender name on the receiving device |
| `LOCALSEND_SEND_TIMEOUT` | no | `3` | discovery wait in seconds |
| `LOCALSEND_SEND_SCAN` | no | local /24 | CIDR probed outbound for devices; `none` disables the scan |
| `LOCALSEND_SEND_MAX_BYTES` | no | `2147483648` | maximum file size (2 GiB) |

### API

- `GET /healthz` — `200 {"status": "ok"}`
- `GET /devices` — discover devices; answers `{"devices": [{alias, fingerprint, address, port, protocol, deviceType}]}`
- `POST /send` — body is the file
  - `Authorization: Bearer <token>` (required)
  - `X-Send-To`: target fingerprint from `/devices`
  - `X-Send-Filename`: URL-encoded filename
  - `X-Send-User`: optional display name prefixed to the filename
  - answers `{"status": "sent", "device": …, "session": …, "bytes": …}`; a
    device that declines the transfer answers `{"status": "skipped"}`
- `POST /api/localsend/v2/register` — discovery replies from LocalSend devices
  (no token; only used to remember devices)

### Troubleshooting

- **No devices found** — the relay must reach the devices. It probes
  `LOCALSEND_SEND_SCAN` (local /24 by default) outbound, so inbound firewall
  rules on the server do not matter. If your devices are on another subnet
  (e.g. a separate Wi-Fi VLAN), set `LOCALSEND_SEND_SCAN` to that range
  (comma-separated ranges are not supported — run one relay per subnet or use
  a wider CIDR). With Docker, host networking (`network_mode: host`, the
  default in `compose.yaml`) is required for the multicast part; the scan
  works from a bridge too as long as the LAN is routable.
- **The device never shows the confirmation** — open the LocalSend app on the
  device and keep the screen on while sending.
- **Self-signed certificates** — like LocalSend itself, the relay does not
  verify TLS certificates of other LocalSend devices; peers are identified by
  fingerprint instead.

## Security notes

- The relay is designed for a trusted LAN. Put a TLS terminator in front of it
  if it must cross a network boundary, and keep the token secret.
- Any client that knows the token can list devices and send files; the app adds
  Nextcloud's own permission checks (a user can only send files they can read).

## Development

```bash
npm ci && npm run build        # rebuild js/localsend.js with esbuild
python3 -m unittest discover -s server/tests -v
docker build -t localsend-relay:dev server/
```

The bundle in `js/` is committed because Nextcloud does not build apps on
install. CI rebuilds it and fails when the committed file is stale.

For a real-instance check, copy the repository into
`custom_apps/localsend_share/` of a Nextcloud 33+ installation, run
`occ app:enable localsend_share` and use *Administration settings → LocalSend
Share*.

## Releasing to the Nextcloud App Store

The store requires an app-specific certificate and a signed archive.

1. Generate a key and CSR (once):

   ```bash
   mkdir -p ~/.nextcloud/certificates
   openssl req -nodes -newkey rsa:4096 \
     -keyout ~/.nextcloud/certificates/localsend_share.key \
     -out ~/.nextcloud/certificates/localsend_share.csr \
     -subj "/CN=localsend_share"
   ```

2. Open a pull request with the `.csr` at
   [nextcloud/app-certificate-requests](https://github.com/nextcloud/app-certificate-requests)
   and put the signed `localsend_share.crt` next to the key.

3. Add the repository secrets `APP_PRIVATE_KEY` and `APP_PUBLIC_CRT`, and
   set the repository variable `SIGNING_ENABLED=true`. For the App Store push,
   also add `APPSTORE_TOKEN` (from <https://apps.nextcloud.com/account/token>)
   and set the variable `APPSTORE_ENABLED=true`.

4. Bump the version in `appinfo/info.xml` (and `CHANGELOG.md`), create a GitHub
   release tagged `v<version>` and publish it. The
   [release workflow](.github/workflows/release.yml) builds, signs and attaches
   `localsend_share.tar.gz`, then pushes it to the store.

Until `SIGNING_ENABLED` is set, the workflow attaches an unsigned archive to
the GitHub release (enough for manual installs). Certification is pending for
both apps; the certificate requests are at
[nextcloud/app-certificate-requests](https://github.com/nextcloud/app-certificate-requests).

## License

[AGPL-3.0-or-later](LICENSE).

---

## 日本語の概要

Nextcloud の Files アプリに「LocalSendで送る」アクションを追加する
`localsend_share` アプリと、その受け先となる LocalSend 送信API
（`server/`、Python 標準ライブラリのみ）のリポジトリです。

- 中継APIは同一LAN上で動かします（UDPのマルチキャスト/ブロードキャスト探索の
  ため、Dockerでは `network_mode: host`）。`docker compose up -d` で起動し、
  `LOCALSEND_SEND_TOKEN` と `LOCALSEND_SEND_FINGERPRINT` を `.env` で設定します
- アプリは管理画面（*管理設定 → LocalSend Share*）で中継APIのURLとトークンを
  設定します。保存時にヘルスチェックして結果を表示します
- 利用者はFilesの「LocalSendで送る」からLAN上の端末を選んで送信します。送り先の
  端末ではLocalSendアプリを開いて受信を確認してください
- App Store 公開にはアプリ専用の証明書（CSRを
  [app-certificate-requests](https://github.com/nextcloud/app-certificate-requests)
  へPR）と、GitHub Release からの署名・アップロードが必要です
