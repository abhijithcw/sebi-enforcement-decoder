# Pi setup guide — for beginners

Follow it top to bottom. Don't skip. Each step says **what you should see**.
If anything looks different, stop and send Claude the exact text on your screen.

**What you need**
- Raspberry Pi 5 + its official power supply (27W)
- MicroSD card (32 GB or more) + a way to plug it into your laptop
- 1 TB hard disk (USB) for the data
- Your HP laptop, on the same Wi-Fi the Pi will use
- An Anthropic API key (Phase 6)

**Words you'll meet**
- **Terminal** = a black window where you type commands. Type (or paste) one line, press Enter.
- **SSH** = using the Pi's terminal from your laptop.
- **Paste in terminal:** right-click (Windows PowerShell) or `Ctrl+Shift+V` (Pi, if you use a keyboard on it).
- A line starting with `#` is a note to you. Don't type it.

---

## Phase 1 — Put the operating system on the SD card (laptop)

1. On the HP laptop, open `https://www.raspberrypi.com/software/` and download **Raspberry Pi Imager**. Install and open it.
2. Click **Choose Device** → **Raspberry Pi 5**.
3. Click **Choose OS** → **Raspberry Pi OS (other)** → **Raspberry Pi OS Lite (64-bit)**.
   (Lite = no desktop. We don't need one.)
4. Click **Choose Storage** → pick your **SD card**.
   Check the size twice. Everything on it will be erased.
5. Click **Next**. When asked about customisation, click **Edit Settings**.
6. **General** tab, set:
   - Hostname: `govpi`
   - Username: `abhi`
   - Password: pick one and **write it down** (you'll type it often)
   - Wi-Fi: your Wi-Fi name and password. Country: **IN**
   - Locale / time zone: **Asia/Kolkata**
7. **Services** tab: tick **Enable SSH** → **Use password authentication**.
8. **Save** → **Yes** → **Yes** to write. Wait until it says done.

You should see: "Write Successful". Remove the SD card.

---

## Phase 2 — First boot and connect (Pi + laptop)

1. Put the SD card in the Pi (slot underneath).
2. Plug the **hard disk** into a **blue** USB port (USB 3) on the Pi.
3. Plug in the Pi's power. Wait **5 minutes** the first time.
   (Green light blinking is normal.)
4. On the laptop, press the Windows key, type `PowerShell`, open it.
5. Type:
   ```
   ssh abhi@govpi.local
   ```
6. If it asks "continue connecting?" type `yes` and Enter.
7. Type your Pi password. **Nothing shows while you type. That's normal.** Press Enter.

You should see: a line like `abhi@govpi:~ $`. That's the Pi. Everything below is typed here.

**If `govpi.local` is not found:**
- Wait 2 more minutes and retry.
- Open your Wi-Fi router's page (or the router app), look for a device called `govpi`, note its IP (like `192.168.1.45`), then use `ssh abhi@192.168.1.45`.
- Still no? Send Claude a photo of what you see.

---

## Phase 3 — Update the Pi and set the time

```
sudo apt update && sudo apt full-upgrade -y
```
Takes 5-15 minutes. Wait for `abhi@govpi:~ $` to come back.

```
sudo timedatectl set-timezone Asia/Kolkata
date
```
You should see the correct India time.

```
sudo apt install -y git python3-venv python3-pip tesseract-ocr tmux caddy parted sqlite3
```
(git = download code, tmux = keeps long jobs running, tesseract = reads scanned PDFs, caddy = shows the website, parted + sqlite3 = disk and database tools.)

```
sudo reboot
```
Wait 1 minute, then log in again with `ssh abhi@govpi.local`.

---

## Phase 4 — Prepare the hard disk

**Warning: this ERASES the hard disk. Only do it if nothing on it matters.**

1. Look at the disks:
   ```
   lsblk -o NAME,SIZE,MODEL,FSTYPE,MOUNTPOINT
   ```
   You should see a line of about **931.5G**. Its name is probably `sda`.
   The SD card is `mmcblk0` (about 30G). **Never touch that one.**
   If you don't see a 931G disk: unplug it, plug it in again, wait 10 seconds, run the command again.
   Still not there? The disk may need more power. Tell Claude the disk model.

2. If the big disk is `sda`, run these two lines (change `sda` if yours is different):
   ```
   sudo parted /dev/sda --script mklabel gpt mkpart primary ext4 0% 100%
   sudo mkfs.ext4 -F -L sebihdd /dev/sda1
   ```
   You should see "Writing superblocks ... done".

3. Make it mount automatically every boot:
   ```
   sudo mkdir -p /mnt/hdd
   echo 'LABEL=sebihdd /mnt/hdd ext4 defaults,nofail,noatime 0 2' | sudo tee -a /etc/fstab
   sudo systemctl daemon-reload
   sudo mount -a
   sudo chown abhi:abhi /mnt/hdd
   df -h /mnt/hdd
   ```
   You should see `/dev/sda1` with about **916G** size, mounted on `/mnt/hdd`.

4. Make the project folder:
   ```
   mkdir -p /mnt/hdd/sebi-enforcement-decoder
   ```

---

## Phase 5 — Download the project

```
mkdir -p ~/projects && cd ~/projects
git clone https://github.com/abhijithcw/sebi-enforcement-decoder
cd sebi-enforcement-decoder
python3 -m venv .venv
.venv/bin/pip install -e ".[ocr]"
```
Takes 2-5 minutes. You should see "Successfully installed ...".

Test it:
```
.venv/bin/sedec init-db
```
You should see: `schema ready at /mnt/hdd/sebi-enforcement-decoder/db/sed.sqlite`

---

## Phase 6 — Add your Anthropic API key

The API is **separate from your Claude app plan**. It needs its own credit.

1. On your laptop open `https://console.anthropic.com` and sign in.
2. **Billing**: add a small credit (₹500-1000 is plenty to test).
3. **Settings → Limits**: set a monthly spend limit. Your safety net.
4. **API keys** → Create key → copy it (starts with `sk-ant-`).
5. On the Pi:
   ```
   cd ~/projects/sebi-enforcement-decoder
   cp .env.example .env
   nano .env
   ```
6. Replace `sk-ant-...` with your real key. In nano: **Ctrl+O**, Enter (save), **Ctrl+X** (exit).

Never post this key anywhere, and never send it to Claude. The repo ignores `.env` so it won't go to GitHub.

---

## Phase 7 — Small live test (do NOT skip)

All commands from `~/projects/sebi-enforcement-decoder`.

1. Count orders on SEBI's site:
   ```
   .venv/bin/sedec count
   ```
   You should see a table and **"total since 2015-01-01: about 12,000 orders"**. Takes about 20 seconds.
   If you see errors/403, copy them to Claude.

2. Fetch just 2 listing pages (recent adjudication orders) and 5 PDFs:
   ```
   .venv/bin/sedec scrape --smid 6 --from 2026-10-01 --max-pages 2 --pdf-limit 5
   ```
   Takes a few minutes (it waits 2.5 seconds between requests on purpose, to be polite to SEBI).
   You should see numbers like `"downloaded": 5`.

3. Read the PDFs:
   ```
   .venv/bin/sedec extract-text
   ```
   Then check Hindi handling:
   ```
   sqlite3 /mnt/hdd/sebi-enforcement-decoder/db/sed.sqlite "select language,count(*) from orders group by 1;"
   ```
   Send Claude the result.

4. See the cost before spending anything:
   ```
   .venv/bin/sedec estimate
   ```
   Shows ₹ for Sonnet 5.5 and for Haiku 5.5. **Send this to Claude** to decide which model.

5. Extract only 3 orders (tiny cost, a few rupees):
   ```
   .venv/bin/sedec extract --limit 3
   ```
   You should see `{"done": ..., "needs_review": ...}`. Send Claude the result.
   `needs_review` isn't a failure. It means the checker wanted a human to look.

6. Build the website:
   ```
   .venv/bin/sedec build-site
   ```

---

## Phase 8 — See it on your phone

1. Tell Caddy where the site is (copy this as ONE line):
   ```
   printf ':8080 {\n\troot * /mnt/hdd/sebi-enforcement-decoder/site\n\tfile_server\n\tencode gzip\n}\n' | sudo tee /etc/caddy/Caddyfile
   sudo systemctl restart caddy
   ```
2. Find the Pi's address:
   ```
   hostname -I
   ```
   Example: `192.168.1.45`
3. On your **phone** (same Wi-Fi), open Chrome and go to `http://192.168.1.45:8080` (use your number).
   (Phones often can't open `govpi.local`, so use the number.)

You should see the search page. Try searching a company name.
Tip: in your router settings, "reserve" that IP for the Pi so it never changes.

---

## Phase 9 — The big backfill (only after Claude says OK)

This reads all ~12,000 orders since 2015. It takes **many hours** (SEBI is polite-throttled), and the LLM step costs real money.

1. Start a session that survives closing your laptop:
   ```
   tmux new -s backfill
   cd ~/projects/sebi-enforcement-decoder
   ```
2. Download everything (safe to stop and restart, it continues where it stopped):
   ```
   .venv/bin/sedec scrape
   .venv/bin/sedec extract-text
   ```
3. See the real cost:
   ```
   .venv/bin/sedec estimate
   ```
   Send it to Claude. Only continue if you're happy with the ₹ amount.
4. Spend the money (the `--yes` is you approving):
   ```
   .venv/bin/sedec extract --yes
   ```
   It stops by itself at your monthly cap (₹1,500 in `config.yaml`).
5. To leave: press **Ctrl+B**, release, then press **D**. The job keeps running.
   To come back: `tmux attach -t backfill`
6. When done: `.venv/bin/sedec build-site`

Scanned PDFs (no text) are queued. Read them overnight, slowly, in the background:
```
nice -n 19 .venv/bin/sedec ocr --limit 50
```

---

## Phase 10 — Make it run by itself every night

```
chmod +x ~/projects/sebi-enforcement-decoder/deploy/run.sh
crontab -e
```
First time it asks for an editor: choose **nano**. Go to the bottom and add this one line:
```
30 21 * * * /home/abhi/projects/sebi-enforcement-decoder/deploy/run.sh
```
Save (**Ctrl+O**, Enter) and exit (**Ctrl+X**).

This runs at **9:30 PM**, away from the governance engine's 7:45 PM slot. It only checks the last 45 days and sends at most 40 new orders to the LLM per night. The monthly cap still applies.

Check it ran (next morning):
```
ls /mnt/hdd/sebi-enforcement-decoder/logs/
tail -20 /mnt/hdd/sebi-enforcement-decoder/logs/run-*.log
```

---

## Everyday things

**Update the code later:**
```
cd ~/projects/sebi-enforcement-decoder && git pull && .venv/bin/pip install -e ".[ocr]"
```

**Shut down safely:** `sudo shutdown now`, wait for the green light to stop, then unplug. Never pull the power while it's on: it can corrupt the SD card and the disk.

**Check free space:** `df -h /mnt/hdd`

**Disk not mounted after reboot?** Run `sudo mount -a` then `df -h /mnt/hdd`.

**Stuck?** Copy the exact red/error text (not a summary) and send it to Claude, with the step number.

---

## Do not

- Don't run the disk-erase commands (Phase 4) a second time on a disk that has your data.
- Don't share your `.env` or API key.
- Don't run `sedec extract --yes` before looking at `sedec estimate`.
- Don't unplug the Pi without `sudo shutdown now`.
