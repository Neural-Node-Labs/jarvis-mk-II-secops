# Kali Linux Hardening & Intrusion Monitoring Directive

## Purpose
This directive covers two areas: (1) hardening a Kali Linux system for safe day-to-day use, and (2) setting up active monitoring to detect intrusions or suspicious activity. It's written as a practical checklist with example commands.

---

## PART 1: Hardening Kali Linux

### 1. Update and Patch Regularly
```bash
sudo apt update && sudo apt full-upgrade -y
```
- Schedule this regularly (weekly at minimum) — outdated packages are the most common entry point for attackers.
- Consider `unattended-upgrades` for automatic security patches on non-tooling packages.

### 2. Change Default Credentials and Lock Down Accounts
- Set a strong password immediately if using a default/known one.
- Create a non-root user for daily work; reserve root for tasks that truly need it.
```bash
adduser yourname
usermod -aG sudo yourname
```
- Disable direct root SSH login (covered in SSH section below).
- Remove or disable unused accounts: `sudo passwd -l <username>`.

### 3. Minimize Attack Surface
- Kali ships with many tools and services pre-installed — disable anything not actively in use.
```bash
systemctl list-unit-files --state=enabled
sudo systemctl disable --now <service>
```
- Common ones to check: `ssh`, `bluetooth`, `cups`, `avahi-daemon`, `postgresql` (used by some Kali tools but shouldn't run unless needed).
- Uninstall services/tools you don't use to reduce both attack surface and noise.

### 4. Configure a Firewall
```bash
sudo apt install ufw -y
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow from <trusted_ip> to any port 22 proto tcp   # if SSH needed
sudo ufw enable
```
- Deny by default, allow only what's explicitly needed.
- Review rules periodically: `sudo ufw status verbose`.

### 5. Harden SSH (if enabled)
Edit `/etc/ssh/sshd_config`:
- `PermitRootLogin no`
- `PasswordAuthentication no` (use key-based auth instead)
- `Port <non-default-port>` (reduces automated scan noise, not a substitute for other controls)
- `AllowUsers yourname`
- `MaxAuthTries 3`
- `ClientAliveInterval 300` / `ClientAliveCountMax 2` (drop idle sessions)

Then:
```bash
sudo systemctl restart sshd
```

### 6. Full Disk Encryption
- If not set up at install time, plan a reinstall with LUKS full-disk encryption for any laptop or portable device — especially important since Kali often holds sensitive engagement data.

### 7. Enable Mandatory Access Control
```bash
sudo apt install apparmor apparmor-profiles apparmor-utils -y
sudo systemctl enable --now apparmor
sudo aa-status   # check enforcement status
```
- AppArmor confines what individual programs can access even if compromised.

### 8. Network Hygiene for Tooling
- Use a VPN or isolated network segment when running offensive tools, so Kali itself isn't directly exposed to untrusted networks.
- If running Kali in a VM, prefer a host-only or NAT network for general use, and a dedicated bridged/isolated network for engagements.

### 9. Secure Sensitive Data
- Encrypt directories holding client data/engagement notes (e.g., with `gocryptfs`, `veracrypt`, or LUKS containers).
- Use a password manager (e.g., KeePassXC) rather than storing credentials in plaintext files.

### 10. Backups
- Maintain encrypted backups of configuration and important data (e.g., `restic`, `borgbackup`) — useful for both recovery and forensic comparison if something is later found to be tampered with.

---

## PART 2: Active Intrusion Monitoring

### 1. Centralized, Tamper-Resistant Logging
- Ensure logging is enabled and persistent:
```bash
sudo apt install rsyslog -y
sudo systemctl enable --now rsyslog
```
- For critical systems, forward logs to a remote/central log server so an attacker can't simply erase local logs to cover tracks.

### 2. System Auditing with auditd
```bash
sudo apt install auditd audispd-plugins -y
sudo systemctl enable --now auditd
```
- Add rules to watch sensitive files and actions, e.g. in `/etc/audit/rules.d/audit.rules`:
```
-w /etc/passwd -p wa -k passwd_changes
-w /etc/shadow -p wa -k shadow_changes
-w /etc/sudoers -p wa -k sudoers_changes
-a always,exit -F arch=b64 -S execve -k exec_log
```
- Review with `ausearch -k passwd_changes` or `aureport`.

### 3. File Integrity Monitoring
```bash
sudo apt install aide -y
sudo aideinit
sudo cp /var/lib/aide/aide.db.new /var/lib/aide/aide.db
```
- Run regular checks: `sudo aide --check`
- Schedule via cron/systemd timer to detect unauthorized changes to system binaries and configs.

### 4. Intrusion Detection / Prevention
- **Host-based**: Wazuh or OSSEC — agent-based monitoring with log analysis, file integrity, rootkit detection, and alerting.
- **Network-based**: Suricata or Snort for packet inspection on network interfaces.
```bash
sudo apt install suricata -y
sudo suricata-update
sudo systemctl enable --now suricata
```

### 5. Brute-Force Protection
```bash
sudo apt install fail2ban -y
sudo systemctl enable --now fail2ban
```
- Configure `/etc/fail2ban/jail.local` to monitor SSH and any other exposed services, banning IPs after repeated failed attempts.

### 6. Rootkit and Anomaly Scanning
```bash
sudo apt install rkhunter chkrootkit -y
sudo rkhunter --update
sudo rkhunter --check
sudo chkrootkit
```
- Schedule periodic scans; review output for unexpected findings (some false positives are normal — investigate, don't ignore).

### 7. Network and Process Monitoring
- Check listening ports and active connections regularly:
```bash
sudo ss -tulpn
sudo netstat -tulpn
```
- Watch for unexpected processes:
```bash
ps aux --sort=-%cpu | head
```
- Use `lsof` to see what files/sockets a suspicious process has open.

### 8. Real-Time Alerting
- Configure Wazuh/OSSEC or a simple log-watching script (e.g., with `logwatch` or custom `journalctl` parsing) to email/notify on:
  - New sudo/root sessions
  - Failed login spikes
  - New listening ports
  - Changes to AIDE/auditd watched files

### 9. Establish a Baseline
- Document "normal" for your system: expected running services, open ports, scheduled cron jobs, and user accounts.
- Anomaly detection only works if you know what's normal — review this baseline after legitimate changes (new software, config updates).

### 10. Incident Response Readiness
- Keep a clean, offline copy of key system info (installed packages, `crontab -l` for all users, `/etc/passwd`, firewall rules) for comparison if compromise is suspected.
- If something looks compromised: isolate the system from the network first, then investigate — don't reboot immediately, as this can erase volatile evidence (running processes, memory, network connections) needed for analysis.

---

## Summary Cadence
- **Daily**: review auth logs, fail2ban bans, auditd alerts.
- **Weekly**: apply updates, run rkhunter/chkrootkit, review AIDE diffs.
- **Monthly**: review firewall rules, user accounts, and overall baseline for drift.
