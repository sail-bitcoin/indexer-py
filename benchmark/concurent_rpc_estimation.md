# Connection pool size per request

## First reasoning

Context: in the case of using an external RPC provider with rate limits, we want to optimize our througput

Let's consider QuickNode free RPC tier, with a rate limit of 15 requets per second.

In order to save a block B data into our database were are calling the RPC twice: `getblockhash` and `getblock`. The later is called with `verbosity=2` which means that it contains all block's transactions data. Therefore, the second request is significantly longer than the first one.

For QuickNode's RPC we measure these results using `benchmark/rpc_latency.py`:
```json
{
  "rpc_host": "quiknode.pro",
  "start_height": 878031,
  "n_blocks": 100,
  "get_block_hash": {
    "avg": 0.15150553952043994,
    "median": 0.12156585849879775,
    "min": 0.10985802300274372,
    "max": 0.5383430090005277,
    "p10": 0.11362811199796852,
    "p90": 0.21892346618988084
  },
  "get_block": {
    "avg": 0.8827184411302733,
    "median": 0.6926163145035389,
    "min": 0.3508396389952395,
    "max": 5.967668416997185,
    "p10": 0.42492448750126643,
    "p90": 1.3400334390025819
  }
}
```

Let's consider `latency{H}` and `latency{B}` both on `average` and `p90`.

Little's law give us: `concurency = rate * latency`

Let's assume that we want to define different rating mechanism for method as their latencies are at significant different scale.

Then, the goal of these calculations is to estimate as efficiently as possible the number of connection pool we will set up for both of them. We have the equation: `C = C{H} + C{B}`

On average latencies, it gives us: 
```
C = 150*L{H} + 900*L{B}
```

We assume that the total rate `R` also as a linear relationship to the sum of both rates: `R = R{H} + R{B}`, and we know that in this case `R = 15 req/s`

We now have:
```
15 req/s = R{H} + R{B}  <=>   15 = C{H}/0.15 + C{B}/0.9   <=>   C{B} = 13.5 - 6*C{H}
```

As concurency values need to be `> 0` we only have these results possible for `[C{H}, C{B}]`: `[1, 7.5] [2, 1.5]`

The same calculations for `p90` give:
```
C = 0.15*L{H} + 0.9*L{B}

15 req/s = R{H} + R{B}  <=>   15 = C{H}/0.2 + C{B}/1.3   <=>   C{B} = 19.5 - 6.5*C{H}
```
Which gives: `[1, 13] [2, 6.5]`


Now that have estimations of the potential concurency allowed for both of our method, and we have their latencies, we can compute both their rate. 
Let's calculate the time to process N blocks depending of the possible concurency values. What's interest us here is that to be able to process `getblock` method, we need to have the block's hash. Then, `R{H}` needs to be `> R{B}`.

On average:

|  [C{H}, C{B}] |  R{H}     |    R{B}        |      
|-------------|-------------|-----------------|
| [1, 7]        |  6.67        |  7.78     |
| [2, 1]      |  13.33        |  1.6           |


90th percentile:

|  [C{H}, C{B}] |  R{H}     |    R{B}        |      
|-------------|-------------|-----------------|
| [1, 13]        |  5        |  10     |
| [2, 7]      |  10        |  5,38           |


Here, the only set of value that fulfill the requirement `R{H} > R{B}` while keeping both values not too is `C{H} = 2` and `C{B} = 7`

For processing `N = 10,00 blocks` it means that each method will take `N/R = N*L/C` which gives `10,000*L{B}/2` for `getblockhash` and `10,000*L{B}/7` for `getblock`:

- avg: `12 min` to query block's hash and `21 min` to query the block
- p90: `17 min` to query block's hash and `31 min` to query the block

---

## Second reasoning

Another way to compute, and that confirm our results is to start iterating `C{H}` from `1`, estimating that `L{H} = 0.2` (as it's distribution is quite tight), and compute the other variables and check if the conditions are checked.

Going through `C{H} = 1` we will find that `R{H} < R{B}` which can be a solution here.

With `C{H} = 2` and `L{H}=0.2` we find `R{H} = 10`. From here we consider the extra `5 req/sec` is for `R{B}`, and compute `C{B}` to be somewhere between `4.5` and `6.5` (avg, p90 latencies). We find a similar results as the one from the first long-detailed reasoning.

And we stop iterating at `C{H} = 3` as `R{H} = 15` which won't let any request per second available for `getblock`

This second fast-iterating reasoning confirm our hypothesis and results below, meaning that for our use case, for an external RPC provider with rating limit to be `15 req/s` we should define a ratio for the `getblockhash` method using `Async Limiter` to be setted at 


---

## Final results

### `getblockhash`
Fast, and easy to estimate, we should simply use a rate limiter like `Async Limiter` to fix it's rate to `10 req/second` 

### `getblock`
Waiting `getblockhash` calls to finish, `getblock` should have a ratio of `5 req/second` as a minimum, and should go up to `15` when hash coroutines will be done. 
This maximum of `C{B}` would need to be equal to: 
- `10.35` for latency median
- `13.5` for latency average
- `19.5` for latency p90


---
- Expected IO-time (rpc calls) with one RPC: 25,000 blocks per day, taking an hour per day for 40 days to sync (rate limit of 50,000 req/s on QuickNode)
- add the cpu-time 
- consider using several RPC urls at the same time

