"""验证 profile SVG 融合统计脚本。"""

from datetime import date
import unittest

from scripts.generate_profile_combined import (
    DayContribution,
    RepoTotals,
    build_day_group,
    build_stats,
    DayGroup,
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

    def test_mixed_day_outputs_two_color_layers(self) -> None:
        """同一天个人和组织贡献同时存在时输出双色分层柱。"""
        group = DayGroup(x=10, y=20, base_y=23, animated=False)
        day = DayContribution(day=date(2026, 8, 1), personal=2, org=3)

        svg = build_day_group(group, day)

        self.assertIn("#27689f", svg)
        self.assertIn("#2da44e", svg)
        self.assertIn("personal 2, SynlysAI 3", svg)


if __name__ == "__main__":
    unittest.main()
