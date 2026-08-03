"""生成融合个人与 SynlysAI 贡献数据的 GitHub profile SVG。"""

from __future__ import annotations

import json
import math
import os
import re
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any


GRAPHQL_URL = "https://api.github.com/graphql"
DEFAULT_ORG = "SynlysAI"
DEFAULT_PROFILE_DIR = "profile-3d-contrib"
DEFAULT_USERNAME = "FangYikaii"
PERSONAL_TOP = "#2f78b7"
PERSONAL_LEFT = "#27689f"
PERSONAL_RIGHT = "#205782"
ORG_TOP = "#2da44e"
ORG_LEFT = "#238636"
ORG_RIGHT = "#196c2e"
TOTAL_RADAR = "#1f2933"
NEUTRAL_TOP = "#ebedf0"
NEUTRAL_LEFT = "#cfcfcf"
NEUTRAL_RIGHT = "#aeaeae"
SCALE_Y = 1.15
DAY_TOP_SIZE = 18.0
DAY_SIDE_Y = 10.39
RADAR_LABELS = ("Commit", "Issue", "PullReq", "Review", "Repo")
RADAR_RANGE_LABELS = ("1", "10", "100", "1K", "10K")


@dataclass(frozen=True)
class DayContribution:
    """单日贡献拆分。

    Args:
        day: 日期。
        personal: 非 SynlysAI 贡献数量。
        org: SynlysAI 组织贡献数量。
    """

    day: date
    personal: int
    org: int

    @property
    def total(self) -> int:
        """返回个人与组织贡献合计。"""
        return self.personal + self.org


@dataclass(frozen=True)
class MetricSet:
    """雷达图贡献指标集合。"""

    commits: int
    issues: int
    pull_requests: int
    reviews: int
    repositories: int

    def values(self) -> tuple[int, int, int, int, int]:
        """按雷达图轴顺序返回指标。"""
        return (
            self.commits,
            self.issues,
            self.pull_requests,
            self.reviews,
            self.repositories,
        )


@dataclass(frozen=True)
class RepoTotals:
    """可见仓库整体统计。"""

    stars: int
    forks: int


@dataclass(frozen=True)
class LanguageStat:
    """语言贡献统计。"""

    name: str
    color: str
    count: int


@dataclass(frozen=True)
class ContributionStats:
    """融合后的贡献统计。"""

    days: tuple[DayContribution, ...]
    personal_metrics: MetricSet
    org_metrics: MetricSet
    total_metrics: MetricSet
    total_contributions: int
    repo_totals: RepoTotals
    languages: tuple[LanguageStat, ...]


def main() -> int:
    """读取 GitHub GraphQL 数据并后处理 profile SVG 目录。"""
    profile_dir = Path(os.environ.get("PROFILE_DIR", DEFAULT_PROFILE_DIR))
    username = os.environ.get("GITHUB_USERNAME", DEFAULT_USERNAME).strip()
    org = os.environ.get("ORG", DEFAULT_ORG).strip()
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    fixture = os.environ.get("PROFILE_STATS_FIXTURE", "").strip()

    if fixture:
        stats = load_stats_fixture(Path(fixture))
    else:
        source_svg = profile_dir / "profile-green.svg"
        from_day, to_day = read_date_range(source_svg)
        if not token:
            raise RuntimeError("GITHUB_TOKEN is required unless PROFILE_STATS_FIXTURE is set.")
        stats = fetch_contribution_stats(token, username, org, from_day, to_day)

    rewrite_profile_svgs(profile_dir, stats)
    remove_legacy_outputs(profile_dir)
    return 0


