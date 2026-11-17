"""One bounded multi-factor experiment, with mandatory M3A compatibility gate."""
import argparse
from collections import Counter
from datetime import date, datetime, timezone
from hashlib import sha256
from importlib.metadata import version
import json
from pathlib import Path
import platform
import statistics
import tomllib
from uuid import uuid4

import polars as pl

from ..labels import forward_returns
from ..m3a.data import LakeReader
from ..m3a.universe import add_eligibility
from ..m3a.experiment import LIMITATIONS
from . import VERSION
from .registry import REGISTRY
from .factors import calculate
from .data import load_amounts, audit_amounts
from .evaluation import matched_keys, freeze_assignments, evaluate

KEYS = ["symbol", "trade_date"]


def fingerprint(path):
    return sha256(path.read_bytes()).hexdigest()


def regression(reference, table, assignments, metrics, daily, labels, source_root):
    saved=json.loads((reference/"results.json").read_text(encoding="utf-8"))
    for name,digest in saved["provenance"]["source_sha256"].items():
        if fingerprint(source_root/name)!=digest:
            raise ValueError(f"M3A source changed: {name}")
    expected=pl.read_parquet(reference/"factors.parquet").join(table.select(KEYS),on=KEYS,how="semi").sort(KEYS)
    actual=table.select(*KEYS,pl.col("factor_value").alias("momentum")).sort(KEYS)
    if not actual.equals(expected):
        raise ValueError("Momentum 60 factor values differ from M3A")
    baseline=pl.read_parquet(reference/"assignments.parquet").sort(KEYS)
    if not assignments.rename({"factor_value":"momentum"}).sort(KEYS).equals(baseline):
        raise ValueError("Momentum 60 assignments differ from M3A")
    if not labels.sort(KEYS).equals(pl.read_parquet(reference/"labels.parquet").sort(KEYS)):
        raise ValueError("Forward labels differ from M3A")
    # Group-order may change the ordering of reason lists, but not numerical metrics.
    baseline_metrics=saved["metrics"]
    for key in ["mean_daily_rank_ic","valid_ic_dates","assigned_dates","primary_comparison_dates","primary_high_minus_low","outcomes","buckets"]:
        if metrics[key]!=baseline_metrics[key]:
            raise ValueError(f"M3A numerical regression mismatch: {key}")
    if daily!=saved["daily_metrics"]:
        raise ValueError("M3A daily numerical regression mismatch")
    return {"passed":True,"factor_values":"exact","assignments":"exact","labels":"exact","IC_and_quantiles":"exact","daily_metrics":"exact","legacy_source_hashes":"unchanged"}


def compare(results, scope):
    all_dates=None
    for item in results.values():
        dates={r["trade_date"] for r in item["daily"] if r["primary_comparison"]}
        all_dates=dates if all_dates is None else all_dates & dates
    all_dates=all_dates or set()
    rows=[]
    for factor_id,item in results.items():
        m=item["metrics"]
        common=[r for r in item["daily"] if r["trade_date"] in all_dates]
        rows.append({"scope":scope,"factor_id":factor_id,"mean_daily_rank_ic":m["mean_daily_rank_ic"],"IC_dates":m["valid_ic_dates"],"assigned":m["outcomes"]["assigned"],"valid_outcomes":m["outcomes"]["valid"],"missing_outcomes":m["outcomes"]["missing"],"missing_rate":m["outcomes"]["missing_rate"],"primary_dates":m["primary_comparison_dates"],"cross_factor_common_primary_dates":len(common),**{f"q{q}_common_date_mean":statistics.mean(r["buckets"][str(q)]["mean_return"] for r in common) if common else None for q in [1,2,3]}})
    return rows


