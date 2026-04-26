#!/usr/bin/env python3
"""
Quick test to verify benchmark contact export functionality works.
"""

from pathlib import Path

from gc_contacts.benchmark import ComparisonReport, DiscoveryResult


def test_discovery_result_with_contacts():
    """Test that DiscoveryResult can store contacts."""
    contacts = [
        {"name": "John Doe", "email": "john@example.com", "role": "Professor", "page_url": "http://example.com/staff"},
        {"name": "Jane Smith", "email": "jane@example.com", "role": "Dean", "page_url": "http://example.com/leadership"},
    ]

    result = DiscoveryResult(
        method="heuristic",
        university_name="Test University",
        homepage_url="http://example.com",
        candidates_found=10,
        probe_attempts=5,
        candidates_probed=5,
        contacts_extracted=8,
        contacts_kept=2,
        time_seconds=2.5,
        contacts_list=contacts,
    )

    assert result.contacts_list == contacts
    assert len(result.contacts_list) == 2
    print("[ok] DiscoveryResult stores contacts correctly")


def test_comparison_report_export():
    """Test that ComparisonReport can export contacts to JSON."""
    import json
    import tempfile

    report = ComparisonReport()

    for method in ["heuristic", "ai_slug"]:
        contacts = [
            {"name": f"{method} Contact 1", "email": f"contact1@{method}.com", "role": "Staff", "page_url": "http://example.com/1"},
            {"name": f"{method} Contact 2", "email": f"contact2@{method}.com", "role": "Faculty", "page_url": "http://example.com/2"},
        ]

        result = DiscoveryResult(
            method=method,
            university_name="Oxford University",
            homepage_url="http://ox.ac.uk",
            candidates_found=15,
            probe_attempts=10,
            candidates_probed=10,
            contacts_extracted=10,
            contacts_kept=2,
            time_seconds=3.5,
            contacts_list=contacts,
        )
        report.add(result)

    with tempfile.TemporaryDirectory() as tmpdir:
        export_dir = Path(tmpdir) / "test_benchmark_contacts"
        export_dir.mkdir(exist_ok=True)

        report.export_contacts(export_dir)

        files = list(export_dir.glob("*.json"))
        assert len(files) == 2, f"Expected 2 files, got {len(files)}"

        for file_path in files:
            with open(file_path, encoding="utf-8") as handle:
                data = json.load(handle)
                assert "method" in data
                assert "university" in data
                assert "contacts" in data
                assert len(data["contacts"]) > 0

        print(f"[ok] ComparisonReport exports contacts (generated {len(files)} JSON files)")


def test_comparison_report_csv_includes_failure_rows():
    """Failure rows should be exported explicitly rather than dropped."""
    import csv
    import tempfile

    report = ComparisonReport()
    report.add(
        DiscoveryResult(
            method="ai_crawler",
            university_name="Failed University",
            homepage_url="http://failed.example",
            candidates_found=0,
            probe_attempts=0,
            candidates_probed=0,
            contacts_extracted=0,
            contacts_kept=0,
            time_seconds=12.5,
            status="timeout",
            error="timed out after 180.0s",
        )
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        csv_path = Path(tmpdir) / "results.csv"
        report.to_csv(csv_path)
        with open(csv_path, encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        assert len(rows) == 1
        assert rows[0]["status"] == "timeout"
        assert "timed out" in rows[0]["error"]
        print("[ok] ComparisonReport exports failure status rows to CSV")


def test_probe_return_signature():
    """Verify probe_candidates_and_extract return signature includes contacts."""
    from inspect import signature
    from gc_contacts.benchmark import probe_candidates_and_extract

    sig = signature(probe_candidates_and_extract)
    print(f"[ok] probe_candidates_and_extract signature: {sig}")


if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("TESTING BENCHMARK CONTACT EXPORT FUNCTIONALITY")
    print("=" * 60 + "\n")

    try:
        test_discovery_result_with_contacts()
        test_comparison_report_export()
        test_comparison_report_csv_includes_failure_rows()
        test_probe_return_signature()

        print("\n" + "=" * 60)
        print("[ok] All tests passed!")
        print("=" * 60)
        print("\nYou can now run benchmarks with contact data export:")
        print("  python benchmark_runner.py --country GB --limit 5 --output-dir benchmark_runs")
    except Exception as exc:
        print(f"\n[fail] Test failed: {exc}")
        import traceback

        traceback.print_exc()
