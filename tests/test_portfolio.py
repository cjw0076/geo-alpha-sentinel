"""Offline tests for the portfolio lens: 13F / N-PORT parsing, aggregation math, joint Monte Carlo."""

from pathlib import Path

import pytest

from sentinel.agent import verifier
from sentinel.analytics import portfolio as P
from sentinel.analytics.stress import run_stress
from sentinel.ledger import Ledger
from sentinel.models import Evidence, HoldingRow, HoldingsBook, Provenance, Scenario
from sentinel.sources import holdings as H

FIX = Path(__file__).resolve().parent / "fixtures"


def prov(title="fixture"):
    return Provenance(source="sec_edgar", title=title, url="https://www.sec.gov/", retrieved_at="2026-10-31T00:00:00Z", license="Public domain (SEC filing)")


# ---- Form 13F --------------------------------------------------------------

def test_13f_infotable_parse_and_dollar_detection():
    rows = H.parse_13f_infotable((FIX / "13f_infotable_dollars.xml").read_bytes())
    cover = H.parse_13f_cover((FIX / "13f_cover.xml").read_bytes())
    assert len(rows) == 5 == cover["table_entry_total"]
    assert cover["period"] == "2026-06-30" and cover["table_value_total"] == 20_500_000
    assert [r["put_call"] for r in rows].count("Call") == 1
    mult, why = H.value_multiplier(rows, cover["table_value_total"], filed="2026-08-12")
    assert mult == 1 and "dollars" in why
    book, options = H.aggregate_13f(rows, mult)
    assert len(book) == 3 and len(options) == 1            # ALPHA aggregated over two rows; the call is listed separately
    alpha = next(r for r in book if r.cusip == "000000A01")
    assert alpha.value_usd == 7_000_000 and alpha.shares == 58_000
    assert options[0].weight == 0.0 and options[0].put_call == "Call"
    assert sum(r.weight for r in book) == pytest.approx(1.0, abs=1e-12)
    assert alpha.weight == pytest.approx(7_000_000 / 20_000_000)   # option value excluded from the denominator


def test_13f_thousands_detection():
    rows = H.parse_13f_infotable((FIX / "13f_infotable_thousands.xml").read_bytes())
    # pre-2023 filing: info table and cover both in thousands -> implied price/share < $1 -> x1000
    mult, why = H.value_multiplier(rows, 20_500, filed="2022-11-10")
    assert mult == 1000 and "thousands" in why
    # info table in thousands but cover total in dollars -> ratio 1000 -> x1000
    mult, _ = H.value_multiplier(rows, 20_500_000, filed="2026-08-12")
    assert mult == 1000
    book, _ = H.aggregate_13f(rows, 1000)
    assert sum(r.value_usd for r in book) == 20_000_000
    with pytest.raises(ValueError):
        H.value_multiplier(rows, 99_999, filed="2026-08-12")


# ---- Form N-PORT -----------------------------------------------------------

def test_nport_parse_korea_removed_and_renormalised():
    info, rows = H.parse_nport((FIX / "nport_small.xml").read_bytes())
    assert info["series_name"] == "Example MSCI ACWI ETF" and info["period_end"] == "2026-07-31"
    assert len(rows) == 7
    adr = next(r for r in rows if r["name"].startswith("Example ADR"))
    assert adr["asset_cat"] == "OTHER" and adr["asset_desc"] == "ADR"
    book, excl, stats = H.nport_equity_book(rows)
    assert {r.name for r in book} == {"US ONE INC", "US TWO INC", "Japan Example Corporation"}   # EC only, KR dropped
    assert [r.country for r in excl] == ["KR"]
    assert sum(r.weight for r in book) == pytest.approx(1.0, abs=1e-12)
    w = {r.name: r.weight for r in book}
    assert w["US ONE INC"] == pytest.approx(400 / 800) and w["Japan Example Corporation"] == pytest.approx(200 / 800)
    assert stats["removed_share_of_equity"] == pytest.approx(100 / 900)


