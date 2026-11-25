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

## Rebuild SOXL at 20 shares

SOXL ladder buys and sells are 20 shares. The standing sell of 150 at 75 is retired: reset and intraday do not place it, and soxl-rebuild cancels an open one. The standing buy of 400 at 18.7 (`soxl-buy-400-18-7-v2`) is placed by reset only, again after each reset until 400 shares have filled. An open match is kept. Intraday, adjust, cancel-dup, and soxl-rebuild leave it alone. The previous id `soxl-buy-400-18-7` does not block it. Ladder shares from sell 36 are 475 minus 100 base minus 120 exception, which is 255. At 20 shares a rung that book ends at 103 for the last 15 shares.

Actions → soxl-rebuild → Run workflow prints the cancel and place lines and sends nothing. The live input is off by default. An owner comment on issue **#18** whose body starts with `soxl-rebuild` does the same. That comment sends orders only when the repository variable `SOXL_REBUILD_LIVE` is exactly `on`. `LIVE_TRADING` does not turn it on. The job uses the same account as the ladder. It keeps the core pair at sell 36 / buy 30 and treats the buy of 4 at 30 as already handled, so a later reset does not step that fill. It cancels every open SOXL order in the ladder account, including one that is not a rung, except the standing buy of 400 at 18.7. It does not place that buy. It does not cancel KORU or NAIL. A reset that already has sell 20 at 36, sell 20 at 39, buy 20 at 30, and buy 20 at 28 leaves those orders in place. A buy that only changes the share count is not a ladder fill, so the anchor stays sell 36 / buy 30. After the cancels it reads the open book again and does not place a second order on a rung that is still open. A live run still stops for the time guards (08:00–09:10 and 16:00–17:10 KST while the US is on standard time, an hour earlier in KST during daylight time), which sit ahead of the 10:00 KST broker cancel.

## Check account 201-12

Actions → acct-check → Run workflow. The job requests an OAuth token and calls `/n2/acctinfo` only. It prints each account masked (for example `201-12-68***5`) with its `acct_type`, then `201-12 present yes` or `201-12 present no`. It does not place orders and does not write state.

## TQQQ standing buys

The TQQQ program is separate from the SOXL/KORU/NAIL ladder. It trades only the type-01 account whose number contains `201-12`, and it stores progress in `.tqqq-state/state.json`. It does not cancel other orders in that account. The same run also keeps one TLTW buy of 100 shares at 18.00 (`tltw-buy-100-18`) until that buy has filled 100 shares once. TQQQ levels are sent first, highest price first. Other TLTW and SCHD orders are left alone.

It runs weekdays at 10:11 KST (day session, order type 63) and 18:11 KST (US premarket, order type 61). On issue **#18**, from the repository owner account, a new comment whose body starts with `tqqq-reset` runs the same reset.

Orders stay off until the repository variable `TQQQ_LIVE` is exactly `on`. While it is unset or any other value, the run prints each planned buy with its price and quantity and places nothing. Do not set `NHPLUG_ACCOUNT_SUFFIX` on the ladder workflows. The TQQQ workflow sets it to `201-12` for itself.
