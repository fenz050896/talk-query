# Answer Format for Reasoning Questions — Design Spec

**Status:** draft | **Date:** 2026-05-28 | **Revision:** 1

## Problem

Talk-Query hanya punya dua format respons: `EXPLAIN` (narasi murni, tanpa SQL) dan `SELECT` (SQL + hasil). Tidak ada format untuk pertanyaan reasoning bisnis — pertanyaan yang butuh SQL untuk verifikasi, tapi jawabannya berupa kesimpulan naratif, bukan data mentah.

### Contoh kasus

Pertanyaan: *"Apakah ada buku yang tidak pernah dipinjam sama sekali?"*

**Respons saat ini (SELECT path):**

> Ya, kemungkinan besar ada buku yang tidak pernah dipinjam. Sistem ini memiliki tabel `biblio` untuk mencatat semua buku dan tabel `loan` untuk mencatat peminjaman. Berikut query untuk menampilkan buku-buku yang tidak pernah dipinjam:
>
> ```sql
> SELECT b.biblio_id, b.title, ...
> FROM biblio b
> WHERE b.biblio_id NOT IN (...)
> ```
>
> *(hasil: 0 rows — atau error karena asumsi skema salah)*

**Masalah:**
1. SQL ditampilkan ke user padahal user tidak minta query, user minta kesimpulan
2. User awam tidak peduli dengan SQL — mereka peduli jawaban ya/tidak + alasannya
3. Kalau SQL salah (asumsi kolom, hasil kosong), kredibilitas jawaban runtuh

**Respons ideal (seharusnya):**

> Ya, ada 12 buku yang tidak pernah dipinjam sama sekali. Sebagian besar buku referensi dan koleksi khusus yang diterbitkan setelah 2020. Anda mungkin ingin mempromosikan buku-buku ini di display perpustakaan atau rekomendasi pustakawan.

SQL tetap dijalankan, tapi tidak ditampilkan. User dapat insight, bukan query.

### Akar masalah

Prompt `SYSTEM_PROMPT_WITH_CONTEXT` hanya mendefinisikan dua intent:

```
- Jika pertanyaan TENTANG database → EXPLAIN: <narasi>
- Jika pertanyaan meminta DATA → SELECT <query>
```

Tidak ada slot untuk: **"pertanyaan DATA, tapi jawabannya KESIMPULAN."**

## Goal

Sistem bisa membedakan tiga intent pertanyaan dan memberikan format jawaban yang sesuai:

| Intent | Contoh | SQL dijalankan? | SQL ditampilkan? | Jawaban |
|---|---|---|---|---|
| **META** | "Database ini tentang apa?" | Tidak | Tidak | Narasi |
| **DATA** | "Tampilkan daftar buku yang belum dipinjam" | Ya | Ya | Tabel/data |
| **REASONING** | "Apakah ada buku yang belum dipinjam?" | Ya | Tidak (hidden) | Narasi + kesimpulan |

## Constraints

- Tetap satu LLM call untuk pertanyaan reasoning (tidak tambah latency)
- SQL tetap disimpan di backend (field `sql`) untuk transparency, tapi frontend bisa memilih tidak menampilkan
- Tidak merusak flow DATA existing — query simpel tetap cepat
- Fallback aman: kalau LLM salah klasifikasi, tetap ada jawaban yang berguna

## Approaches

### A. Prompt-only — tambah format respons `ANSWER`

Modifikasi `SYSTEM_PROMPT_WITH_CONTEXT`:

```
- Jika pertanyaan meminta KESIMPULAN, PENILAIAN, atau YA/TIDAK
  (apakah, mengapa, bagaimana mungkin, beri pendapat, analisis):
  ANSWER: <kesimpulan naratif dalam bahasa pertanyaan>
  SQL: <query untuk verifikasi>
```

Backend parsing di `generate_answer()`:
1. Deteksi prefix `ANSWER:`
2. Ekstrak narasi (setelah `ANSWER:`) dan SQL (setelah `SQL:`)
3. Route ke "reasoning": jalankan SQL, tapi hanya stream narasi ke user
4. SQL tetap disimpan di `field["sql"]` untuk debugging/transparency