def test_identifier_keys_and_name_normalisation():
    assert H.identifier_key({"cusip": "037833100", "isin": "US0378331005"}) == "CUSIP:037833100"
    assert H.identifier_key({"cusip": None, "isin": "US0378331005"}) == "CUSIP:037833100"
    assert H.identifier_key({"cusip": None, "isin": "TW0002330008"}) == "ISIN:TW0002330008"
    assert H.identifier_key({"cusip": "000000000", "isin": None}) is None
    n = H.normalise_name
    assert n("Taiwan Semiconductor Manufacturing Company Limited") == n("TAIWAN SEMICONDUCTOR MANUFACTURING CO LTD") == "TAIWAN SEMICONDUCTOR MANUFACTURING"
    assert n("ASML Holding N.V.") == n("ASML HOLDING NV") == "ASML HOLDING"
    assert n("Novo Nordisk A/S") == n("NOVO NORDISK A S") == "NOVO NORDISK"
    assert n("The Toronto-Dominion Bank") == "TORONTO DOMINION BANK"
    assert H.loose_name("EXXON MOBIL CORP") == H.loose_name("ExxonMobil Holdings Corp") == "EXXONMOBIL"
    assert H._figi_job("CUSIP:G1151C101") == {"idType": "ID_CINS", "idValue": "G1151C101", "exchCode": "US"}     # CINS (foreign issuer)
    assert H._figi_job("CUSIP:037833100")["idType"] == "ID_CUSIP" and H._figi_job("ISIN:TW0002330008")["idType"] == "ID_ISIN"


# ---- aggregation math ------------------------------------------------------

def vec(t, chans, rev=100e9, gm=0.5, fcf=10e9, base_year=2025, verdict="HOLD"):
    return {"ticker": t, "cik": f"c{t}", "name": f"{t} Corp", "chans": dict(zip(P.CHANNELS, chans)), "rev": rev, "gm": gm, "fcf": fcf, "base_year": base_year,
            "verdict": verdict, "filing": {"form": "10-K", "filed": "2026-02-01", "url": f"https://www.sec.gov/{t}"}, "retrieved_at": "2026-10-31T00:00:00Z"}


VECS = {
    "AAA": vec("AAA", [0.9, 0.6, 0.1, 0.5], verdict="REDUCE"),
    "BBB": vec("BBB", [0.1, 0.2, 0.8, 0.1], gm=0.3, fcf=2e9, verdict="HEDGE"),
    "CCC": vec("CCC", [0.0, 0.1, 0.0, 0.0], base_year=2026, verdict="ACCUMULATE"),
    "DDD": vec("DDD", [0.3, 0.3, 0.3, 0.3], fcf=-1e9, verdict="HOLD"),
}


def pos(t, w, filer=True):
    return {"key": f"cik:c{t}", "ticker": t, "cik": f"c{t}", "name": f"{t} Corp", "weight": w, "value_usd": w * 1e9, "sec_filer": filer, "ids": [f"{t}0000001"]}


def test_aggregation_weights_and_active():
    book = [pos("AAA", 0.5), pos("BBB", 0.3), pos("CCC", 0.1), pos("ZZZ", 0.1, filer=False)]
    bench = [pos("AAA", 0.2), pos("BBB", 0.2), pos("DDD", 0.6)]
    cb, fb = P.attach_vectors(book, VECS)
    cm, _ = P.attach_vectors(bench, VECS)
    assert [f["ticker"] for f in fb] == ["ZZZ"]
    wb = P.renormalise(cb)
    assert sum(wb.values()) == pytest.approx(1.0)
    assert wb["cAAA"] == pytest.approx(0.5 / 0.9)
    tab = P.channel_table(cb, cm)
    for k_i, k in enumerate(P.CHANNELS):
        e = sum(wb[f"c{t}"] * VECS[t]["chans"][k] for t in ("AAA", "BBB", "CCC"))
        b = 0.2 * VECS["AAA"]["chans"][k] + 0.2 * VECS["BBB"]["chans"][k] + 0.6 * VECS["DDD"]["chans"][k]
        assert tab[k]["book"] == pytest.approx(e)
        assert tab[k]["bench"] == pytest.approx(b)
        assert tab[k]["active_pp"] == pytest.approx(100 * (e - b))
        assert sum(tab[k]["contrib"].values()) == pytest.approx(e)
        assert sum(tab[k]["active"].values()) == pytest.approx(e - b)          # active contributions on the union
        if e > 0:
            assert tab[k]["effective_n"] == pytest.approx(1 / sum((c / e) ** 2 for c in tab[k]["contrib"].values()))


def test_selection_rules():
    ps = [pos(f"T{i}", w) for i, w in enumerate([0.4, 0.3, 0.15, 0.1, 0.05])]
    assert [p["ticker"] for p in P.select_book(ps, target=0.9, max_names=300)] == ["T0", "T1", "T2", "T3"]   # 0.95 >= 0.90
    assert len(P.select_book(ps, target=0.9, max_names=2)) == 2
    ps[1]["sec_filer"] = False
    assert [p["ticker"] for p in P.select_bench(ps, max_filers=2)] == ["T0", "T2"]


