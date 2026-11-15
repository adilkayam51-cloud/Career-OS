from src.source_network import Board, boolean_queries, discover, parse_public_jobs, public_url
import pytest


def test_allowlisted_sources_and_reject_untrusted_slug():
    assert public_url(Board("greenhouse", "example")) == "https://boards-api.greenhouse.io/v1/boards/example/jobs"
    assert public_url(Board("lever", "example")) == "https://api.lever.co/v0/postings/example?mode=json"
    assert public_url(Board("ashby", "example")) == "https://api.ashbyhq.com/posting-api/job-board/example"
    for invalid in ("../../private", "http://localhost", "example.com/other", ""):
        with pytest.raises(ValueError):
            public_url(Board("greenhouse", invalid))
    with pytest.raises(ValueError):
        public_url(Board("unknown", "example"))


def test_provider_parsing_public_metadata_only():
    examples = [
        (Board("greenhouse", "g"), {"jobs": [{"id": 42, "title": "Account Manager", "absolute_url": "https://job-boards.greenhouse.io/g/jobs/42", "location": {"name": "India"}}]}),
        (Board("lever", "l"), [{"id": "abc", "text": "Sales Manager", "hostedUrl": "https://jobs.lever.co/l/abc", "categories": {"location": "Noida"}}]),
        (Board("ashby", "a"), {"jobs": [{"id": "xyz", "title": "Revenue Growth", "jobUrl": "https://jobs.ashbyhq.com/a/xyz", "location": "Remote India", "isRemote": True}]}),
    ]
    for board, response in examples:
        lead = parse_public_jobs(board, response)[0]
        assert lead.provider == board.provider
        assert lead.provider_job_id
        assert lead.job_url.startswith("https://")
        assert lead.status == "SOURCE_DISCOVERED_NEEDS_JD_AND_ROUTE_VERIFICATION"


def test_redundancy_survives_one_feed_failure_and_deduplicates_boards():
    def simulated(url):
        if "lever.co" in url:
            raise TimeoutError("upstream timeout")
        return {"jobs": [{"id": 1, "title": "B2B Sales", "absolute_url": "https://job-boards.greenhouse.io/g/jobs/1", "location": {"name": "Gurugram"}}]}

    report = discover([Board("lever", "l"), Board("greenhouse", "g"), Board("greenhouse", "g")], fetch=simulated)
    assert report["boards_requested"] == 2
    assert report["lead_count"] == 1
    assert report["failures"][0]["provider"] == "lever"
    assert "NO_APPLICATION_SUBMISSION" in report["mode"]


def test_boolean_lanes_and_input_guards():
    assert len(boolean_queries("remote_india")) >= 4
    assert len(boolean_queries("geo_ncr_jaipur")) >= 4
    with pytest.raises(ValueError):
        boolean_queries("other")
    with pytest.raises(ValueError):
        discover([Board("lever", f"b{i}") for i in range(13)], fetch=lambda _: [])
