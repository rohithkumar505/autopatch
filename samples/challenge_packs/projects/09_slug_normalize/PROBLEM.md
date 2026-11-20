# Challenge 09 — URL slug normalizer

## Expected
- Lowercase, replace spaces with `-`, collapse repeated dashes.
- Reject empty slugs.
- Unicode letters may be transliterated or stripped consistently.

## Verify
`slugify("Hello  World!")` → `hello-world`.