**Perubahan file:**
- `backend/llm.py` — `SYSTEM_PROMPT_WITH_CONTEXT` (+4 baris), `generate_answer()` parsing (+15 baris)
- `backend/main.py` — routing handler (+10 baris)
- Frontend — opsional: sembunyikan SQL box untuk tipe ANSWER

**Kelebihan:**
- Satu LLM call
- Perubahan minimal
- Prompt overhead rendah (~3-4 baris)

**Kekurangan:**
- Prompt sudah padat, tambah satu jalur bisa bikin LLM bingung (ANSWER vs EXPLAIN)
- Bergantung pada kualitas LLM membedakan intent dari pertanyaan user

### B. Dua-pass LLM

Pass 1: `generate_sql()` — generate SQL saja (seperti mode lama tanpa profile)
Pass 2: Kirim hasil query + pertanyaan asli ke LLM, minta narasi reasoning

```
[User question] → [LLM: generate SQL] → [Execute SQL] → [LLM: generate narrative from results] → [User]
```

**Kelebihan:**
- Separasi bersih: SQL generation dan narrative generation terisolasi
- Narasi lebih kaya karena LLM bisa lihat data aktual (jumlah row, pola, anomali)
- SQL tidak pernah bocor ke user

**Kekurangan:**
- 2 LLM call = 2x latency (~3-6 detik tambahan)
- 2x token cost
- Pass 2 perlu konteks yang cukup (pertanyaan + hasil query + schema)

### C. Intent classifier + routing

Classifier prompt ultra-ringan (~50 token) klasifikasi intent sebelum prompt utama:

```
Classify this question: "Apakah ada buku yang tidak pernah dipinjam?"
Categories: META | DATA | REASONING
Answer with one word.
```

Lalu routing:
- `META` → EXPLAIN (existing)
- `DATA` → SELECT + tampilkan SQL (existing)
- `REASONING` → SELECT (hidden) + generate narasi dari hasil

**Kelebihan:**
- Paling robust: classifier terisolasi, prompt utama tidak terbebani
- Classifier murah: request kecil (~100 token total)
- Bisa ditune independen (ganti model kecil untuk classifier)
- Tidak membebani prompt utama

**Kekurangan:**
- Tambah 1 LLM call kecil (~200-500ms)
- Tambah kompleksitas routing di `main.py`
- Butuh prompt classifier yang akurat

## Recommendation

**Approach A (prompt-only)** untuk iterasi pertama.

Alasan:
- Perubahan minimal, bisa di-deploy cepat
- Kalau LLM salah klasifikasi ANSWER vs EXPLAIN, fallback masih aman (dua-duanya narasi)
- Bisa jadi batu loncatan ke approach C kalau diperlukan
- Overhead prompt rendah

## Decision Matrix

| Kriteria | A (Prompt-only) | B (Two-pass) | C (Classifier) |
|---|---|---|---|
| Kompleksitas implementasi | Rendah | Sedang | Tinggi |
| Latency tambahan | 0 | +3-6 detik | +200-500ms |
| Akurasi klasifikasi intent | LLM-dependent | LLM-dependent | Tinggi (terisolasi) |
| Kualitas narasi | Baik | Sangat baik (lihat data) | Baik |
| Risiko regresi flow existing | Rendah | Rendah | Sedang |
| Kemudahan iterasi | Tinggi | Sedang | Rendah |

## Open Questions

1. Apakah frontend perlu di-update untuk menyembunyikan SQL box untuk tipe ANSWER? Atau cukup backend tidak kirim field `sql`?
2. Bagaimana handle pertanyaan REASONING yang SQL-nya gagal atau return 0 rows? Tetap narasi, atau fallback ke DATA?
3. Apakah perlu indikator visual di UI yang membedakan jawaban META vs REASONING vs DATA?

## Next Steps

1. Pilih approach (approve A, atau pilih B/C)
2. Tulis implementation plan (`writing-plans`)
3. Implement + test dengan 3-5 pertanyaan reasoning SLiMS
