# Plan: Karaoke → TalkQuery Nginx Switch

## Context
Ganti karaoke Podman dengan TalkQuery langsung (no container). Pakai domain `df-karaoke.duckdns.org` yang sama, config nginx yang sama, cukup comment karaoke + tambah TalkQuery.

## Langkah

### 1. Stop Podman containers
```bash
podman stop fun-karaoke_backend_1 fun-karaoke_frontend_1
```

### 2. Edit nginx config
File: `/etc/nginx/sites-available/df-karaoke`
- Comment semua karaoke config dengan `#`
- Tambah TalkQuery config (sama struktur: `/api/` → 8000, sisanya → 3000)

### 3. Reload nginx
```bash
nginx -t && systemctl reload nginx
```

### 4. Jalankan TalkQuery
```bash
cd /home/ubuntu/projects/talk-query && bash start.sh
```

### 5. Verifikasi
Buka `http://df-karaoke.duckdns.org` dari browser.
