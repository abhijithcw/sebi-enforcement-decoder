# Pi setup (do once the Pi + HDD are connected)

```bash
# 1. confirm the HDD mount and fix data_root in config.yaml (or export SED_DATA_ROOT)
lsblk -f; df -h /mnt/hdd
sudo mkdir -p /mnt/hdd/sebi-enforcement-decoder && sudo chown "$USER" /mnt/hdd/sebi-enforcement-decoder

# 2. code + venv
sudo apt install -y git python3-venv tesseract-ocr   # tesseract only needed for the OCR queue
git clone https://github.com/abhijithcw/sebi-enforcement-decoder ~/projects/sebi-enforcement-decoder
cd ~/projects/sebi-enforcement-decoder
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
cp .env.example .env && nano .env            # ANTHROPIC_API_KEY
.venv/bin/sedec init-db

# 3. cron (flock-guarded)
chmod +x deploy/run.sh
(crontab -l 2>/dev/null; echo "30 21 * * * $HOME/projects/sebi-enforcement-decoder/deploy/run.sh") | crontab -

# 4. serve the static site to the phone (Caddy)
sudo apt install -y caddy
# /etc/caddy/Caddyfile:
#   :8080 { root * /mnt/hdd/sebi-enforcement-decoder/site  file_server }
sudo systemctl restart caddy
```
