from dataclasses import dataclass


@dataclass
class ReleaseNotes:
    version: str
    from_tag: str
    to_tag: str
    generated_at: str
    features: list[str]
    improvements: list[str]
    bug_fixes: list[str]
    breaking_changes: list[str]
    untracked_commits_count: int
    coverage_pct: float
    run_id: str
    llm_provider: str
    llm_model: str

    def summary(self) -> str:
        total = sum([
            len(self.features), len(self.improvements),
            len(self.bug_fixes), len(self.breaking_changes)
        ])
        return (
            f"Generated {total} entries "
            f"({len(self.breaking_changes)} breaking, {len(self.features)} features, "
            f"{len(self.bug_fixes)} fixes) | coverage: {self.coverage_pct:.0%}"
        )
