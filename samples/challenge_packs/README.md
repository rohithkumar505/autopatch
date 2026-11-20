# AutoPatch challenge packs (demo / recording)

Ten **independent** Python mini-projects, each shipped as a **zip** for import in AutoPatch (Projects → Zip).  
Each pack has planted bugs that AutoPatch Core + scan can find; fixes are meant to be **reviewed and validated** in the app.

## Folder layout

| Path | Purpose |
|------|---------|
| `zip/*.zip` | What you upload or pick for a recording |
| `projects/<name>/` | Unpacked source (regenerate zips after edits) |
| `ground_truth/<name>.json` | **Grader / host only** — not inside zips so scans cannot read answers |
| `EXPECTED_OUTCOMES.md` | One-page expected results per pack |

## Recording workflow

1. **Settings** → enable **Allow limited isolation** (macOS) or set up Docker sandbox.  
2. **Projects** → import **one** zip from `zip/`.  
3. **Scan** → review findings (compare mentally to `EXPECTED_OUTCOMES.md` if you are hosting).  
4. **AutoFix** on a finding → accept patch → **Run tests**.  
5. Repeat with a **different** zip for another take.

## Pack index

| # | Zip file | Theme | Planted bug count |
|---|----------|--------|-------------------|
| 01 | `01_token_bucket.zip` | API rate limiting | 5 |
| 02 | `02_invoice_totals.zip` | Billing / tax | 5 |
| 03 | `03_path_sandbox.zip` | File path safety | 4 |
| 04 | `04_priority_inbox.zip` | Message priority | 5 |
| 05 | `05_room_overlap.zip` | Calendar overlaps | 4 |
| 06 | `06_coupon_stack.zip` | Discount stacking | 5 |
| 07 | `07_metric_windows.zip` | Time-series rollup | 5 |
| 08 | `08_session_filter.zip` | Session query (security) | 4 |
| 09 | `09_slug_normalize.zip` | URL slug rules | 4 |
| 10 | `10_slot_grid.zip` | Appointment grid | 5 |

Regenerate zips after editing sources:

```bash
./samples/challenge_packs/build_zips.sh
```