def run(config_path):
    started=datetime.now(timezone.utc)
    research=Path(__file__).resolve().parents[3]
    config_bytes=config_path.read_bytes(); cfg=tomllib.loads(config_bytes.decode("utf-8"))
    start,end=date.fromisoformat(cfg["evaluation_start"]),date.fromisoformat(cfg["evaluation_end"])
    if (start,end)!=(date(2020,1,2),date(2021,12,31)) or cfg["horizon"]!=20 or cfg["quantiles"]!=3:
        raise ValueError("Only approved bounded 2020–2021 experiment supported")
    reference=(research/"artifacts"/cfg["m3a_reference"]).resolve()
    if reference.parent!=(research/"artifacts").resolve():
        raise ValueError("Reference must be a direct artifact directory")
    reference_hashes={p.name:fingerprint(p) for p in reference.iterdir() if p.is_file()}
    saved=json.loads((reference/"results.json").read_text(encoding="utf-8"))
    selected=pl.read_parquet(reference/"sample_manifest.parquet")
    if selected.height!=100 or selected.group_by("exchange").len().sort("exchange")["len"].to_list()!=[50,50]:
        raise ValueError("Invalid frozen M3A cohort")
    for key in ["quantiles","min_cross_section","min_bucket","min_paired","coverage_threshold"]:
        if cfg[key]!=saved["provenance"]["configuration"][key]:
            raise ValueError("M3A evaluation policy must match")
    reader=LakeReader(Path(cfg["data_root"]))
    # Historical inputs must be identical, not merely the same symbol list.
    for name,digest in saved["provenance"]["input_file_sha256"].items():
        if fingerprint(reader.root/name)!=digest:
            raise ValueError(f"Historical input changed since M3A: {name}")
    cal=reader.calendar(2019,2022)
    sessions=cal.filter(pl.col("is_trading"))["trade_date"].to_list()
    evaluation_days=[d for d in sessions if start<=d<=end]
    begin=sessions.index(evaluation_days[0])-120
    finish=sessions.index(evaluation_days[-1])+20
    if begin<0 or finish>=len(sessions):
        raise ValueError("Insufficient calendar padding")
    days=sessions[begin:finish+1]
    if cal.filter(pl.col("trade_date").is_between(days[0],days[-1])).height!=(days[-1]-days[0]).days+1:
        raise ValueError("Incomplete calendar")
    symbols=selected["symbol"].to_list()
    panel=add_eligibility(reader.prices(symbols,days),selected)
    panel=panel.with_columns(pl.when(pl.col("listing_eligible")).then(pl.col("analytical_close")).otherwise(None).alias("analytical_close"))
    panel=panel.join(reader.status(symbols,days[0],days[-1]),on=KEYS,how="left",validate="1:1").with_columns(pl.col("status_evidence").fill_null("missing_unknown"))
    panel=panel.join(load_amounts(reader,symbols,days),on=KEYS,how="left",validate="1:1")
    panel,audit=audit_amounts(panel,cfg["min_liquidity_unit_rows"],cfg["min_liquidity_unit_pass_fraction"])
    print(f"Loaded {len(days)} sessions; liquidity enabled={audit['enabled']}",flush=True)
    tables={}
    for name,spec in REGISTRY.items():
        tables[name]=calculate(panel,spec,audit["enabled"]).filter(pl.col("trade_date").is_between(start,end))
    enabled=[name for name in REGISTRY if name!="liquidity_20" or audit["enabled"]]
    common=matched_keys({name:tables[name] for name in enabled})
    # Freeze ALL native and matched assignments before constructing any labels.
    assignments={scope:{name:freeze_assignments(tables[name],cfg,common if scope=="matched" else None) for name in enabled} for scope in ["native","matched"]}
    labels=forward_returns(panel,20).join(panel.select(*KEYS,"delist_date"),on=KEYS,validate="1:1")
    ends={d:days[i+20] if i+20<len(days) else None for i,d in enumerate(days)}
    labels=labels.with_columns(pl.col("trade_date").replace_strict(ends,return_dtype=pl.Date).alias("forward_end_date"))
    labels=labels.with_columns(((pl.col("delist_date")>pl.col("trade_date"))&(pl.col("delist_date")<=pl.col("forward_end_date"))).fill_null(False).alias("delisting_in_forward_window"))
    labels=labels.with_columns(pl.when(pl.col("forward_return").is_not_null()).then(pl.lit("valid")).when(pl.col("forward_end_date").is_null()).then(pl.lit("insufficient_future_sessions")).when(pl.col("delisting_in_forward_window")).then(pl.lit("missing_outcome_delisting_window")).otherwise(pl.lit("missing_or_invalid_future_price_window")).alias("label_reason")).drop("delist_date").filter(pl.col("trade_date").is_between(start,end))
    results={}; outputs={}
    for scope in assignments:
        results[scope]={}; outputs[scope]={}
        for name,a in assignments[scope].items():
            outcomes,metrics,daily=evaluate(a,labels,cfg)
            results[scope][name]={"metrics":metrics,"daily":daily}
            outputs[scope][name]=outcomes
    regression_result=regression(reference,tables["momentum_60"],assignments["native"]["momentum_60"],results["native"]["momentum_60"]["metrics"],results["native"]["momentum_60"]["daily"],labels,research/"src/ashare_research")
    diagnostics={}
    for name,t in tables.items():
        ready=t.filter(pl.col("factor_reason")=="ready").height
        diagnostics[name]={"enabled":name in enabled,"evaluation_slots":t.height,"factor_reasons":dict(Counter(t["factor_reason"])),"native_ready":ready,"matched_ready":common.height if name in enabled else 0,"ready_lost_to_matching":ready-common.height if name in enabled else None,"ready_loss_rate":(ready-common.height)/ready if name in enabled and ready else None,"native_assigned":assignments["native"][name].height if name in enabled else 0,"matched_assigned":assignments["matched"][name].height if name in enabled else 0}
    comparisons=compare(results["native"],"native")+compare(results["matched"],"matched")
    input_scope=panel.filter(pl.col("trade_date").is_between(start,end)&pl.col("listing_eligible"))
    quality={"eligible_evaluation_slots":input_scope.height,"price_reasons":dict(Counter(input_scope["price_reason"])),"status_evidence":dict(Counter(input_scope["status_evidence"])),"matching_note":"Intersection of factor-ready stock/date keys before labels; ranks recomputed independently on that intersection", "matched_scope":"all_six_factors" if audit["enabled"] else "price_factors_only","matched_factor_ids":enabled,"common_ready_observations":common.height}
    reader.verify()
    if any(fingerprint(reference/name)!=digest for name,digest in reference_hashes.items()):
        raise ValueError("M3A reference changed")
    source_root=research/"src/ashare_research"
    sources={str(p.relative_to(source_root)):fingerprint(p) for p in source_root.rglob("*.py")}
    experiment_id="m3b-"+started.strftime("%Y%m%dT%H%M%S%fZ")+"-"+uuid4().hex[:8]
    report={"schema_version":"3.0","experiment_id":experiment_id,"research_version":VERSION,"evaluation_version":"signal_first_v1","evaluation_period":{"start":str(start),"end":str(end),"sessions":len(evaluation_days)},"universe_policy":saved["universe_policy"],"factor_definitions":[s.manifest() for s in REGISTRY.values()],"timing":"After session t closes; all trailing windows include t. Sample return volatility ddof=1, unannualized. No direction/parameter optimization.","return_convention":saved["return_convention"],"liquidity_validation":audit,"diagnostics":diagnostics,"data_quality":quality,"results":results,"comparisons":comparisons,"regression":regression_result,"limitations":LIMITATIONS+["Multiple predefined factors increase data-mining risk; all results are reported without selecting a strategy or composite score.","Liquidity is raw traded amount, not turnover; zeros are conservatively rejected. Unit checks establish internal consistency, not independent vendor accuracy.","Native factor samples differ. Matching loses observations and does not remove outcome missingness or unknown tradability."],"provenance":{"started_at_utc":started.isoformat(),"completed_at_utc":datetime.now(timezone.utc).isoformat(),"configuration":cfg,"configuration_sha256":sha256(config_bytes).hexdigest(),"source_sha256":sources,"reference_artifact_sha256":reference_hashes,"input_file_sha256":reader.hashes,"load_start":str(days[0]),"load_end":str(days[-1]),"python_version":platform.python_version(),"dependency_versions":{p:version(p) for p in ["polars","ashare-lake"]}}}
    dest=research/"artifacts"/experiment_id
    dest.mkdir(parents=True,exist_ok=False)
    frames={"sample_manifest":selected,"observations":panel,"labels":labels,"matched_signal_keys":common,"comparison":pl.DataFrame(comparisons)}
    frames.update({"factor_"+name:t for name,t in tables.items()})
    for scope in assignments:
        for name,a in assignments[scope].items():
            frames[f"{scope}_{name}_assignments"]=a
            frames[f"{scope}_{name}_outcomes"]=outputs[scope][name]
    for name,frame in frames.items():
        with (dest/(name+".parquet")).open("xb") as handle:
            frame.write_parquet(handle)
    with (dest/"results.json").open("x",encoding="utf-8") as handle:
        json.dump(report,handle,indent=2,default=str,allow_nan=False)
        handle.write("\n")
    lines=["# M3B independent multi-factor research", "",f"Experiment: `{experiment_id}`. Evaluation: 2020–2021, {len(evaluation_days)} dates. Same frozen M3A cohort: 50 SH + 50 SZ, including four eventual delistings. Exploratory, not representative of all A-shares.","",f"Liquidity gate: **{'ENABLED' if audit['enabled'] else 'DISABLED'}**. Matched scope: `{quality['matched_scope']}`. Reasons: {audit['disabled_reasons']}","","Native and matched comparisons (IC is mean daily Spearman; Q1/Q2/Q3 are mean 20-session outcomes on cross-factor common primary dates):","","| Scope | Factor | IC | Assigned | Missing | Q1 | Q2 | Q3 |","|---|---|---:|---:|---:|---:|---:|---:|"]
    for r in comparisons:
        def fmt(x): return "NA" if x is None else f"{x:.6f}"
        lines.append(f"| {r['scope']} | {r['factor_id']} | {fmt(r['mean_daily_rank_ic'])} | {r['assigned']} | {r['missing_outcomes']} | {fmt(r['q1_common_date_mean'])} | {fmt(r['q2_common_date_mean'])} | {fmt(r['q3_common_date_mean'])} |")
    lines.extend(["","Each factor is independent and ascending by raw value. No composite, optimized weights or preferred strategy. Momentum P(t)/P(t-L)-1; MA distance P(t)/mean(P[t-59:t])-1; volatility is std of 20 simple daily HFQ returns, ddof=1; liquidity is mean of 20 raw CNY amounts. Every trailing window includes t and requires all inputs.","","Ranks and matched-signal keys are frozen before labels. Average ranks retain ties. Minimum 30 signals, five per primary bucket, 30 IC pairs, 90% outcome coverage per bucket. Unknown status remains unknown. No missing returns are zero-filled. Stocks are equally weighted within date, dates equally weighted thereafter. Primary cross-factor means use the same accepted dates; native means on each factor's own dates are in results.json.","","## Coverage loss", "", "```json",json.dumps(diagnostics,indent=2),"```","","## Liquidity audit", "", "```json",json.dumps(audit,indent=2),"```","","## Data quality and regression", "", "```json",json.dumps({"data_quality":quality,"regression":regression_result},indent=2),"```","","Per-factor native/matched daily results, missing-outcome counts/rates by bucket and delisting-window diagnostics are recorded in results.json and outcome Parquet tables. Assignment files contain only historical signals, never labels.","","## Limitations",""])
    lines.extend("- "+x for x in report["limitations"])
    lines.extend(["","M1 remains open. Overlapping outcomes are dependent; these are not portfolio returns or profitability evidence. Input/source/config/reference hashes and UTC timestamps are retained in results.json.",""])
    with (dest/"report.md").open("x",encoding="utf-8") as handle:
        handle.write("\n".join(lines))
    print(json.dumps({"artifact_directory":str(dest),"liquidity":audit,"regression":regression_result,"data_quality":quality,"diagnostics":diagnostics,"comparisons":comparisons},indent=2),flush=True)
    return dest,report


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--config",type=Path,required=True)
    run(parser.parse_args().config)


if __name__=="__main__":
    main()
