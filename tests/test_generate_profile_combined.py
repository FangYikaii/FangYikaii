"""验证 profile SVG 融合统计脚本。"""

from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from scripts.generate_profile_combined import (
    ContributionStats,
    DayContribution,
    LanguageStat,
    MetricSet,
    RepositoryInfo,
    build_day_group,
    build_radar_group,
    build_stats,
    DayGroup,
    RepoTotals,
    fetch_radar_metrics,
    fetch_search_metrics,
    group_repo_qualifiers,
    rewrite_profile_svgs,
)


class GenerateProfileCombinedTest(unittest.TestCase):
    """测试个人与组织贡献融合逻辑。"""

    @staticmethod
    def _collection(
        days: list[tuple[str, int]],
        commits: int,
        pull_requests: int,
        languages: list[tuple[str, str, int]] | None = None,
    ) -> dict:
        """构造 contributionsCollection fixture。

        Args:
            days: 日期和贡献数。
            commits: commit 指标。
            pull_requests: PR 指标。
            languages: 语言名、颜色和提交贡献数。

        Returns:
            GraphQL collection 形状的字典。
        """
        return {
            "contributionCalendar": {
                "weeks": [
                    {
                        "contributionDays": [
                            {"date": day, "contributionCount": count}
                            for day, count in days
                        ]
                    }
                ]
            },
            "totalCommitContributions": commits,
            "totalIssueContributions": 1,
            "totalPullRequestContributions": pull_requests,
            "totalPullRequestReviewContributions": 2,
            "totalRepositoryContributions": 3,
            "commitContributionsByRepository": [
                {
                    "repository": {
                        "primaryLanguage": {
                            "name": name,
                            "color": color,
                        }
                    },
                    "contributions": {"totalCount": count},
                }
                for name, color, count in languages or []
            ],
        }

    def test_personal_is_total_minus_org_and_clamped(self) -> None:
        """个人贡献按总贡献扣除组织贡献，并对负数钳制为 0。"""
        total = self._collection(
            [("2026-08-01", 4), ("2026-08-02", 2)],
            commits=10,
            pull_requests=5,
        )
        org = self._collection(
            [("2026-08-01", 3), ("2026-08-02", 5)],
            commits=7,
            pull_requests=4,
        )

        stats = build_stats(total, org, RepoTotals(stars=2, forks=1))

        self.assertEqual(stats.days[0].personal, 1)
        self.assertEqual(stats.days[0].org, 3)
        self.assertEqual(stats.days[1].personal, 0)
        self.assertEqual(stats.days[1].org, 5)
        self.assertEqual(stats.personal_metrics.pull_requests, 1)
        self.assertEqual(stats.org_metrics.pull_requests, 4)
        self.assertEqual(stats.total_metrics.pull_requests, 5)

    def test_language_ring_uses_combined_personal_and_org_counts(self) -> None:
        """语言环图使用个人与组织贡献合并后的语言统计。"""
        total = self._collection(
            [("2026-08-01", 4)],
            commits=10,
            pull_requests=5,
            languages=[("Python", "#3572A5", 6), ("TypeScript", "#3178c6", 2)],
        )
        org = self._collection(
            [("2026-08-01", 3)],
            commits=7,
            pull_requests=4,
            languages=[("Python", "#3572A5", 4), ("Go", "#00ADD8", 3)],
        )

        stats = build_stats(total, org, RepoTotals(stars=2, forks=1))

        self.assertEqual(
            [(item.name, item.count) for item in stats.languages],
            [("Python", 6), ("Go", 3), ("TypeScript", 2)],
        )

    def test_search_metrics_count_authored_prs_for_org(self) -> None:
        """PR 指标使用 author 查询，并能统计 SynlysAI 组织 PR。"""
        seen_queries: list[str] = []

        def fake_search(_token: str, query: str) -> int:
            seen_queries.append(query)
            if "org:SynlysAI" in query and "is:pr author:FangYikaii" in query:
                return 9
            if "is:pr author:FangYikaii" in query:
                return 12
            return 0

        total = fetch_search_metrics(
            "token",
            "FangYikaii",
            "",
            date(2025, 8, 3),
            date(2026, 8, 3),
            fake_search,
        )
        org = fetch_search_metrics(
            "token",
            "FangYikaii",
            "SynlysAI",
            date(2025, 8, 3),
            date(2026, 8, 3),
            fake_search,
        )

        self.assertEqual(total.pull_requests, 12)
        self.assertEqual(org.pull_requests, 9)
        self.assertTrue(any("created:2025-08-03..2026-08-03" in query for query in seen_queries))

    def test_repo_search_fallback_counts_all_org_prs(self) -> None:
        """组织搜索为 0 时，仓库级搜索会汇总全部组织 PR。"""
        seen_queries: list[str] = []
        org_repos = [
            RepositoryInfo("ExampleOrg/repo-a", stars=0, forks=0, is_fork=False),
            RepositoryInfo("ExampleOrg/repo-b", stars=0, forks=0, is_fork=False),
        ]

        def fake_search(_token: str, query: str) -> int:
            seen_queries.append(query)
            if (
                "repo:ExampleOrg/repo-a" in query
                and "repo:ExampleOrg/repo-b" in query
                and "is:pr author:FangYikaii" in query
            ):
                return 9
            return 0

        def fake_commits(
            _token: str,
            _repo: RepositoryInfo,
            _username: str,
            _from_day: date,
            _to_day: date,
        ) -> int:
            return 0

        _personal, org, total = fetch_radar_metrics(
            "token",
            "FangYikaii",
            "ExampleOrg",
            date(2025, 8, 3),
            date(2026, 8, 3),
            [],
            org_repos,
            search_counter=fake_search,
            commit_counter=fake_commits,
        )

        self.assertEqual(org.pull_requests, 9)
        self.assertEqual(total.pull_requests, 9)
        pr_queries = [
            query for query in seen_queries
            if "repo:ExampleOrg/" in query and "is:pr author:FangYikaii" in query
        ]
        self.assertEqual(len(pr_queries), 1)

    def test_repo_search_fallback_skips_duplicate_and_fork_repos(self) -> None:
        """仓库级搜索会跳过重复仓库和 fork 仓库。"""
        seen_queries: list[str] = []
        org_repos = [
            RepositoryInfo("ExampleOrg/repo-a", stars=0, forks=0, is_fork=False),
            RepositoryInfo("ExampleOrg/repo-a", stars=0, forks=0, is_fork=False),
            RepositoryInfo("ExampleOrg/forked", stars=0, forks=0, is_fork=True),
        ]

        def fake_search(_token: str, query: str) -> int:
            seen_queries.append(query)
            if (
                "repo:ExampleOrg/repo-a" in query
                and "is:pr author:FangYikaii" in query
            ):
                return 3
            return 0

        def fake_commits(
            _token: str,
            _repo: RepositoryInfo,
            _username: str,
            _from_day: date,
            _to_day: date,
        ) -> int:
            return 0

        _personal, org, total = fetch_radar_metrics(
            "token",
            "FangYikaii",
            "ExampleOrg",
            date(2025, 8, 3),
            date(2026, 8, 3),
            [],
            org_repos,
            search_counter=fake_search,
            commit_counter=fake_commits,
        )

        repo_a_queries = [
            query
            for query in seen_queries
            if (
                "repo:ExampleOrg/repo-a" in query
                and "is:pr author:FangYikaii" in query
            )
        ]
        self.assertEqual(org.pull_requests, 3)
        self.assertEqual(total.pull_requests, 3)
        self.assertEqual(len(repo_a_queries), 1)
        self.assertFalse(
            any("repo:ExampleOrg/forked" in query for query in seen_queries)
        )

    def test_repo_qualifier_groups_respect_query_length(self) -> None:
        """仓库 qualifier 会按 Search 查询长度分组。"""
        repos = [
            RepositoryInfo(f"ExampleOrg/repo-{index}", stars=0, forks=0, is_fork=False)
            for index in range(1, 6)
        ]

        groups = group_repo_qualifiers(
            repos,
            max_length=90,
            suffix="is:pr author:FangYikaii created:2025-08-03..2026-08-03",
        )

        self.assertGreater(len(groups), 1)
        self.assertTrue(
            all(
                len(f"{group} is:pr author:FangYikaii created:2025-08-03..2026-08-03")
                <= 90
                for group in groups
            )
        )

    def test_rest_commit_counts_enter_org_and_total_radar_metrics(self) -> None:
        """仓库 REST commit 计数进入组织和合计雷达指标。"""
        user_repos = [
            RepositoryInfo("FangYikaii/personal", stars=1, forks=0, is_fork=False),
        ]
        org_repos = [
            RepositoryInfo("SynlysAI/private-a", stars=2, forks=1, is_fork=False),
            RepositoryInfo("SynlysAI/private-fork", stars=3, forks=1, is_fork=True),
        ]

        def fake_search(_token: str, query: str) -> int:
            if "repo:" in query:
                return 0
            if "org:SynlysAI" in query and "is:pr author:FangYikaii" in query:
                return 5
            if "is:pr author:FangYikaii" in query:
                return 7
            return 0

        def fake_commits(
            _token: str,
            repo: RepositoryInfo,
            _username: str,
            _from_day: date,
            _to_day: date,
        ) -> int:
            return {
                "FangYikaii/personal": 4,
                "SynlysAI/private-a": 11,
                "SynlysAI/private-fork": 100,
            }[repo.name_with_owner]

        personal, org, total = fetch_radar_metrics(
            "token",
            "FangYikaii",
            "SynlysAI",
            date(2025, 8, 3),
            date(2026, 8, 3),
            user_repos,
            org_repos,
            search_counter=fake_search,
            commit_counter=fake_commits,
        )

        self.assertEqual(personal.commits, 4)
        self.assertEqual(org.commits, 11)
        self.assertEqual(total.commits, 15)
        self.assertEqual(org.pull_requests, 5)
        self.assertEqual(total.pull_requests, 7)
        self.assertEqual(total.repositories, 2)

    def test_mixed_day_outputs_two_color_layers(self) -> None:
        """同一天个人和组织贡献同时存在时输出双色分层柱。"""
        group = DayGroup(x=10, y=20, base_y=23, animated=False)
        day = DayContribution(day=date(2026, 8, 1), personal=2, org=3)

        svg = build_day_group(group, day)

        self.assertIn("#27689f", svg)
        self.assertIn("#2da44e", svg)
        self.assertIn("personal 2, SynlysAI 3", svg)

    def test_radar_group_outputs_visible_metric_values(self) -> None:
        """雷达图指标数值会作为可见文本输出。"""
        stats = ContributionStats(
            days=(),
            personal_metrics=MetricSet(4, 1, 2, 0, 1),
            org_metrics=MetricSet(11, 0, 5, 1, 1),
            total_metrics=MetricSet(15, 1, 7, 1, 2),
            total_contributions=0,
            repo_totals=RepoTotals(stars=0, forks=0),
            languages=(),
        )

        svg = build_radar_group("980, 284.5", stats)

        self.assertIn("Commit<title>15</title></text>", svg)
        self.assertIn('x="0" y="-200.52"', svg)
        self.assertIn('x="0" y="-178.52"', svg)
        self.assertIn('class="fill-strong">15</text>', svg)
        self.assertIn("PullReq<title>7</title></text>", svg)
        self.assertIn('class="fill-strong">7</text>', svg)

    def test_rewrite_all_theme_svgs_outputs_combined_markers(self) -> None:
        """所有主题 SVG 都会被后处理成融合图。"""
        source_svg = Path("profile-3d-contrib/profile-green.svg")
        stats = ContributionStats(
            days=(
                DayContribution(day=date(2026, 8, 1), personal=2, org=3),
            ),
            personal_metrics=MetricSet(4, 1, 2, 0, 1),
            org_metrics=MetricSet(11, 0, 5, 1, 1),
            total_metrics=MetricSet(15, 1, 7, 1, 2),
            total_contributions=5,
            repo_totals=RepoTotals(stars=3, forks=2),
            languages=(
                LanguageStat(name="Python", color="#3572A5", count=8),
            ),
        )

        with TemporaryDirectory() as tmp_name:
            profile_dir = Path(tmp_name)
            for name in ("profile-green.svg", "profile-night-green.svg"):
                (profile_dir / name).write_text(
                    source_svg.read_text(encoding="utf-8"),
                    encoding="utf-8",
                )
            rewrite_profile_svgs(profile_dir, stats)

            for svg_path in sorted(profile_dir.glob("*.svg")):
                svg = svg_path.read_text(encoding="utf-8")
                self.assertIn("Personal", svg)
                self.assertIn("SynlysAI", svg)
                self.assertIn("Total", svg)
                self.assertIn("#2f78b7", svg)
                self.assertIn("#2da44e", svg)
                self.assertIn("Window: 2026-08-01 / 2026-08-01", svg)
                self.assertNotIn("Contribution Origin", svg)


if __name__ == "__main__":
    unittest.main()
