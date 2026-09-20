# Multi-order pooling — architecture

Design for letting one driver serve multiple trips in a single run when
their pickups/drops are close together or along the same route (Porter/Uber
Pool-style batching), instead of always assigning one driver per trip.
This is a design doc, not yet implemented — `trips/` today is strictly
one driver : one trip.

## Why a new model instead of reshaping Trip

`Trip` is the customer-facing unit: one fare, one pickup, one drop, one
`reference_id` a partner system reconciles against. Pooling doesn't change
any of that — it changes how many trips one driver physically runs at once,
and in what order they hit each stop. Overloading `Trip` to hold multiple
pickups/drops would break every existing consumer of the single
pickup/drop/fare shape (the estimate response, the fare card, the Postman
collection, any partner already integrated against `POST /trips`).

So: `Trip` stays exactly as it is. Two new concepts sit above it.

## Data model

```
TripBatch (new)
  id, company, driver, vehicle, status, started_at, completed_at,
  total_distance_meters, total_duration_seconds, route_polyline

RouteStop (new)
  id, batch (FK -> TripBatch), trip (FK -> Trip), stop_type (pickup|drop),
  sequence (positive int, order within the batch), lat, lng, address,
  status (pending|arrived|completed), arrived_at

Trip (existing, +1 field)
  batch = FK -> TripBatch, null=True  # SET_NULL, same pattern as Trip.driver/vehicle
```

