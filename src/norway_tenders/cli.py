from __future__ import annotations

import argparse
import json
import logging
import sys

from norway_tenders import analysis, pipeline


def _configure_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Norwegian pharmaceutical tender pipeline")
    parser.add_argument(
        "command",
        choices=[
            "discover", "audit-discovery", "validate-local", "build-preview",
            "build-final-candidate", "fetch", "build", "analyse", "run",
        ],
    )
    parser.add_argument("--offline", action="store_true", help="Use cached discovery/downloads")
    parser.add_argument("--refresh", action="store_true", help="Refresh cached data")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    _configure_logging(args.verbose)

    if args.command == "discover":
        result = pipeline.discover(offline=args.offline, refresh=args.refresh)
        print(f"Discovery complete: {len(result.candidates)} candidates")
        print(f"Written to {result.candidates and 'data/discovery/review_candidates.csv'}")
        print(json.dumps(
            {
                "queries_by_molecule": result.queries_by_molecule,
                "candidates_by_molecule": result.candidates_by_molecule,
                "decision_counts": result.decision_counts,
                "errors": result.errors,
                "lis2234_notice_ids": sorted({c.notice_id for c in result.lis2234_matches}),
            },
            indent=2,
        ))
    elif args.command == "audit-discovery":
        result = pipeline.audit_discovery(offline=args.offline, refresh=args.refresh)
        report = json.loads(result.report_path.read_text(encoding="utf-8")) if result.report_path else {}
        print("=== Phase 4B: Discovery gap & candidate quality review ===\n")
        print(f"Axitinib queries executed: {result.axitinib_queries}")
        print(f"Axitinib candidate notices: {result.axitinib_notices}")
        print(f"Axitinib tender families: {', '.join(result.axitinib_families) or 'none'}")
        print(f"Axitinib confirmation status: {result.axitinib_confirmation}")
        print(f"Axitinib API errors: {result.axitinib_errors or 'none'}\n")
        print("Accepted candidates by evidence level:")
        for k, v in sorted(report.get("accepted_evidence_level_counts", {}).items()):
            print(f"  {k}: {v}")
        print("\nUnique procedures by molecule:")
        for k, v in sorted(report.get("procedures_by_molecule", {}).items()):
            print(f"  {k}: {v}")
        print(f"\nLanguage duplicates: {report.get('language_duplicate_count', 0)}")
        print(f"Lifecycle-related notices: {report.get('lifecycle_related_count', 0)}")
        print("\nCandidate counts before audit:", report.get("candidate_counts_before"))
        print("Candidate counts after audit:", report.get("candidate_counts_after_audit"))
        print("\nDocument access classification:", report.get("document_access_counts"))
        print("\nDowngrade examples:")
        for ex in report.get("downgrade_examples", []):
            print(f"  - {ex['noticeId']} ({ex['targetMolecule']}): {ex['evidenceLevel']} -> {ex['auditedDecision']}")
            print(f"    {ex['auditReason']}")
        print("\nRecommended document-fetch queue:")
        for item in report.get("recommended_fetch_queue", []):
            print(f"  - {item['targetMolecule']}: {item.get('canonicalNoticeId') or 'TBD'} ({item['priority']})")
        print(f"\nReports written to data/discovery/")
    elif args.command == "validate-local":
        from norway_tenders.validation.phase5a import run_phase5a

        result = run_phase5a()
        print("=== Phase 5A: Local document validation & inventory ===\n")
        print(f"Files validated: {result.file_count}")
        print(f"Written: {result.validation_path}")
        print(f"Written: {result.inventory_path}")
        print(f"Written: {result.matches_path}")
        print(f"Written: {result.parser_path}")
        print(f"Written: {result.summary_path}")
        print(f"Updated: {result.sources_path}")
    elif args.command == "build-preview":
        from norway_tenders.validation.phase5b import run_phase5b

        result = run_phase5b()
        print("=== Phase 5B: Target-filtered pack preview ===\n")
        print(f"Preview rows: {result.row_count}")
        print(f"Written: {result.preview_path}")
        print(f"Written: {result.evidence_path}")
        print(f"Written: {result.audit_path}")
        print(f"Written: {result.lifecycle_path}")
        print(f"Written: {result.quality_path}")
    elif args.command == "build-final-candidate":
        from norway_tenders.validation.phase5e import run_phase5e

        result = run_phase5e()
        print("=== Phase 5E: Volume correction & DMP enrichment ===\n")
        print(f"Final candidate rows: {result.row_count}")
        print(f"Preview unchanged: {result.preview_path}")
        print(f"Written: {result.final_candidate_path}")
        print(f"Written: {result.dmp_audit_path}")
        print(f"Written: {result.evidence_path}")
        print(f"Written: {result.quality_path}")
    elif args.command == "fetch":
        docs = pipeline.fetch(offline=args.offline, refresh=args.refresh)
        ok = sum(1 for d in docs if d.local_path and not d.download_error)
        print(f"Fetched {ok}/{len(docs)} documents")
    elif args.command == "build":
        if args.offline:
            from norway_tenders.validation.phase5g import run_offline_build

            result = run_offline_build()
            print("=== Offline build: preview → final candidate → output ===\n")
            print(f"Output rows: {result.row_count}")
            print(f"Written: {result.output_path}")
            print(f"SHA-256: {result.output_sha256}")
        else:
            rows = pipeline.build(offline=args.offline, refresh=args.refresh)
            print(f"Built {len(rows)} output rows")
    elif args.command == "analyse":
        result = analysis.analyse()
        print("=== Phase 6: Decision-oriented analytics ===\n")
        print(f"Summary: {result.summary_path}")
        print(f"Notes: {result.notes_path}")
        print(f"Tables: {len(result.table_paths)} files in reports/tables/")
        print(f"Charts: {len(result.chart_paths)} files in reports/charts/")
        for path in result.chart_paths:
            if path.suffix == ".png":
                print(f"  - {path}")
    elif args.command == "run":
        rows = pipeline.run(offline=args.offline, refresh=args.refresh)
        result = analysis.analyse()
        print(f"Pipeline complete: {len(rows)} rows, {len(result.chart_paths)} chart files")
    return 0


if __name__ == "__main__":
    sys.exit(main())