def test_positions_merge_share_classes():
    rows = [HoldingRow(name="ALPHABET INC", cusip="02079K305", ticker="GOOGL", cik="0001652044", value_usd=60, weight=0.6, map_method="openfigi", sec_filer=True),
            HoldingRow(name="ALPHABET INC", cusip="02079K107", ticker="GOOG", cik="0001652044", value_usd=30, weight=0.3, map_method="openfigi", sec_filer=True),
            HoldingRow(name="NO MAP", cusip="999999999", value_usd=10, weight=0.1)]
    book = HoldingsBook(name="t", as_of="2026-06-30", source=prov(), total_value_usd=100, rows=rows)
    ps = P.positions(book)
    assert len(ps) == 2 and ps[0]["ticker"] == "GOOGL" and ps[0]["weight"] == pytest.approx(0.9)


# ---- joint Monte Carlo -----------------------------------------------------

@pytest.mark.parametrize("t", ["AAA", "BBB", "CCC", "DDD"])
def test_joint_mc_one_issuer_parity(t):
    v = VECS[t]
    sc = Scenario()
    st = run_stress(sc, v["chans"], base_revenue=v["rev"], gross_margin=v["gm"], base_fcf=v["fcf"], base_year=v["base_year"], paths=20000, seed=42)
    r = P.joint_stress([v], [1.0], [1.0], sc, paths=20000, seed=42, issuer_floor=-1e18)   # floor disabled: exact replica
    assert abs(r["book"]["p05"] - st.fcf_shock_pct["p05"]) <= 1.0
    assert abs(r["book"]["p50"] - st.fcf_shock_pct["p50"]) <= 1.0
    assert r["book"]["p_below_20"] / 100 == pytest.approx(st.prob_fcf_drawdown_gt_20, abs=1e-3)
    d = P.joint_stress([v], [1.0], [1.0], sc, paths=20000, seed=42)                          # default floor (-100%)
    assert abs(d["book"]["p50"] - max(-100.0, st.fcf_shock_pct["p50"])) <= 1.0
    assert abs(d["book"]["p05"] - max(-100.0, st.fcf_shock_pct["p05"])) <= 1.0


def test_joint_mc_deterministic_and_common_shock():
    vs = [VECS[t] for t in ("AAA", "BBB", "CCC", "DDD")]
    a = P.joint_stress(vs, [0.4, 0.3, 0.2, 0.1], [0.1, 0.2, 0.3, 0.4], paths=4000, seed=7)
    b = P.joint_stress(vs, [0.4, 0.3, 0.2, 0.1], [0.1, 0.2, 0.3, 0.4], paths=4000, seed=7)
    c = P.joint_stress(vs, [0.4, 0.3, 0.2, 0.1], [0.1, 0.2, 0.3, 0.4], paths=4000, seed=8)
    for k in ("book", "bench"):
        assert a[k] == b[k]
    assert a["tail_contrib_pp"] == b["tail_contrib_pp"]
    assert a["book"] != c["book"]
    # issuer marginals in the joint program equal each issuer's own run_stress (same seed, same draw order)
    for j, v in enumerate(vs):
        st = run_stress(Scenario(), v["chans"], base_revenue=v["rev"], gross_margin=v["gm"], base_fcf=v["fcf"], base_year=v["base_year"], paths=4000, seed=7)
        assert a["issuer_p05_pct"][j] == pytest.approx(st.fcf_shock_pct["p05"], abs=0.006)
    # tail contributions decompose the book's ES95
    assert sum(a["tail_contrib_pp"]) == pytest.approx(a["book"]["es95"], abs=1e-6)
    assert a["book"]["es95"] <= a["book"]["p05"] <= a["book"]["p50"]


# ---- assembled view: evidence integrity -------------------------------------

def chokepoint(cid, name, delta, base, mean):
    return {"id": cid, "name": name, "delta_pct": delta, "baseline_365d": base, "mean_7d": mean, "latest": {"date": "2026-10-30", "n_total": 1},
            "provenance": {"source": "imf_portwatch", "title": f"PortWatch {name}", "url": "https://portwatch.imf.org/", "retrieved_at": "2026-10-31T00:00:00Z",
                           "license": "IMF PortWatch — attribution"}}


