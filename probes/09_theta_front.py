"""Probe: 09-live-win-probability.md sec2.4 -- theta_front, the second serve-time constant.

09 sec2.4 forces an extra gate on front-of-field pairs. It must be a SERVE-TIME
CONSTANT for the same reason 08 sec11.1 gives for theta: a live consumer sees
one tick at a time and cannot take a percentile over a race in progress.

theta_front = 60th percentile of the CALIBRATION fold's predictions, restricted
to rows that are already in-domain (p_raw >= theta) AND have the pursuer inside
the top six. Train+calib only, never the test fold -- same discipline as theta.
"""
import sys
import overtake_fit as F
from lib import overtake_features as of

rows = F.load_matrix(F.DEFAULT_MATRIX)
rounds = sorted({r["round"] for r in rows})
names = of.FEATURE_NAMES
THETA = 0.0037

thetas, fronts, cov, surv = [], [], [], []
for i in range(4, len(rounds)):
    tr = [r for r in rows if r["round"] in rounds[:i-2]]
    ca = [r for r in rows if r["round"] in rounds[i-2:i]]
    te = [r for r in rows if r["round"] == rounds[i]]
    if not te or sum(r["label"] for r in te) == 0 or sum(r["label"] for r in tr) == 0:
        continue
    Xtr, rest, _ = F.standardize(tr, ca + te, names)
    w, b = F.fit_logistic(Xtr, [r["label"] for r in tr])
    p_ca = F.predict(rest[:len(ca)], w, b)
    p_te = F.predict(rest[len(ca):], w, b)
    thetas.append(F.percentile(list(p_ca), 0.80))
    front_ca = [p for r, p in zip(ca, p_ca) if p >= THETA and r["position"] <= 6]
    tf = F.percentile(front_ca, 0.60)
    fronts.append(tf)
    # what the gate costs on the TEST fold
    fr = [(p, r["label"]) for r, p in zip(te, p_te) if p >= THETA and r["position"] <= 6]
    kept = [(p, y) for p, y in fr if p >= tf]
    cov.append((len(fr), sum(y for _, y in fr), len(kept), sum(y for _, y in kept)))
    surv.extend(kept)
    print("R%-2d theta=%.5f theta_front=%.5f  front in-domain rows %5d pos %3d -> kept %5d pos %3d"
          % (rounds[i], thetas[-1], tf, cov[-1][0], cov[-1][1], cov[-1][2], cov[-1][3]))

print("\ntheta_front: mean %.5f  range %.5f-%.5f" % (sum(fronts)/len(fronts), min(fronts), max(fronts)))
tr_, tp_, kr_, kp_ = (sum(c[j] for c in cov) for j in range(4))
print("pooled test: front in-domain %d rows / %d overtakes -> after theta_front %d rows / %d overtakes"
      % (tr_, tp_, kr_, kp_))
print("  rows retained %.1f%%, overtakes retained %.1f%%, observed rate %.5f (was %.5f)"
      % (100*kr_/tr_, 100*kp_/tp_, kp_/kr_, tp_/tr_))

print("\n=== calibration of the surviving front-of-field set (n=%d, pos=%d), 3 bins ==="
      % (len(surv), sum(y for _, y in surv)))
surv.sort()
nb = 3; per = len(surv)//nb
for i in range(nb):
    ch = surv[i*per:(i+1)*per if i < nb-1 else len(surv)]
    pm = sum(p for p,_ in ch)/len(ch); om = sum(y for _,y in ch)/len(ch)
    print("  q%d n=%5d pred=%.5f obs=%.5f ratio=%.2f" % (i+1, len(ch), pm, om, pm/om if om else float("inf")))
