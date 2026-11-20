# bulk_suite

One import root for regression-testing AutoPatch and the IDE against **all** demo apps:

- `marks_calculator/` — seven planted bugs (grades, sorting, `eval`, etc.)
- `shop_inventory/` — inventory / pricing bugs
- `library_app/` — library desk bugs

Ground truth for each app stays in `samples/ground_truth/` (outside this tree so scans cannot read answers).

**Import in AutoPatch:** Projects → **Local path** →  
`/Users/rohith_kumar_505/Desktop/VINOD_PROJECT/samples/bulk_suite`  
(or your repo path ending in `samples/bulk_suite`).

Then open **IDE**, run **Scan**, **Run tests**, and exercise multi-tab editing across the three packages.