def test_view_actions_and_headline_evidence_exist():
    book = [pos("AAA", 0.5), pos("BBB", 0.3), pos("CCC", 0.1), pos("ZZZ", 0.1, filer=False)]
    bench = [pos("AAA", 0.2), pos("BBB", 0.2), pos("DDD", 0.6)]
    chks = [chokepoint("chokepoint6", "Strait of Hormuz", -40.0, 100.0, 60.0), chokepoint("chokepoint11", "Taiwan Strait", -30.0, 300.0, 210.0),
            chokepoint("chokepoint5", "Malacca Strait", -5.0, 200.0, 190.0)]
    meta = {"id": "t", "name_ko": "테스트 북", "name_en": "Test book", "as_of": "2026-06-30", "filed": "2026-08-12", "holdings_n": 4, "value_usd": 1e9,
            "subject_ko": "테스트 북의", "subject_en": "the test book", "source": prov("13F"), "weight_basis": "13f"}
    bmeta = {"name_ko": "벤치", "name_en": "Bench", "as_of": "2026-07-31", "source": prov("N-PORT")}
    view = P.build_view(meta, bmeta, book, bench, bench, VECS, chks, paths=3000, seed=42, n_actions=3)
    ids = {e["id"] for e in view["evidence"]}
    assert len(ids) == len(view["evidence"])                                   # unique ids
    assert view["book"]["covered_n"] == 3 and view["book"]["covered_weight"] == pytest.approx(0.9)
    assert [f["ticker"] for f in view["failures"]] == ["ZZZ"]
    assert len(view["actions"]) == 3
    ledger = Ledger()
    ledger.items = [Evidence(**e) for e in view["evidence"]]
    for a in view["actions"]:
        assert set(a["evidence_ids"]) <= ids
        assert set(verifier.TAG.findall(a["reason_ko"])) <= set(a["evidence_ids"])
        ok, why = verifier.check_text(a["reason_ko"], ledger)
        assert ok, why
    assert view["headline"]["channel"] == "taiwan"                              # largest shortfall: 90 ships/day vs 40
    assert set(view["headline"]["evidence_ids"]) <= ids and len(view["headline"]["evidence_ids"]) == 4      # Δ, material share, threshold, bench share
    assert "실질 노출(노출도 ≥ 0.5)" in view["headline"]["ko"]
    for ch in view["channels"]:
        assert set(ch["evidence_ids"]) <= ids
        assert all(t["evidence_id"] in ids for t in ch["top"] + ch["top_active"])
        # book_pct recomputed from the holdings list
        num = sum(h["weight_pct"] * h["chans"][ch["id"]] for h in view["holdings"])
        assert 100 * num / sum(h["weight_pct"] for h in view["holdings"]) == pytest.approx(ch["book_pct"], abs=0.01)
        mat = sum(h["weight_pct"] for h in view["holdings"] if h["chans"][ch["id"]] >= 0.5)
        assert 100 * mat / sum(h["weight_pct"] for h in view["holdings"]) == pytest.approx(ch["book_material_pct"], abs=0.01)
        assert ch["material_active_pp"] == pytest.approx(ch["book_material_pct"] - ch["bench_material_pct"], abs=0.02)
    assert view["stress"]["evidence_id"] in ids
    assert sum(view["verdict_distribution"].values()) == view["book"]["covered_n"]


def test_issuer_floor_bounds_portfolio_and_quality_gates():
    tiny = vec("TNY", [0.9, 0.9, 0.9, 0.9], rev=100e9, gm=0.5, fcf=1e6)            # FCF near zero: its own shock is unbounded
    r = P.joint_stress([tiny, VECS["AAA"]], [0.5, 0.5], [0.0, 1.0], paths=4000, seed=42)
    assert r["issuer_p05_pct"][0] < -1000                                           # marginal reported without the floor
    assert r["book"]["p05"] >= -100.0 - 1e-9 and r["book"]["es95"] >= -100.0 - 1e-9 # each holding loses at most its base FCF
    assert sum(r["tail_contrib_pp"]) == pytest.approx(r["book"]["es95"], abs=1e-6)
    assert P.quality_issue({**VECS["AAA"], "rev": 1.0}) .startswith("no USD revenue")
    assert "gross margin" in P.quality_issue({**VECS["AAA"], "gm": 4.8e9})
    assert "FY2019" in P.quality_issue({**VECS["AAA"], "base_year": 2019}, "2026-06-30")
    assert "filed 2011" in P.quality_issue({**VECS["AAA"], "filing": {"form": "20-F", "filed": "2011-06-24"}}, "2026-06-30")
    assert P.quality_issue(VECS["AAA"], "2026-06-30") is None
    assert H.is_current_filer({"filed": "2025-03-01"}, "2026-06-30") and not H.is_current_filer({"filed": "2024-02-23"}, "2026-06-30")
