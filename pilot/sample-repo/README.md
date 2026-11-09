# Sample Shop

A tiny multi-module Python repo used for the Agent Guild V1 pilot.

- `src/shop/pricing.py` — subtotal / discounts
- `src/shop/cart.py` — cart counts / totals
- `src/shop/formatting.py` — money formatting
- `tests/` — unittest suite

Run tests:

```powershell
$env:PYTHONPATH="src"
python -m unittest discover -s tests -q
```