def fetch_contribution_stats(
    token: str,
    username: str,
    org: str,
    from_day: date,
    to_day: date,
) -> ContributionStats:
    """从 GitHub GraphQL 获取个人总贡献和 SynlysAI 组织贡献。

    Args:
        token: GitHub API token。
        username: GitHub 用户名。
        org: GitHub 组织名。
        from_day: 统计起始日期。
        to_day: 统计结束日期。

    Returns:
        融合后的贡献统计。
    """
    org_id = fetch_org_id(token, org)
    payload = graphql_request(
        token,
        """
        query($login: String!, $orgId: ID!, $from: DateTime!, $to: DateTime!) {
          user(login: $login) {
            total: contributionsCollection(from: $from, to: $to) {
              contributionCalendar {
                totalContributions
                weeks {
                  contributionDays {
                    contributionCount
                    date
                  }
                }
              }
              commitContributionsByRepository(maxRepositories: 100) {
                repository {
                  nameWithOwner
                  primaryLanguage {
                    name
                    color
                  }
                }
                contributions {
                  totalCount
                }
              }
              totalCommitContributions
              totalIssueContributions
              totalPullRequestContributions
              totalPullRequestReviewContributions
              totalRepositoryContributions
            }
            org: contributionsCollection(from: $from, to: $to, organizationID: $orgId) {
              contributionCalendar {
                totalContributions
                weeks {
                  contributionDays {
                    contributionCount
                    date
                  }
                }
              }
              commitContributionsByRepository(maxRepositories: 100) {
                repository {
                  nameWithOwner
                  primaryLanguage {
                    name
                    color
                  }
                }
                contributions {
                  totalCount
                }
              }
              totalCommitContributions
              totalIssueContributions
              totalPullRequestContributions
              totalPullRequestReviewContributions
              totalRepositoryContributions
            }
          }
        }
        """,
        {
            "login": username,
            "orgId": org_id,
            "from": f"{from_day.isoformat()}T00:00:00Z",
            "to": f"{to_day.isoformat()}T23:59:59Z",
        },
    )
    user = payload["data"]["user"]
    repo_totals = fetch_repo_totals(token, username, org)
    return build_stats(user["total"], user["org"], repo_totals)


def fetch_org_id(token: str, org: str) -> str:
    """查询组织节点 ID。

    Args:
        token: GitHub API token。
        org: GitHub 组织名。

    Returns:
        组织 GraphQL node id。
    """
    payload = graphql_request(
        token,
        """
        query($login: String!) {
          organization(login: $login) {
            id
          }
        }
        """,
        {"login": org},
    )
    organization = payload["data"].get("organization")
    if not organization:
        raise RuntimeError(f"organization not found: {org}")
    return str(organization["id"])


def fetch_repo_totals(token: str, username: str, org: str) -> RepoTotals:
    """获取个人仓库和组织仓库的星标、fork 合计。

    Args:
        token: GitHub API token。
        username: GitHub 用户名。
        org: GitHub 组织名。

    Returns:
        可见仓库整体统计。
    """
    user_repos = fetch_repo_page_sequence(token, "user", username)
    org_repos = fetch_repo_page_sequence(token, "organization", org)
    repos = user_repos + org_repos
    return RepoTotals(
        stars=sum(int(repo.get("stargazerCount") or 0) for repo in repos),
        forks=sum(int(repo.get("forkCount") or 0) for repo in repos),
    )


def fetch_repo_page_sequence(token: str, owner_type: str, login: str) -> list[dict[str, Any]]:
    """分页获取用户或组织仓库。

    Args:
        token: GitHub API token。
        owner_type: owner 类型，支持 user 或 organization。
        login: owner 登录名。

    Returns:
        仓库节点列表。
    """
    field = "user" if owner_type == "user" else "organization"
    repo_args = "first: 100, after: $cursor"
    if owner_type == "user":
        repo_args += ", ownerAffiliations: OWNER"
    cursor: str | None = None
    nodes: list[dict[str, Any]] = []
    while True:
        payload = graphql_request(
            token,
            f"""
            query($login: String!, $cursor: String) {{
              {field}(login: $login) {{
                repositories({repo_args}) {{
                  pageInfo {{
                    hasNextPage
                    endCursor
                  }}
                  nodes {{
                    forkCount
                    stargazerCount
                  }}
                }}
              }}
            }}
            """,
            {"login": login, "cursor": cursor},
        )
        owner = payload["data"].get(field)
        if not owner:
            break
        repositories = owner["repositories"]
        nodes.extend(repositories["nodes"])
        if not repositories["pageInfo"]["hasNextPage"]:
            break
        cursor = repositories["pageInfo"]["endCursor"]
    return nodes


