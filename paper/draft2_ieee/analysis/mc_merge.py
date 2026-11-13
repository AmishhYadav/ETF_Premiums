"""Merge the three per-design Monte Carlo files into mc_results.json."""
import json
R = {mu: json.load(open(f"mc_part_{float(mu)}.json")) for mu in ("0.1", "0.2", "0.3")}
json.dump(R, open("mc_results.json", "w")); print({k: len(v) for k, v in R.items()})
