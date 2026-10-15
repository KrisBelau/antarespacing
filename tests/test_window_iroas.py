import sys, datetime as dt
sys.path.insert(0, "/home/user/antarespacing")
import pacing_tracker_daily as P

TODAY = P.TODAY
curve = {a: min(1.0, 0.55 + 0.45*a/27) for a in range(28)}   # 55% day 0 -> 100% day 27

# 7 complete days ending yesterday: six at $1,000 / 1.50x true, one thin $50 day at 6.00x.
# Spend-weighted answer is dominated by the $1,000 days; a mean of ratios is not.
detail = {}
for k in range(1, 8):
    d = (TODAY - dt.timedelta(days=k)).isoformat()
    sp, true_roas = (50.0, 6.0) if k == 4 else (1000.0, 1.50)
    detail[d] = (sp, sp * true_roas * curve[k])      # book only the matured share
# Partial day: spend is on the board, the conversions it drives have not booked yet.
# This is the realistic shape, and it reads low for reasons that are about the clock.
detail[TODAY.isoformat()] = (300.0, 20.0)

proj = P.matured(detail, curve)
w = P.window_iroas(proj, 1.0, 7, TODAY - dt.timedelta(days=1))

ratios = [proj[d][1]/proj[d][0] for d in
          [(TODAY - dt.timedelta(days=k)).isoformat() for k in range(1, 8)]]
mean_of_ratios = sum(ratios)/len(ratios)
expected = (6*1000*1.50 + 50*6.00) / (6*1000 + 50)

print(f"spend-weighted : {w:.4f}   expected {expected:.4f}")
print(f"mean of ratios : {mean_of_ratios:.4f}  <- what we are NOT doing")
assert abs(w - expected) < 1e-6, w
assert abs(mean_of_ratios - w) > 0.3, "fixture too weak to tell them apart"

# Today excluded. Including it drags the window DOWN - $300 of spend against barely
# any booked revenue - which is the whole reason the window ends yesterday.
w_incl = P.window_iroas(proj, 1.0, 8, TODAY)
assert w_incl < w - 0.05, (w, w_incl)
print(f"incl. today    : {w_incl:.4f}  <- {w-w_incl:.4f} lower, so excluded")

# empty window is absent, not zero
assert P.window_iroas({}, 1.0, 7, TODAY) is None
print("empty window   : None  (not 0.00x)")
print("PASS")