def graphql_request(token: str, query: str, variables: dict[str, Any]) -> dict[str, Any]:
    """请求 GitHub GraphQL API。

    Args:
        token: GitHub API token。
        query: GraphQL 查询。
        variables: 查询变量。

    Returns:
        GraphQL JSON 响应。
    """
    body = json.dumps(
        {
            "query": re.sub(r"\s+", " ", query).strip(),
            "variables": variables,
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        GRAPHQL_URL,
        data=body,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "FangYikaii-profile-visuals",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        message = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"GitHub GraphQL failed with HTTP {exc.code}: {message}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"GitHub GraphQL failed: {exc}") from exc

    if payload.get("errors"):
        raise RuntimeError(json.dumps(payload["errors"], ensure_ascii=False))
    return payload


def build_stats(
    total_collection: dict[str, Any],
    org_collection: dict[str, Any],
    repo_totals: RepoTotals,
) -> ContributionStats:
    """合并总贡献和组织贡献，得到个人/组织拆分。

    Args:
        total_collection: 用户总 contributionsCollection。
        org_collection: 组织过滤 contributionsCollection。
        repo_totals: 可见仓库整体统计。

    Returns:
        融合后的贡献统计。
    """
    total_days = calendar_by_date(total_collection)
    org_days = calendar_by_date(org_collection)
    days: list[DayContribution] = []
    for day in sorted(total_days):
        total_count = total_days[day]
        org_count = org_days.get(day, 0)
        days.append(
            DayContribution(
                day=day,
                personal=max(total_count - org_count, 0),
                org=max(org_count, 0),
            )
        )

    org_metrics = collection_metrics(org_collection)
    total_metrics = collection_metrics(total_collection)
    personal_metrics = subtract_metrics(total_metrics, org_metrics)
    merged_total = add_metrics(personal_metrics, org_metrics)
    languages = merged_languages(total_collection, org_collection)
    return ContributionStats(
        days=tuple(days),
        personal_metrics=personal_metrics,
        org_metrics=org_metrics,
        total_metrics=merged_total,
        total_contributions=sum(day.total for day in days),
        repo_totals=repo_totals,
        languages=languages,
    )


def calendar_by_date(collection: dict[str, Any]) -> dict[date, int]:
    """把 contributionCalendar 转成日期到贡献数的映射。

    Args:
        collection: contributionsCollection 对象。

    Returns:
        日期到贡献数映射。
    """
    result: dict[date, int] = {}
    weeks = collection["contributionCalendar"]["weeks"]
    for week in weeks:
        for day in week["contributionDays"]:
            result[date.fromisoformat(day["date"][:10])] = int(day["contributionCount"])
    return result


def collection_metrics(collection: dict[str, Any]) -> MetricSet:
    """从 contributionsCollection 读取雷达图指标。"""
    return MetricSet(
        commits=int(collection["totalCommitContributions"]),
        issues=int(collection["totalIssueContributions"]),
        pull_requests=int(collection["totalPullRequestContributions"]),
        reviews=int(collection["totalPullRequestReviewContributions"]),
        repositories=int(collection["totalRepositoryContributions"]),
    )


def subtract_metrics(total: MetricSet, subset: MetricSet) -> MetricSet:
    """从总指标中扣除组织指标并钳制为非负值。"""
    return MetricSet(
        commits=max(total.commits - subset.commits, 0),
        issues=max(total.issues - subset.issues, 0),
        pull_requests=max(total.pull_requests - subset.pull_requests, 0),
        reviews=max(total.reviews - subset.reviews, 0),
        repositories=max(total.repositories - subset.repositories, 0),
    )


def add_metrics(first: MetricSet, second: MetricSet) -> MetricSet:
    """合并两组指标。"""
    return MetricSet(
        commits=first.commits + second.commits,
        issues=first.issues + second.issues,
        pull_requests=first.pull_requests + second.pull_requests,
        reviews=first.reviews + second.reviews,
        repositories=first.repositories + second.repositories,
    )


def merged_languages(
    total_collection: dict[str, Any],
    org_collection: dict[str, Any],
) -> tuple[LanguageStat, ...]:
    """合并个人与 SynlysAI 的语言贡献统计。

    Args:
        total_collection: 用户总 contributionsCollection。
        org_collection: 组织过滤 contributionsCollection。

    Returns:
        按贡献数量降序排列的语言统计。
    """
    total_languages = collection_languages(total_collection)
    org_languages = collection_languages(org_collection)
    names = set(total_languages) | set(org_languages)
    merged: list[LanguageStat] = []
    for name in names:
        total_count, total_color = total_languages.get(name, (0, "#8b949e"))
        org_count, org_color = org_languages.get(name, (0, total_color))
        personal_count = max(total_count - org_count, 0)
        count = personal_count + org_count
        if count > 0:
            merged.append(LanguageStat(name=name, color=org_color or total_color, count=count))
    return tuple(sorted(merged, key=lambda item: (-item.count, item.name)))


def collection_languages(collection: dict[str, Any]) -> dict[str, tuple[int, str]]:
    """从 commitContributionsByRepository 统计主要语言贡献。

    Args:
        collection: contributionsCollection 对象。

    Returns:
        语言名到贡献数和颜色的映射。
    """
    languages: dict[str, tuple[int, str]] = {}
    repositories = collection.get("commitContributionsByRepository") or []
    for item in repositories:
        repository = item.get("repository") or {}
        language = repository.get("primaryLanguage") or {}
        name = language.get("name")
        if not name:
            continue
        count = int((item.get("contributions") or {}).get("totalCount") or 0)
        current_count, current_color = languages.get(name, (0, "#8b949e"))
        languages[name] = (
            current_count + count,
            str(language.get("color") or current_color),
        )
    return languages


def rewrite_profile_svgs(profile_dir: Path, stats: ContributionStats) -> None:
    """覆盖处理 profile 目录下所有主题 SVG。

    Args:
        profile_dir: SVG 输出目录。
        stats: 融合后的贡献统计。
    """
    for svg_path in sorted(profile_dir.glob("*.svg")):
        if svg_path.name in {"profile-green-combined.svg", "synlysai-pr-summary.svg"}:
            continue
        svg = svg_path.read_text(encoding="utf-8")
        svg = remove_legacy_overlay(svg)
        svg = rewrite_heatmap(svg, stats.days)
        svg = rewrite_radar(svg, stats)
        svg = rewrite_language_chart(svg, stats.languages)
        svg = rewrite_bottom_totals(svg, stats)
        svg_path.write_text(svg, encoding="utf-8", newline="\n")


def remove_legacy_outputs(profile_dir: Path) -> None:
    """删除旧的独立统计输出文件。

    Args:
        profile_dir: SVG 输出目录。
    """
    for name in ("profile-green-combined.svg", "synlysai-pr-summary.svg"):
        path = profile_dir / name
        if path.exists():
            path.unlink()


def remove_legacy_overlay(svg: str) -> str:
    """删除旧版 Contribution Origin 面板。"""
    return re.sub(
        r"\s*<g id=\"synlysai-origin-overlay\">.*?</g>\s*",
        "",
        svg,
        flags=re.DOTALL,
    )


def rewrite_heatmap(svg: str, days: tuple[DayContribution, ...]) -> str:
    """把 3D 热力图格子改写为个人/组织双色分层柱。

    Args:
        svg: 原始 SVG。
        days: 单日贡献拆分序列。

    Returns:
        改写后的 SVG。
    """
    polygon_index = svg.find('<polygon class="radar"')
    if polygon_index < 0:
        return svg
    radar_start = svg.rfind('<g transform="translate(', 0, polygon_index)
    marker = 'class="fill-bg"></rect><g>'
    heatmap_marker = svg.find(marker)
    if heatmap_marker < 0 or radar_start < 0:
        return svg
    heatmap_inner_start = heatmap_marker + len(marker)
    heatmap_inner_end = radar_start - len("</g>") if svg[radar_start - 4 : radar_start] == "</g>" else radar_start
    original_inner = svg[heatmap_inner_start:heatmap_inner_end]
    groups = parse_day_groups(original_inner)
    aligned_days = align_days_to_groups(days, len(groups))
    rewritten_groups = [
        build_day_group(group, day)
        for group, day in zip(groups, aligned_days, strict=False)
    ]
    return (
        svg[:heatmap_inner_start]
        + "".join(rewritten_groups)
        + svg[heatmap_inner_end:]
    )


@dataclass(frozen=True)
class DayGroup:
    """原始 SVG 中单日 3D 柱的几何信息。"""

    x: float
    y: float
    base_y: float
    animated: bool


def parse_day_groups(svg_inner: str) -> list[DayGroup]:
    """解析原始热力图中每个日期格子的坐标和底部基线。

    Args:
        svg_inner: 热力图外层 g 内部 SVG。

    Returns:
        日期格几何信息列表。
    """
    groups: list[DayGroup] = []
    pattern = re.compile(
        r'<g transform="translate\(([-\d.]+) ([-\d.]+)\)">(.*?)</g>',
        flags=re.DOTALL,
    )
    for match in pattern.finditer(svg_inner):
        x = float(match.group(1))
        y = float(match.group(2))
        body = match.group(3)
        height, scale_y = parse_left_panel_height(body)
        groups.append(
            DayGroup(
                x=x,
                y=y,
                base_y=y + height * scale_y,
                animated="animateTransform" in body,
            )
        )
    return groups


def parse_left_panel_height(body: str) -> tuple[float, float]:
    """从单日柱体中读取左侧面高度和缩放。

    Args:
        body: 单日柱体 SVG。

    Returns:
        左侧面高度和 y 方向缩放。
    """
    match = re.search(
        r'<rect[^>]*height="([\d.]+)"[^>]*transform="skewY\(30\) scale\(([-\d.]+) ([-\d.]+)\)"',
        body,
    )
    if not match:
        return 2.6, SCALE_Y
    return float(match.group(1)), float(match.group(3))


def align_days_to_groups(
    days: tuple[DayContribution, ...],
    group_count: int,
) -> tuple[DayContribution, ...]:
    """把 GraphQL 日期序列对齐到 SVG 格子数量。"""
    if len(days) == group_count:
        return days
    if len(days) > group_count:
        return days[-group_count:]
    padding = tuple(
        DayContribution(day=date.min + timedelta(days=index), personal=0, org=0)
        for index in range(group_count - len(days))
    )
    return padding + days


def build_day_group(group: DayGroup, day: DayContribution) -> str:
    """生成单日双色 3D 柱。

    Args:
        group: 原 SVG 日期格几何信息。
        day: 单日贡献拆分。

    Returns:
        单日 SVG 片段。
    """
    if day.total <= 0:
        return neutral_day_group(group)

    total_height = contribution_height(day.total)
    personal_height = total_height * (day.personal / day.total) if day.personal else 0.0
    org_height = total_height - personal_height if day.org else 0.0
    y = group.base_y - total_height
    parts = [f'<g transform="translate({fmt(group.x)} {fmt(y)})">']
    if group.animated:
        parts.append(
            '<animateTransform attributeName="transform" type="translate" '
            f'values="{fmt(group.x)} {fmt(group.base_y - 3)};{fmt(group.x)} {fmt(y)}" '
            'dur="3s" repeatCount="1"></animateTransform>'
        )

    if org_height > 0:
        parts.append(build_segment(0.0, org_height, ORG_TOP, ORG_LEFT, ORG_RIGHT, top=True))
    if personal_height > 0:
        offset = org_height if org_height > 0 else 0.0
        parts.append(
            build_segment(
                offset,
                personal_height,
                PERSONAL_TOP,
                PERSONAL_LEFT,
                PERSONAL_RIGHT,
                top=org_height <= 0,
            )
        )
    parts.append(f"<title>{day.day.isoformat()}: personal {day.personal}, SynlysAI {day.org}</title>")
    parts.append("</g>")
    return "".join(parts)


def neutral_day_group(group: DayGroup) -> str:
    """生成无贡献日期的浅灰格。"""
    y = group.base_y - 3
    return (
        f'<g transform="translate({fmt(group.x)} {fmt(y)})">'
        f'{top_panel(NEUTRAL_TOP)}'
        f'{side_panel(0.0, 3.0, NEUTRAL_LEFT, "left")}'
        f'{side_panel(0.0, 3.0, NEUTRAL_RIGHT, "right")}'
        "</g>"
    )


def build_segment(
    offset_y: float,
    height: float,
    top_color: str,
    left_color: str,
    right_color: str,
    *,
    top: bool,
) -> str:
    """生成一个 3D 柱体颜色分层。

    Args:
        offset_y: 分层相对顶端的 y 偏移。
        height: 分层高度。
        top_color: 顶面颜色。
        left_color: 左侧面颜色。
        right_color: 右侧面颜色。
        top: 是否绘制顶面。

    Returns:
        SVG 片段。
    """
    parts = [f'<g transform="translate(0 {fmt(offset_y)})">']
    if top:
        parts.append(top_panel(top_color))
    parts.append(side_panel(0.0, height, left_color, "left"))
    parts.append(side_panel(0.0, height, right_color, "right"))
    parts.append("</g>")
    return "".join(parts)


def top_panel(color: str) -> str:
    """生成柱体顶面。"""
    return (
        '<rect stroke="none" x="0" y="0" '
        f'width="{fmt(DAY_TOP_SIZE)}" height="{fmt(DAY_TOP_SIZE)}" '
        f'fill="{color}" transform="skewY(-30) skewX(40.89) scale(1 1.15)"></rect>'
    )


def side_panel(offset_y: float, height: float, color: str, side: str) -> str:
    """生成柱体侧面。"""
    rect_height = max(height / SCALE_Y, 2.6)
    if side == "left":
        transform = "skewY(30) scale(1 1.15)"
    else:
        transform = f"translate({fmt(DAY_TOP_SIZE)} {fmt(DAY_SIDE_Y)}) skewY(-30) scale(1 1.15)"
    return (
        f'<rect stroke="none" x="0" y="{fmt(offset_y)}" '
        f'width="{fmt(DAY_TOP_SIZE)}" height="{fmt(rect_height)}" '
        f'fill="{color}" transform="{transform}"></rect>'
    )


def contribution_height(count: int) -> float:
    """按原 action 公式把贡献数转换为 3D 柱高。"""
    return math.log10(count / 20 + 1) * 144 + 3


def rewrite_radar(svg: str, stats: ContributionStats) -> str:
    """重写雷达图为个人、SynlysAI、合计三层。"""
    polygon_index = svg.find('<polygon class="radar"')
    if polygon_index < 0:
        return svg
    group_start = svg.rfind('<g transform="translate(', 0, polygon_index)
    next_section = svg.find('<g transform="translate(40, 520)', polygon_index)
    if group_start < 0 or next_section < 0:
        return svg
    group_end = next_section
    if svg[group_end - 4 : group_end] == "</g>":
        group_end -= 4
    transform_match = re.search(r'<g transform="translate\(([^"]+)\)">', svg[group_start:])
    transform = transform_match.group(1) if transform_match else "980, 284.5"
    radar = build_radar_group(transform, stats)
    return svg[:group_start] + radar + svg[next_section:]


def build_radar_group(transform: str, stats: ContributionStats) -> str:
    """构建分层雷达图 SVG。"""
    radius = 156.0
    levels = len(RADAR_RANGE_LABELS)
    personal_values = stats.personal_metrics.values()
    org_values = stats.org_metrics.values()
    total_values = stats.total_metrics.values()
    parts = [f'<g transform="translate({transform})">']

    for level in range(1, levels + 1):
        for axis in range(len(RADAR_LABELS)):
            parts.append(
                '<line '
                f'x1="{radar_x(radius, level, axis)}" y1="{radar_y(radius, level, axis)}" '
                f'x2="{radar_x(radius, level, axis + 1)}" y2="{radar_y(radius, level, axis + 1)}" '
                'class="stroke-weak" style="stroke-dasharray: 4 4; stroke-width: 1px;"></line>'
            )
    for index, label in enumerate(RADAR_RANGE_LABELS, start=1):
        parts.append(
            f'<text style="font-size: 13px;" text-anchor="start" dominant-baseline="auto" '
            f'x="3.12" y="{fmt(-radius * index / levels)}" class="fill-weak">{label}</text>'
        )
    for axis, (label, value) in enumerate(zip(RADAR_LABELS, total_values, strict=True)):
        parts.append(
            '<g class="axis">'
            f'<line x1="{radar_x(radius, 1, axis)}" y1="{radar_y(radius, 1, axis)}" '
            f'x2="{radar_x(radius, levels, axis)}" y2="{radar_y(radius, levels, axis)}" '
            'class="stroke-weak" style="stroke-dasharray: 4 4; stroke-width: 1px;"></line>'
            f'<text style="font-size: 20.8px;" text-anchor="middle" dominant-baseline="middle" '
            f'x="{radar_x(radius, 6.25, axis)}" y="{radar_y(radius, 5.85, axis)}" '
            f'class="fill-fg">{label}<title>{value}</title></text>'
            "</g>"
        )
    parts.append(
        f'<polygon points="{radar_points(radius, personal_values)}" '
        f'fill="{PERSONAL_TOP}" fill-opacity="0.28" stroke="{PERSONAL_TOP}" stroke-width="2"></polygon>'
    )
    parts.append(
        f'<polygon points="{radar_points(radius, org_values)}" '
        f'fill="{ORG_TOP}" fill-opacity="0.32" stroke="{ORG_TOP}" stroke-width="2"></polygon>'
    )
    parts.append(
        f'<polygon points="{radar_points(radius, total_values)}" '
        f'fill="none" stroke="{TOTAL_RADAR}" stroke-width="2.4"></polygon>'
    )
    parts.append(radar_legend())
    parts.append("</g>")
    return "".join(parts)


def radar_legend() -> str:
    """生成雷达图极简图例。"""
    return (
        '<g transform="translate(-120 -204)">'
        f'<rect x="0" y="0" width="10" height="10" fill="{PERSONAL_TOP}" opacity="0.8"></rect>'
        '<text x="16" y="10" font-size="12" class="fill-fg">Personal</text>'
        f'<rect x="86" y="0" width="10" height="10" fill="{ORG_TOP}" opacity="0.9"></rect>'
        '<text x="102" y="10" font-size="12" class="fill-fg">SynlysAI</text>'
        f'<line x1="184" y1="5" x2="208" y2="5" stroke="{TOTAL_RADAR}" stroke-width="2.4"></line>'
        '<text x="214" y="10" font-size="12" class="fill-fg">Total</text>'
        '</g>'
    )


def radar_points(radius: float, values: tuple[int, int, int, int, int]) -> str:
    """把雷达图指标转换为 polygon points。"""
    return " ".join(
        f"{radar_x(radius, radar_level(value), index)},{radar_y(radius, radar_level(value), index)}"
        for index, value in enumerate(values)
    )


def radar_level(value: int) -> float:
    """按原 action 的对数比例转换雷达图层级。"""
    if value < 1:
        return 0.8
    return min(math.log10(value), 5) + 1


def radar_x(radius: float, level: float, axis: int) -> str:
    """计算雷达图 x 坐标。"""
    return fmt(radius * (level / len(RADAR_RANGE_LABELS)) * math.sin((axis / len(RADAR_LABELS)) * 2 * math.pi))


def radar_y(radius: float, level: float, axis: int) -> str:
    """计算雷达图 y 坐标。"""
    return fmt(radius * (level / len(RADAR_RANGE_LABELS)) * -math.cos((axis / len(RADAR_LABELS)) * 2 * math.pi))


def rewrite_bottom_totals(svg: str, stats: ContributionStats) -> str:
    """重写底部 contributions、stars、forks 统计。"""
    svg = re.sub(
        r'(<text style="font-size: 32px; font-weight: bold;" x="384" y="830" text-anchor="end" class="fill-strong">)\d+(</text><text style="font-size: 24px;" x="394" y="830" text-anchor="start" class="fill-fg">contributions</text>)',
        rf"\g<1>{stats.total_contributions}\g<2>",
        svg,
        count=1,
    )
    svg = replace_counter_after_x(svg, "650", stats.repo_totals.stars)
    svg = replace_counter_after_x(svg, "772", stats.repo_totals.forks)
    return svg


def replace_counter_after_x(svg: str, x_value: str, value: int) -> str:
    """替换指定 x 坐标处的底部计数。"""
    pattern = (
        rf'(<text style="font-size: 32px; font-weight: bold;" x="{x_value}" '
        rf'y="830" text-anchor="start" class="fill-fg">)\d+(<title>)\d+(</title></text>)'
    )
    return re.sub(pattern, rf"\g<1>{value}\g<2>{value}\g<3>", svg, count=1)


def rewrite_language_chart(svg: str, languages: tuple[LanguageStat, ...]) -> str:
    """重写语言环图为个人与 SynlysAI 的合并语言贡献。

    Args:
        svg: 原始 SVG。
        languages: 合并后的语言贡献统计。

    Returns:
        改写后的 SVG。
    """
    if not languages:
        return svg
    section_start = svg.find('<g transform="translate(40, 520)">')
    bottom_start = svg.find('<g><text style="font-size: 32px;', section_start)
    if section_start < 0 or bottom_start < 0:
        return svg
    return svg[:section_start] + build_language_chart(languages) + svg[bottom_start:]


def build_language_chart(languages: tuple[LanguageStat, ...]) -> str:
    """生成语言图例和环图 SVG。

    Args:
        languages: 合并后的语言贡献统计。

    Returns:
        语言统计 SVG 片段。
    """
    top_languages = languages[:5]
    total = sum(language.count for language in top_languages)
    if total <= 0:
        return ""

    parts = ['<g transform="translate(40, 520)">']
    parts.append('<g transform="translate(273, 0)">')
    swatch_size = 18
    row_gap = 28
    start_y = 60
    for index, language in enumerate(top_languages):
        y = start_y + index * row_gap
        parts.append(
            f'<rect x="0" y="{y}" width="{swatch_size}" height="{swatch_size}" '
            f'fill="{escape(language.color)}" class="stroke-bg" stroke-width="1px"></rect>'
        )
        parts.append(
            f'<text dominant-baseline="middle" x="25" y="{y + swatch_size / 2}" '
            f'class="fill-fg" font-size="19px">{escape(language.name)}</text>'
        )
    parts.append("</g>")

    parts.append('<g transform="translate(130, 130)">')
    start_angle = -math.pi / 2
    for language in top_languages:
        end_angle = start_angle + (2 * math.pi * language.count / total)
        parts.append(
            f'<path d="{donut_segment(start_angle, end_angle)}" '
            f'style="fill: {escape(language.color)};" class="stroke-bg" stroke-width="2px">'
            f'<title>{escape(language.name)} {language.count}</title></path>'
        )
        start_angle = end_angle
    parts.append("</g></g>")
    return "".join(parts)


def donut_segment(start_angle: float, end_angle: float) -> str:
    """生成语言环图单个扇区路径。

    Args:
        start_angle: 扇区起始弧度。
        end_angle: 扇区结束弧度。

    Returns:
        SVG path d 属性。
    """
    outer_radius = 117.0
    inner_radius = 65.0
    if end_angle - start_angle >= 2 * math.pi - 0.001:
        return (
            "M0,-117"
            "A117,117,0,1,1,0,117"
            "A117,117,0,1,1,0,-117"
            "L0,-65"
            "A65,65,0,1,0,0,65"
            "A65,65,0,1,0,0,-65Z"
        )

    outer_start = arc_point(outer_radius, start_angle)
    outer_end = arc_point(outer_radius, end_angle)
    inner_end = arc_point(inner_radius, end_angle)
    inner_start = arc_point(inner_radius, start_angle)
    large_arc = 1 if end_angle - start_angle > math.pi else 0
    return (
        f"M{fmt(outer_start[0])},{fmt(outer_start[1])}"
        f"A{fmt(outer_radius)},{fmt(outer_radius)},0,{large_arc},1,{fmt(outer_end[0])},{fmt(outer_end[1])}"
        f"L{fmt(inner_end[0])},{fmt(inner_end[1])}"
        f"A{fmt(inner_radius)},{fmt(inner_radius)},0,{large_arc},0,{fmt(inner_start[0])},{fmt(inner_start[1])}Z"
    )


def arc_point(radius: float, angle: float) -> tuple[float, float]:
    """计算环图弧线端点。"""
    return radius * math.cos(angle), radius * math.sin(angle)


def read_date_range(source_svg: Path) -> tuple[date, date]:
    """从基础 SVG 读取统计日期范围。

    Args:
        source_svg: 基础 SVG 文件路径。

    Returns:
        起止日期。
    """
    if source_svg.exists():
        text = source_svg.read_text(encoding="utf-8")
        match = re.search(r"(\d{4}-\d{2}-\d{2}) / (\d{4}-\d{2}-\d{2})", text)
        if match:
            return date.fromisoformat(match.group(1)), date.fromisoformat(match.group(2))
    today = datetime.now(UTC).date()
    return today - timedelta(days=365), today


def load_stats_fixture(path: Path) -> ContributionStats:
    """从测试 fixture 读取贡献统计。"""
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    days = tuple(
        DayContribution(
            day=date.fromisoformat(item["date"]),
            personal=int(item["personal"]),
            org=int(item["org"]),
        )
        for item in payload["days"]
    )
    personal = MetricSet(**payload["personal_metrics"])
    org = MetricSet(**payload["org_metrics"])
    total = add_metrics(personal, org)
    repo_totals = RepoTotals(**payload.get("repo_totals", {"stars": 0, "forks": 0}))
    return ContributionStats(
        days=days,
        personal_metrics=personal,
        org_metrics=org,
        total_metrics=total,
        total_contributions=sum(day.total for day in days),
        repo_totals=repo_totals,
        languages=tuple(
            LanguageStat(
                name=item["name"],
                color=item.get("color", "#8b949e"),
                count=int(item["count"]),
            )
            for item in payload.get("languages", [])
        ),
    )


def fmt(value: float) -> str:
    """按 SVG 需要输出短小数字。"""
    text = f"{value:.2f}"
    return text.rstrip("0").rstrip(".")


def escape(value: object) -> str:
    """转义 SVG 文本内容。"""
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


if __name__ == "__main__":
    sys.exit(main())
