"""Probe: 09-live-win-probability.md sec2.4 -- in-domain rows by position band.

Question: 08 sec11.1's "10/10 bins within 2x, in-domain" is pooled over ALL
in-domain rows. The win-probability layer leans hardest on FRONT-of-field pairs.
How many in-domain rows and positives actually sit at P1-P3?

Reproduces overtake_fit.recalibration_pass's nested folds exactly, then buckets
the test rows by the pursuer's `position` feature.
"""
import sys, collections
import overtake_fit as F
from lib import overtake_features as of

rows = F.load_matrix(F.DEFAULT_MATRIX)
rounds = sorted({r["round"] for r in rows})
names = of.FEATURE_NAMES

BANDS = [("P1-P3", 1, 3), ("P4-P6", 4, 6), ("P7-P10", 7, 10),
         ("P11-P15", 11, 15), ("P16+", 16, 99)]

def band(p):
    for nm, lo, hi in BANDS:
        if lo <= p <= hi:
            return nm
    return "other"

test_rows, praws, thetas = [], [], []
for i in range(4, len(rounds)):
    fit_rounds, calib_rounds, test_round = rounds[:i-2], rounds[i-2:i], rounds[i]
    tr = [r for r in rows if r["round"] in fit_rounds]
    ca = [r for r in rows if r["round"] in calib_rounds]
    te = [r for r in rows if r["round"] == test_round]
    if not te or sum(r["label"] for r in te) == 0 or sum(r["label"] for r in tr) == 0:
        continue
    ytr = [r["label"] for r in tr]
    Xtr, rest, _ = F.standardize(tr, ca + te, names)
    Xca, Xte = rest[:len(ca)], rest[len(ca):]
    w, b = F.fit_logistic(Xtr, ytr)
    p_ca = F.predict(Xca, w, b)
    p_te = F.predict(Xte, w, b)
    theta = F.percentile(list(p_ca), 0.80)
    thetas.append(theta)
    for r, p in zip(te, p_te):
        test_rows.append(r); praws.append(p)
    sys.stderr.write("fold R%d done theta=%.5f\n" % (test_round, theta))

theta_mean = sum(thetas) / len(thetas)
print("nested test rows: %d, positives: %d, per-fold theta mean %.5f"
      % (len(test_rows), sum(r["label"] for r in test_rows), theta_mean))

# in-domain by per-fold theta is what 08 reports; recompute with the FIXED
# serve-time constant 0.0037 too, since that is what 09 would actually use.
for label, thr in (("per-fold theta", None), ("fixed theta=0.0037", 0.0037)):
    stat = collections.defaultdict(lambda: [0, 0, 0, 0])  # rows, pos, indom_rows, indom_pos
    t = theta_mean if thr is None else thr
    for r, p in zip(test_rows, praws):
        bn = band(int(r["position"]))
        s = stat[bn]
        s[0] += 1; s[1] += r["label"]
        if p >= t:
            s[2] += 1; s[3] += r["label"]
    print("\n=== in-domain by position band (%s -> %.5f) ===" % (label, t))
    print("%-8s %10s %8s %12s %10s %10s %10s" %
          ("band", "rows", "pos", "indom_rows", "indom_pos", "cov_pos%", "obs_rate"))
    for nm, _, _ in BANDS:
        s = stat[nm]
        if s[0] == 0: continue
        print("%-8s %10d %8d %12d %10d %9.1f%% %10.5f" %
              (nm, s[0], s[1], s[2], s[3],
               100.0*s[3]/s[1] if s[1] else 0.0,
               s[3]/s[2] if s[2] else 0.0))

# calibration inside the front band only, fixed theta
t = 0.0037
front = [(p, r["label"]) for r, p in zip(test_rows, praws)
         if p >= t and int(r["position"]) <= 6]
front.sort()
print("\n=== calibration, in-domain AND pursuer in P2-P6 (n=%d, pos=%d) ==="
      % (len(front), sum(y for _, y in front)))
if len(front) >= 100:
    nb = 5
    per = len(front)//nb
    for i in range(nb):
        chunk = front[i*per: (i+1)*per if i < nb-1 else len(front)]
        pm = sum(p for p, _ in chunk)/len(chunk)
        om = sum(y for _, y in chunk)/len(chunk)
        print("  q%d n=%5d pred=%.5f obs=%.5f ratio=%.2f"
              % (i+1, len(chunk), pm, om, (pm/om if om else float('inf'))))
