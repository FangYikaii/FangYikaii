"""生成融合 SynlysAI 组织数据的 GitHub profile SVG。"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, date, datetime, timedelta
from pathlib import Path


API_ROOT = "https://api.github.com"
ISSUE_SEARCH_URL = f"{API_ROOT}/search/issues"
DEFAULT_ORG = "SynlysAI"
DEFAULT_OUTPUT = "profile-3d-contrib/profile-green-combined.svg"
DEFAULT_SOURCE = "profile-3d-contrib/profile-green.svg"
DEFAULT_USERNAME = "FangYikaii"
MAX_REPOS = 300


def main() -> int:
    """生成带来源分层统计的 profile SVG。"""
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    org = os.environ.get("ORG", DEFAULT_ORG).strip()
    username = os.environ.get("GITHUB_USERNAME", DEFAULT_USERNAME).strip()
    source = Path(os.environ.get("SOURCE_SVG", DEFAULT_SOURCE))
    output = Path(os.environ.get("OUTPUT", DEFAULT_OUTPUT))
    today = datetime.now(UTC).date()
    since = os.environ.get("SINCE", str(today - timedelta(days=365))).strip()

    if not source.exists():
        raise FileNotFoundError(f"source SVG not found: {source}")

    source_svg = source.read_text(encoding="utf-8")
    width, height = read_svg_size(source_svg)
    if not token:
        stats = empty_stats()
        message = "PROFILE_GITHUB_TOKEN is required for organization data."
    else:
        try:
            stats = collect_stats(token, org, username, since)
            message = ""
        except RuntimeError as exc:
            stats = empty_stats()
            message = str(exc)

    overlay = build_overlay(width, height, org, username, since, today, stats, message)
    output.write_text(inject_overlay(source_svg, overlay), encoding="utf-8", newline="\n")
    return 0


def collect_stats(
    token: str,
    org: str,
    username: str,
    since: str,
) -> dict[str, int]:
    """查询个人仓库与组织仓库来源的提交和 PR 统计。

    Args:
        token: 可读取目标组织仓库的 GitHub token。
        org: GitHub 组织名。
        username: GitHub 用户名。
        since: 起始日期，格式为 YYYY-MM-DD。

    Returns:
        提交和 PR 统计字典。
    """
    org_repos = list_repositories(
        token,
        f"{API_ROOT}/orgs/{org}/repos?type=all&sort=updated&per_page=100",
        MAX_REPOS,
    )
    personal_repos = list_repositories(
        token,
        f"{API_ROOT}/users/{username}/repos?type=owner&sort=updated&per_page=100",
        MAX_REPOS,
    )
    org_commits = sum(
        count_repo_commits(token, repo["full_name"], username, since)
        for repo in org_repos
    )
    personal_commits = sum(
        count_repo_commits(token, repo["full_name"], username, since)
        for repo in personal_repos
    )
    org_prs = search_count(
        token,
        f"org:{org} is:pr author:{username} created:>={since}",
    )
    merged_org_prs = search_count(
        token,
        f"org:{org} is:pr is:merged author:{username} created:>={since}",
    )

    return {
        "org_commits": org_commits,
        "personal_commits": personal_commits,
        "org_repos": len(org_repos),
        "personal_repos": len(personal_repos),
        "org_prs": org_prs,
        "merged_org_prs": merged_org_prs,
    }


def list_repositories(token: str, url: str, max_repos: int) -> list[dict[str, object]]:
    """读取仓库列表并过滤 fork 仓库。

    Args:
        token: GitHub API token。
        url: 仓库列表 API 地址。
        max_repos: 最大仓库数量。

    Returns:
        非 fork 仓库列表。
    """
    repos: list[dict[str, object]] = []
    next_url = url
    while next_url and len(repos) < max_repos:
        payload, headers = github_json(token, next_url)
        if not isinstance(payload, list):
            break
        repos.extend(repo for repo in payload if not repo.get("fork"))
        next_url = parse_link_by_rel(headers.get("Link", ""), "next")
    return repos[:max_repos]


def count_repo_commits(
    token: str,
    full_name: object,
    username: str,
    since: str,
) -> int:
    """统计单个仓库中指定作者的提交数量。

    Args:
        token: GitHub API token。
        full_name: 仓库全名。
        username: GitHub 用户名。
        since: 起始日期，格式为 YYYY-MM-DD。

    Returns:
        提交数量。
    """
    query = urllib.parse.urlencode(
        {
            "author": username,
            "since": f"{since}T00:00:00Z",
            "per_page": 1,
        }
    )
    url = f"{API_ROOT}/repos/{full_name}/commits?{query}"
    try:
        payload, headers = github_json(token, url)
    except RuntimeError:
        return 0

    link_count = parse_last_page(headers.get("Link", ""))
    if link_count is not None:
        return link_count
    if isinstance(payload, list):
        return len(payload)
    return 0


def search_count(token: str, query: str) -> int:
    """获取 GitHub issue 搜索结果总数。

    Args:
        token: GitHub API token。
        query: GitHub issue 搜索查询语句。

    Returns:
        搜索结果总数。
    """
    params = urllib.parse.urlencode({"q": query, "per_page": 1})
    payload, _headers = github_json(token, f"{ISSUE_SEARCH_URL}?{params}")
    if isinstance(payload, dict):
        return int(payload.get("total_count", 0))
    return 0


def github_json(token: str, url: str) -> tuple[object, dict[str, str]]:
    """请求 GitHub JSON API。

    Args:
        token: GitHub API token。
        url: API 地址。

    Returns:
        JSON 数据和响应头。
    """
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "User-Agent": "FangYikaii-profile-visuals",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
            headers = dict(response.headers.items())
    except urllib.error.HTTPError as exc:
        message = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"GitHub API failed with HTTP {exc.code}: {message[:140]}"
        ) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"GitHub API failed: {exc}") from exc
    return payload, headers


def parse_last_page(link_header: str) -> int | None:
    """从分页响应中读取最后一页页码。"""
    last_link = parse_link_by_rel(link_header, "last")
    if not last_link:
        return None
    parsed = urllib.parse.urlparse(last_link)
    query = urllib.parse.parse_qs(parsed.query)
    page_values = query.get("page")
    if not page_values:
        return None
    return int(page_values[0])


def parse_link_by_rel(link_header: str, rel: str) -> str:
    """按 rel 解析 GitHub Link 响应头。"""
    for part in link_header.split(","):
        match = re.search(r'<([^>]+)>;\s*rel="([^"]+)"', part.strip())
        if match and match.group(2) == rel:
            return match.group(1)
    return ""


def empty_stats() -> dict[str, int]:
    """返回空统计结构。"""
    return {
        "org_commits": 0,
        "personal_commits": 0,
        "org_repos": 0,
        "personal_repos": 0,
        "org_prs": 0,
        "merged_org_prs": 0,
    }


def read_svg_size(svg: str) -> tuple[int, int]:
    """读取 SVG 的宽高。

    Args:
        svg: SVG 文本。

    Returns:
        SVG 宽高。
    """
    width_match = re.search(r'\bwidth="(\d+)"', svg)
    height_match = re.search(r'\bheight="(\d+)"', svg)
    if not width_match or not height_match:
        return 1280, 850
    return int(width_match.group(1)), int(height_match.group(1))


def build_overlay(
    width: int,
    height: int,
    org: str,
    username: str,
    since: str,
    today: date,
    stats: dict[str, int],
    message: str,
) -> str:
    """构建融合到 3D 图内部的来源统计图层。

    Args:
        width: 原 SVG 宽度。
        height: 原 SVG 高度。
        org: GitHub 组织名。
        username: GitHub 用户名。
        since: 起始日期。
        today: 生成日期。
        stats: 提交和 PR 统计。
        message: 查询失败时的提示信息。

    Returns:
        SVG 图层字符串。
    """
    panel_x = 462 if width >= 900 else 24
    panel_y = 36 if height >= 240 else 24
    panel_width = 368
    panel_height = 128
    commit_bar = stacked_bar(
        panel_x + 22,
        panel_y + 74,
        216,
        stats["org_commits"],
        stats["personal_commits"],
    )
    repo_bar = stacked_bar(
        panel_x + 22,
        panel_y + 108,
        216,
        stats["org_repos"],
        stats["personal_repos"],
    )
    footnote = (
        "Token cannot read org data"
        if message
        else f"{since} / {today} · names hidden"
    )

    return f"""
  <g id="synlysai-origin-overlay">
    <style>
      .syn-panel {{ fill: #ffffff; fill-opacity: 0.94; stroke: #cfd8d1; stroke-width: 1; }}
      .syn-title {{ fill: #172119; font: 700 18px Ubuntu, Helvetica, Arial, sans-serif; }}
      .syn-subtle {{ fill: #59665d; font: 11px Ubuntu, Helvetica, Arial, sans-serif; }}
      .syn-label {{ fill: #2f3b33; font: 700 12px Ubuntu, Helvetica, Arial, sans-serif; }}
      .syn-value {{ fill: #122016; font: 800 18px Ubuntu, Helvetica, Arial, sans-serif; }}
      .syn-org {{ fill: #197a38; }}
      .syn-personal {{ fill: #2f78b7; }}
      .syn-track {{ fill: #eef3ef; }}
    </style>
    <rect class="syn-panel" x="{panel_x}" y="{panel_y}" width="{panel_width}" height="{panel_height}" rx="8"/>
    <text class="syn-title" x="{panel_x + 20}" y="{panel_y + 30}">Contribution Origin</text>
    <text class="syn-subtle" x="{panel_x + 20}" y="{panel_y + 50}">author:{escape(username)} · SynlysAI org vs personal repos</text>
    <text class="syn-label" x="{panel_x + 20}" y="{panel_y + 69}">Commits</text>
    {commit_bar}
    <text class="syn-value" x="{panel_x + 252}" y="{panel_y + 80}">{stats["org_commits"]}</text>
    <text class="syn-subtle" x="{panel_x + 286}" y="{panel_y + 80}">{escape(org)}</text>
    <text class="syn-label" x="{panel_x + 20}" y="{panel_y + 103}">Repos</text>
    {repo_bar}
    <text class="syn-value" x="{panel_x + 252}" y="{panel_y + 114}">{stats["org_prs"]}</text>
    <text class="syn-subtle" x="{panel_x + 286}" y="{panel_y + 114}">org PRs · {stats["merged_org_prs"]} merged</text>
    <rect class="syn-org" x="{panel_x + 20}" y="{panel_y + 119}" width="9" height="9" rx="2"/>
    <text class="syn-subtle" x="{panel_x + 34}" y="{panel_y + 128}">SynlysAI</text>
    <rect class="syn-personal" x="{panel_x + 100}" y="{panel_y + 119}" width="9" height="9" rx="2"/>
    <text class="syn-subtle" x="{panel_x + 114}" y="{panel_y + 128}">personal</text>
    <text class="syn-subtle" x="{panel_x + 180}" y="{panel_y + 128}">{escape(footnote)}</text>
  </g>
"""


def stacked_bar(x: int, y: int, width: int, first: int, second: int) -> str:
    """构建双来源堆叠条。

    Args:
        x: 条形图左侧坐标。
        y: 条形图顶部坐标。
        width: 条形图宽度。
        first: 第一类计数。
        second: 第二类计数。

    Returns:
        SVG 片段。
    """
    total = max(first + second, 1)
    first_width = round(width * first / total, 2)
    second_width = max(width - first_width, 0)
    second_x = x + first_width
    return f"""
    <rect class="syn-track" x="{x}" y="{y}" width="{width}" height="10" rx="5"/>
    <rect class="syn-org" x="{x}" y="{y}" width="{first_width}" height="10" rx="5"/>
    <rect class="syn-personal" x="{second_x}" y="{y}" width="{second_width}" height="10" rx="5"/>
"""


def inject_overlay(svg: str, overlay: str) -> str:
    """把来源统计图层注入到 SVG 结束标签之前。

    Args:
        svg: 原始 SVG。
        overlay: 待注入图层。

    Returns:
        注入后的 SVG。
    """
    close_tag = "</svg>"
    if close_tag not in svg:
        raise ValueError("source SVG does not contain closing </svg> tag")
    return svg.replace(close_tag, f"{overlay}{close_tag}", 1)


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
