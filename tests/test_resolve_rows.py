import sys, json, os
sys.path.insert(0, "/home/user/antarespacing")
import pacing_tracker_daily as P

def camp(name, plat="Google Ads", mtd=100.0):
    return {"platform": plat, "name": name, "mtd_spend": mtd, "mtd_cv": 0.0,
            "l30_spend": mtd, "l30_cv": 0.0}

# The 2026-10-30 shape: the video campaign was renamed ATA -> ATU; the old UK remarketing
# campaign was renamed "DNU ..." and a NEW campaign took its old name.
data = {
    "google_ads:24286409891": camp("Google Ads | ATU | Video | Views | NA | EN", mtd=10121.11),
    "google_ads:22855865707": camp("DNU Google Ads | ATU | Demand Gen | Remarketing | UK | EN", mtd=196.78),
    "google_ads:24401035329": camp("Google Ads | ATU | Demand Gen | Remarketing | UK | EN", mtd=196.39),
    "google_ads:1":           camp("Google Ads | ATU | Search | LV | NA | EN", mtd=23353.73),
}
rows = [
    "Google Ads | ATA | Video | Views | NA | EN",                  # old name, pinned by ID
    "DNU Google Ads | ATU | Demand Gen | Remarketing | UK | EN",
    "Google Ads | ATU | Search | LV | NA | EN",                    # not pinned: name fallback
    "Microsoft Ads | ATU | Search | HV | NA | EN",                 # pinned, no spend this window
    "Some row nobody mapped",
]
id_map = {
    "Google Ads | ATA | Video | Views | NA | EN": "google_ads:24286409891",
    "DNU Google Ads | ATU | Demand Gen | Remarketing | UK | EN": "google_ads:22855865707",
    "Microsoft Ads | ATU | Search | HV | NA | EN": "bing:568379531",
}

resolved, by_name, unresolved, dupes = P.resolve_rows(rows, data, id_map)

# Rename: the old-named row gets the campaign's full spend, under its ID.
assert resolved["Google Ads | ATA | Video | Views | NA | EN"] == "google_ads:24286409891"
# DNU row stays on the OLD campaign, not the new one that inherited its name.
assert resolved["DNU Google Ads | ATU | Demand Gen | Remarketing | UK | EN"] == "google_ads:22855865707"
# Unpinned row still works by current name, and is reported as such.
assert resolved["Google Ads | ATU | Search | LV | NA | EN"] == "google_ads:1"
assert by_name == ["Google Ads | ATU | Search | LV | NA | EN"]
# Pinned but absent from the pull: resolved, so the caller writes $0 rather than stale data.
assert resolved["Microsoft Ads | ATU | Search | HV | NA | EN"] == "bing:568379531"
assert "bing:568379531" not in data
assert unresolved == ["Some row nobody mapped"]
assert dupes == []
# The new UK campaign has no row: it is what the digest reports as unclaimed.
assert set(data) - set(resolved.values()) == {"google_ads:24401035329"}
print("rename, DNU split, name fallback, $0 row, unclaimed: ok")

# Two rows pointing at one campaign: second is skipped, not double-counted.
_, _, _, dupes = P.resolve_rows(
    ["Google Ads | ATA | Video | Views | NA | EN", "Google Ads | ATU | Video | Views | NA | EN"],
    data, {"Google Ads | ATA | Video | Views | NA | EN": "google_ads:24286409891"})
assert dupes == [("Google Ads | ATU | Video | Views | NA | EN",
                  "Google Ads | ATA | Video | Views | NA | EN")], dupes
print("duplicate claim: ok")

# Two campaigns sharing a current name is ambiguous: no guess, the row goes unresolved.
amb = {"google_ads:a": camp("Same"), "google_ads:b": camp("Same")}
_, _, unresolved, _ = P.resolve_rows(["Same"], amb, {})
assert unresolved == ["Same"]
print("ambiguous name: ok")

# The committed map: every value is connector:id on a connector the routine pulls.
m = P.load_campaign_ids()
assert m and all(v.split(":")[0] in {c for _p, c, _s, _f in P.PLATFORMS} for v in m.values())
print(f"campaign_ids.json: {len(m)} entries, all on known connectors")
