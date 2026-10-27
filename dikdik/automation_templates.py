"""Built-in automation starters. They are data-only and never grant approvals."""

TEMPLATES = (
    {
        "id": "daily-maintenance", "name": "Daily maintenance", "category": "maintenance",
        "description": "Review the workspace each morning and address a focused maintenance objective.",
        "spec": {"name": "Daily maintenance", "objective": "Review the workspace and complete the highest-value maintenance task.",
                 "schedule": {"daily_at": "09:00"}, "mode": "agent", "verification": "syntax", "timeout_seconds": 1800},
    },
    {
        "id": "weekday-review", "name": "Weekday review", "category": "quality",
        "description": "Run a quality review Monday through Friday in a chosen time zone.",
        "spec": {"name": "Weekday review", "objective": "Review recent changes for regressions and propose or implement safe fixes.",
                 "schedule": {"cron": "0 10 * * 1-5", "timezone": "local"}, "mode": "agent", "verification": "syntax", "timeout_seconds": 1800},
    },
    {
        "id": "on-commit", "name": "On commit check", "category": "git",
        "description": "Run after the selected local Git revision changes.",
        "spec": {"name": "On commit check", "objective": "Inspect the new commit and run the most relevant verification.",
                 "schedule": {"event": {"type": "git_commit", "repository": ".", "ref": "HEAD"}},
                 "mode": "agent", "verification": "syntax", "timeout_seconds": 1800},
    },
)


def templates() -> list[dict]:
    return [{**item, "spec": dict(item["spec"])} for item in TEMPLATES]
