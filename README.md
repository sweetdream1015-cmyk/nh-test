# nh-test

## Kick an intraday run

The schedule trigger can lag by about an hour. An owner comment starts one `ladder-intraday` run without a personal access token.

1. Create one issue titled `ladder-kick` and add the label `ladder-kick`. The workflow does not create the issue or the label.
2. On that issue, from the repository owner account, post a new comment whose body starts with `kick`.

That comment starts `ladder-intraday` once, on the same intraday path as the schedule (`LADDER_MODE=intraday`), including the time guards. The guards follow US Eastern time: 18:00–19:10 and 02:00–03:10 ET, which is 08:00–09:10 and 16:00–17:10 KST in standard time and an hour earlier in KST during US daylight time. The run queues behind any ladder job already in progress. Editing an existing comment does not start a run. Any other comment skips the job: no secret is used and no broker call is made.

## Cancel one duplicate

On issue **#18** (the `ladder-kick` issue), from the repository owner account, post a new comment whose whole body is:

```
cancel-dup NAIL BUY 28 10
```

Ticker, side, price, and quantity, in that order. The run cancels one matching open order only when the broker inquiry shows two or more identical orders. Fewer than two cancels nothing. The log lists every open order for that ticker (side, price, quantity, order number, order date) before it decides. It uses the same time guards and the same full cancel as `ladder-adjust`, and it cancels nothing else. The order it cancels is the newest match (later order date, then higher order number).

## Reset the current slot

On issue **#18**, from the repository owner account, post a new comment whose whole body is one of:

```
reset
```

```
reset day
```

```
reset premarket
```

`reset` runs `ladder-reset` for whichever slot the clock is in. `reset day` is the 09:10 KST slot (order type 63 once the US after-hours book has closed). `reset premarket` is the 17:10 KST slot (order type 61 once US premarket is open). The other slot logs `reset skip slot` and places nothing. A slot already marked done today logs `OK skip` and places nothing. The time guards still apply. The run queues behind any ladder job already in progress. Editing an existing comment does not start a run.

If the scheduled reset never starts, the next `ladder-intraday` run after the slot opens places that same reset when the ladder book is empty. It does not cancel orders that are already open.

## Check account 201-12

Actions → acct-check → Run workflow. The job requests an OAuth token and calls `/n2/acctinfo` only. It prints each account masked (for example `201-12-68***5`) with its `acct_type`, then `201-12 present yes` or `201-12 present no`. It does not place orders and does not write state.

## TQQQ standing buys

The TQQQ program is separate from the SOXL/KORU/NAIL ladder. It trades only the type-01 account whose number contains `201-12`, and it stores progress in `.tqqq-state/state.json`. It does not cancel other orders in that account.

It runs weekdays at 10:11 KST (day session, order type 63) and 18:11 KST (US premarket, order type 61). On issue **#18**, from the repository owner account, a new comment whose body starts with `tqqq-reset` runs the same reset.

Orders stay off until the repository variable `TQQQ_LIVE` is exactly `on`. While it is unset or any other value, the run prints each planned buy with its price and quantity and places nothing. Do not set `NHPLUG_ACCOUNT_SUFFIX` on the ladder workflows. The TQQQ workflow sets it to `201-12` for itself.