Every trip ends up in exactly one `TripBatch` — including the common case
of "no pooling match found," which just produces a batch of size 1. This
keeps the driver-side API uniform (drivers always work off "my current
batch," never a special-cased single-trip path) instead of branching
between a pooled and non-pooled driver experience.

`RouteStop.sequence` is the actual dispatch instruction: drive to stop 0,
then 1, then 2, etc. A 2-order batch is 4 stops — e.g. pickup A, pickup B,
drop A, drop B, or pickup A, drop A, pickup B, drop B — whichever ordering
`MatchingService` computed as shortest, subject to the one hard constraint
below.

`Trip.status` keeps meaning what it means today; it's now *derived* from
that trip's own two `RouteStop` rows rather than driven directly by driver
actions:

| Trip.status | Driven by |
|---|---|
| `requested` / `no_driver_available` | unchanged (pre-batch) |
| `assigned` | batch has a driver, trip's pickup stop still `pending` |
| `arrived_at_pickup` | trip's pickup stop is `arrived` |
| `in_progress` | trip's pickup stop is `completed`, drop stop still open |
| `completed` | trip's drop stop is `completed` |

## The hard constraint: precedence

A trip's pickup stop must come before its own drop stop in the sequence.
Nothing else is a hard constraint — pickups and drops from different trips
can interleave freely. This is the classic **pickup-and-delivery problem
(PDP)**, not general TSP, and that distinction is what keeps the sequencing
step cheap: with `POOLING_MAX_ORDERS_PER_BATCH` capped at 2–3 (see below),
the valid-ordering search space is small enough to brute-force.

- 1 order → 1 valid ordering.
- 2 orders → 6 valid orderings (of the 4! = 24 total permutations of 4
  stops, 1/4 respect both precedence constraints simultaneously).
- 3 orders → 90 valid orderings out of 6! = 720.

At 3 orders this is still fast enough to brute-force in-process (no need
for a real VRP solver / OR-tools) — evaluate every valid ordering's total
distance via one batched cost estimate and keep the cheapest. Above that,
the search space grows too fast for brute force and would need a real
heuristic (cheapest-insertion + 2-opt); that's out of scope until there's
a concrete case for batches larger than 3.

## When to pool: the matching flow

Today, `TripService.create_trip` calls `MatchingService.try_assign_driver`
directly. Pooling inserts a step before that:

```
create_trip(...)
  1. compute this trip's own route + fare (unchanged — a pooled trip is
     still priced as if it went direct; see Fare policy below)
  2. save the Trip
  3. NEW: MatchingService.find_pooling_candidate(trip)
       -> look at open TripBatches in the same company whose:
          - vehicle_type matches
          - status is `assigned` or `in_progress` (driver already moving,
            not yet finished)
          - order count < POOLING_MAX_ORDERS_PER_BATCH
          - first pending stop's location is within
            POOLING_SEARCH_RADIUS_KM of this trip's pickup (cheap
            haversine prefilter, same idea as MatchingService today —
            before touching Valhalla at all)
       -> for each candidate batch, compute the best valid stop ordering
          that includes the new trip's pickup+drop, and its total
          distance
       -> accept the candidate whose *detour* (new total distance minus
          the batch's current planned distance) is smallest AND under
          POOLING_MAX_DETOUR_KM (or POOLING_MAX_DETOUR_PERCENT of the
          new trip's own direct distance, whichever is stricter)
  4a. match found -> attach trip.batch = that batch, rewrite the batch's
      RouteStop sequence to the winning ordering, recompute
      total_distance/total_duration/route_polyline, notify
      ("trip.pooled", trip)
  4b. no match -> MatchingService.try_assign_driver(trip) exactly as
      today, which (on success) creates a new TripBatch of size 1
      wrapping this one trip
```

Every step from here on (driver arrive/start/complete, cancel) operates
against `RouteStop`, not directly against `Trip.status` — see API changes.

## Fare policy

Each trip is priced independently, exactly as `PricingService` does today
— as if it were a direct trip, not a share of the batch's combined
distance. Pooling is purely a **dispatch-side optimization** (fewer
driver-km per order, more orders served per driver-hour); it does not
change what the customer is quoted or charged. A pooling discount is a
real product decision with its own tradeoffs (undercutting direct-trip
revenue, needing to explain "why is this cheaper" to customers) and
deliberately isn't part of this design — `PricingService.get_surge_multiplier`
is already the marked extension point for pricing changes, and a pooling
discount would be a sibling to it, not something to bolt on speculatively
here.

## API changes

New:
- `GET /driver/batches/active` — the driver's current `TripBatch` with its
  ordered `RouteStop` list (each stop shows which trip it belongs to,
  pickup or drop, address, contact). Supersedes `GET /driver/trips/active`
  for pooled batches; that endpoint can stay for backward compatibility,
  returning the single trip in a size-1 batch.
- `POST /driver/batches/{id}/stops/{stop_id}/arrive` — marks a stop
  `arrived`.
- `POST /driver/batches/{id}/stops/{stop_id}/complete` — marks a stop
  `completed`, which is what actually flips the owning `Trip`'s status per
  the derivation table above.

Changed:
- `GET /trips/{id}` response gains `batch_id` and this trip's own
  `pickup_stop_status` / `drop_stop_status`, so a partner watching one
  `reference_id` can still see it move through pickup/drop without
  needing to know about batching at all.
- `POST /trips/{id}/cancel` — cancelling one trip inside a multi-order
  batch must re-run the sequencing step for the remaining trips (drop the
  cancelled trip's two stops, keep the rest in relative order) rather than
  cancelling the whole batch.

Unchanged: `POST /trips/estimate`, `POST /trips` request/response shape,
`POST /trips/{id}/assign`. A caller booking a single trip never needs to
know pooling exists.

## Config (mirrors the existing `DRIVER_MATCH_RADIUS_KM` pattern)

```python
POOLING_ENABLED = env.bool("POOLING_ENABLED", default=False)  # safe rollout: off until proven
POOLING_MAX_ORDERS_PER_BATCH = env.int("POOLING_MAX_ORDERS_PER_BATCH", default=2)
POOLING_SEARCH_RADIUS_KM = env.float("POOLING_SEARCH_RADIUS_KM", default=3.0)
POOLING_MAX_DETOUR_KM = env.float("POOLING_MAX_DETOUR_KM", default=2.0)
POOLING_MAX_DETOUR_PERCENT = env.float("POOLING_MAX_DETOUR_PERCENT", default=0.25)
```

## Rollout plan

1. Ship `TripBatch` / `RouteStop` with `POOLING_ENABLED=False` — every
   trip creates a size-1 batch, driver endpoints move to
   `/driver/batches/...`, but `find_pooling_candidate` is never called.
   This alone is a real, testable migration (Trip → Batch/Stop) with zero
   behavior change.
   
2. Turn pooling on for a single vehicle category (bikes — smallest
   detour cost when wrong) behind the flag, watch actual detour-vs-estimate
   accuracy before trusting it for cargo-sized vehicle types where a bad
   pooling call costs a lot more driver time.

3. Revisit the brute-force ordering search once/if
   `POOLING_MAX_ORDERS_PER_BATCH` needs to go past 3.
