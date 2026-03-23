# Deploy Playbook

## 1. Deploy App

Gunakan repo GitHub ini sebagai source deploy.

App start command:

```bash
uvicorn server:app --host 0.0.0.0 --port ${PORT:-8000}
```

Kalau platform deploy membaca `Dockerfile`, app ini sudah bisa dipakai dari sana juga.

## 2. Environment Variables

Isi env berikut di cloud:

```env
SCRAPE_TIMEOUT_SECONDS=45
PROFILE_CACHE_TTL_SECONDS=900
SEARCH_CACHE_TTL_SECONDS=900
COMMENTS_CACHE_TTL_SECONDS=900
COOKIE_SECURE=true

SUPABASE_URL=https://banazgsnguqztoxqrgmb.supabase.co
SUPABASE_PUBLISHABLE_KEY=sb_publishable_ehw0NANv16Az-A8aeekOHg_TEwqDiCY
SUPABASE_SERVICE_ROLE_KEY=

MAYAR_URL_RINGAN=
MAYAR_URL_TUMBUH=
MAYAR_URL_TIM=
MAYAR_WEBHOOK_SECRET=
```

## 3. Supabase SQL

Schema sekarang sudah ada juga di folder migration Supabase:

- [20260318220000_init_schema.sql](/Users/amosthiosa/Documents/Playground/supabase/migrations/20260318220000_init_schema.sql)

Kalau deploy project baru, bisa pakai salah satu:

- `npx supabase db push`
- atau paste [schema.sql](/Users/amosthiosa/Documents/Playground/db/schema.sql) ke SQL Editor

## 4. Mayar Setup

Buat 3 checkout page / product page di Mayar:

- Paket Ringan
- Paket Tumbuh
- Paket Tim

Ambil URL publiknya, lalu isi:

- `MAYAR_URL_RINGAN`
- `MAYAR_URL_TUMBUH`
- `MAYAR_URL_TIM`

## 5. Auth Flow Test

Setelah env Supabase terisi:

1. Buka `/signup`
2. Buat akun
3. Buka `/signin`
4. Login
5. Pastikan `/app` bisa diakses

## 6. Payment Flow Test

Setelah env Mayar terisi:

1. Buka `/payment`
2. Klik salah satu paket
3. Pastikan redirect ke Mayar berhasil

## 7. Webhook Target

Set webhook Mayar ke:

```text
https://YOUR-DOMAIN/api/payment/webhook/mayar
```

Webhook sekarang sudah:

- verifikasi shared secret sederhana
- simpan `payment_transactions`
- update `subscriptions`
- infer `plan_code` dari nama produk / nominal

## 8. Next Backend Work

- tampilkan status plan user di UI
- tampilkan meter kuota user di UI
- tambahkan custom invoice reference biar mapping payment -> user lebih deterministic
