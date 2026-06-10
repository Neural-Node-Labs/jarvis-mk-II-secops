# Kali Linux Tool Installation Notes

## Environment Context
- This container runs **Kali Linux Rolling** (`kalilinux/kali-rolling`).
- It is a **minimal/headless** Docker container — many signature Kali tools are NOT pre-installed.

## APT Lock Issue
- The `dpkg` lock (`/var/lib/dpkg/lock-frontend`) can get held by a previous timed-out `apt-get` process.
- **Fix**: `kill` the holding process or wait for it to finish.
  - Check: `ps aux | grep apt`
  - Kill: `kill -9 <PID>`
  - Then retry: `apt-get install -y <package>`

## Signature Kali Packages (available in repos)
| Package | Description |
|---|---|
| `kali-defaults` | Kali default settings (lightweight, quick install) |
| `kali-autopilot` | Automatic attack scripts for Kali |
| `kali-archive-keyring` | Kali GPG archive keys |
| `kali-grant-root` | Privilege escalation config |
| `kali-hidpi-mode` | HiDPI mode switcher |

## Metapackages (NOT in default repos, need kali-tools metapackage repo)
- `kali-tools-top10`
- `kali-tools-web`
- `kali-tools-exploitation`
- `kali-linux-headless`

## Recommended Quick Install (to prove Kali identity)
```bash
apt-get update -qq
apt-get install -y kali-defaults kali-grant-root
```

## Common Kali Tools (install individually if needed)
- `nmap`, `metasploit-framework`, `hydra`, `john`, `sqlmap`, `burpsuite`, `aircrack-ng`, `wireshark`

## Note
- `apt-get update` must run first before any install.
- Large metapackages may timeout in this container (60s limit). Install individual tools instead.
