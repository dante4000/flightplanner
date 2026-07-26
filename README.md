# award-trip-planner

Local dashboard that ranks booking strategies (cash / Aeroplan points / mixed)
for a 2-person LAX ⇄ Korea ⇄ Japan trip, using live seats.aero award data and
Google Flights cash prices.

## Run

    uv run award-trip-planner

Opens http://127.0.0.1:8722. Hit **Refresh data** — first fill takes a few
minutes (Google Flights is slow); re-run refresh until "cash quotes still
missing" reaches 0. Data is cached in `cache.sqlite` (cash 6h TTL, awards 24h).

## Notes

- seats.aero quota: 1,000 calls/day (a refresh uses ~10–20). Key in `.env`.
- Cash prices come from Google Flights via a patched `fast-flights==3.0.2`
  (`gflights_patch.py`). If Google breaks it, cells go stale — click any cash
  cell to enter a manual price; the engine treats overrides as truth.
- Stopover awards (LAX→Seoul⏸→Tokyo, +5k pts) are estimates: seats.aero can't
  price stopovers. Verify at aeroplan.com before transferring points.
- This ranks and recommends; it does not book. Always re-verify award space at
  aeroplan.com right before booking.

## Tests

    uv run pytest -q
