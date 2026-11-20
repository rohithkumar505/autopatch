# Challenge 03 — Safe file join for user uploads

## Expected
- Reject `..`, absolute paths, and nul bytes.
- Normalised path stays under `base`.
- `open_user_file` never opens outside sandbox root.

## Verify
`safe_join('/var/data', 'reports/jan.pdf')` OK; `safe_join('/var/data', '../etc/passwd')` raises.
