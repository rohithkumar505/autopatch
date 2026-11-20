# Expected outcomes (host / recording script)

After a **successful AutoPatch fix cycle**, each pack should behave as below.  
Ground-truth bug lists live in `ground_truth/*.json` (not in zips).

## 01_token_bucket
| Check | Expected |
|-------|----------|
| `TokenBucket(10,2).consume("x",3,0)` | `True`, 7 tokens remain |
| Exact token match `consume` at boundary | Allowed when tokens == cost |
| `merge_limits({"a":1},{})` then `merge_limits({"b":2},{})` | No leaked keys from first call |

## 02_invoice_totals
| Check | Expected |
|-------|----------|
| `total_with_tax([(1,100)], tax=0.1, discount_percent=10)` | Discount on subtotal, then tax → **99.0** not 110 |
| `apply_discount(100, 10)` | **90** not 110 |
| Two `Invoice()` instances | Independent line lists |

## 03_path_sandbox
| Check | Expected |
|-------|----------|
| `safe_join("/data","../x")` | Raises `ValueError` or similar |
| `is_valid_segment("/etc")` | `False` |

## 04_priority_inbox
| Check | Expected |
|-------|----------|
| `top_messages([{id:"a",priority:1},{id:"b",priority:9}],1)` | `["b"]` |
| `urgent_count([{priority:3}])` | **1** |

## 05_room_overlap
| Check | Expected |
|-------|----------|
| `overlaps(0,10,10,20)` | **False** (adjacent ok) |
| `free_slots(0,120,[],60)` | Includes slot starting at 60 |

## 06_coupon_stack
| Check | Expected |
|-------|----------|
| `apply_codes(100,["FLAT5"])` | **95** |
| `parse_coupon` | No `eval` |

## 07_metric_windows
| Check | Expected |
|-------|----------|
| `average([])` | Raises or documented sentinel, no crash |
| `rate_per_minute([1,2],5,5)` | Safe handling |
| `rolling_max([1,3,2,5],2)` | Last value **5** with window including current |

## 08_session_filter
| Check | Expected |
|-------|----------|
| `match_role({"role":"administrator"},"admin")` | **False** |
| `parse_filter` | No `eval` |

## 09_slug_normalize
| Check | Expected |
|-------|----------|
| `slugify("a--b")` | `a-b` (collapsed) |
| `unique_slug("x",{"x"})` | Returns `x1` or similar without broken recursion |

## 10_slot_grid
| Check | Expected |
|-------|----------|
| `align_grid(0,60,30)` | **2** slots (not 3) for half-open range |
| `day_capacity(...)` | Count of **free** slots |
| `parse_booking` | No `eval` |

---

**AutoPatch demo (pack 01):** Import `zip/01_token_bucket.zip` → Scan → expect security/logic findings on `consume`, `_level`, `merge_limits` → Generate fix on one finding → Run tests → status should show sandbox run completed (some tests may still fail until all bugs fixed).