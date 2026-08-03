"""生成 SynlysAI 组织内个人 PR 汇总 SVG。"""

from __future__ import annotations

import json
import os
import sys
import textwrap
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path


API_URL = "https://api.github.com/search/issues"
DEFAULT_ORG = "SynlysAI"
DEFAULT_OUTPUT = "profile-3d-contrib/synlysai-pr-summary.svg"
DEFAULT_USERNAME = "FangYikaii"


def main() -> int:
    """读取 GitHub Search API 并写入组织 PR 汇总 SVG。"""
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    org = os.environ.get("ORG", DEFAULT_ORG).strip()
    username = os.environ.get("USERNAME", DEFAULT_USERNAME).strip()
    output = Path(os.environ.get("OUTPUT", DEFAULT_OUTPUT))
    today = datetime.now(UTC).date()
    since = os.environ.get("SINCE", str(today - timedelta(days=365))).strip()

    if not token:
        write_svg(
            output,
            build_placeholder_svg(
                "SynlysAI PR Activity",
                "PROFILE_GITHUB_TOKEN is required to read organization activity.",
            ),
        )
        return 0

    try:
        stats = collect_stats(token, org, username, since)
    except RuntimeError as exc:
        write_svg(output, build_placeholder_svg("SynlysAI PR Activity", str(exc)))
        return 0

    write_svg(output, build_summary_svg(org, username, since, today, stats))
    return 0


def collect_stats(
    token: str,
    org: str,
    username: str,
    since: str,
) -> dict[str, int]:
    """查询 GitHub Search API 中 SynlysAI 组织内作者 PR 数量。

    Args:
        token: 可读取目标组织仓库的 GitHub token。
        org: GitHub 组织名。
        username: GitHub 用户名。
        since: 起始日期，格式为 YYYY-MM-DD。

    Returns:
        组织 PR 汇总计数字典。
    """
    base = f"org:{org} is:pr author:{username}"
    queries = {
        "all_prs": f"{base} created:>={since}",
        "merged_prs": f"{base} is:merged created:>={since}",
        "open_prs": f"{base} is:open",
        "recent_prs": f"{base} updated:>={recent_since()}",
    }
    return {
        name: search_count(token, query)
        for name, query in queries.items()
    }


def recent_since() -> str:
    """返回近 30 天的起始日期。"""
    return str(datetime.now(UTC).date() - timedelta(days=30))


def search_count(token: str, query: str) -> int:
    """获取 GitHub 搜索结果总数。

    Args:
        token: GitHub API token。
        query: GitHub issue search 查询语句。

    Returns:
        搜索结果总数。
    """
    params = urllib.parse.urlencode({"q": query, "per_page": 1})
    request = urllib.request.Request(
        f"{API_URL}?{params}",
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
    except urllib.error.HTTPError as exc:
        message = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"GitHub search failed with HTTP {exc.code}: {message[:180]}"
        ) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"GitHub search failed: {exc}") from exc

    return int(payload.get("total_count", 0))


def build_summary_svg(
    org: str,
    username: str,
    since: str,
    today: datetime.date,
    stats: dict[str, int],
) -> str:
    """构建 PR 汇总 SVG。

    Args:
        org: GitHub 组织名。
        username: GitHub 用户名。
        since: 起始日期。
        today: 生成日期。
        stats: PR 汇总计数字典。

    Returns:
        SVG 字符串。
    """
    cards = [
        ("Opened PRs", stats["all_prs"], f"{since} / {today}"),
        ("Merged PRs", stats["merged_prs"], f"{since} / {today}"),
        ("Open PRs", stats["open_prs"], "currently open"),
        ("Updated PRs", stats["recent_prs"], "last 30 days"),
    ]
    card_markup = "\n".join(
        metric_card(48 + index * 294, title, value, label)
        for index, (title, value, label) in enumerate(cards)
    )
    generated_at = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="210" viewBox="0 0 1280 210" role="img" aria-labelledby="title desc">
  <title id="title">{escape(org)} PR activity for {escape(username)}</title>
  <desc id="desc">Organization pull request summary generated from GitHub Search API.</desc>
  <style>
    .bg {{ fill: #f7faf8; }}
    .panel {{ fill: #ffffff; stroke: #cfd8d1; stroke-width: 1; }}
    .title {{ fill: #132018; font: 700 24px Ubuntu, Helvetica, Arial, sans-serif; }}
    .subtle {{ fill: #5f6f64; font: 14px Ubuntu, Helvetica, Arial, sans-serif; }}
    .label {{ fill: #334238; font: 700 15px Ubuntu, Helvetica, Arial, sans-serif; }}
    .value {{ fill: #0f3b1d; font: 800 36px Ubuntu, Helvetica, Arial, sans-serif; }}
    .foot {{ fill: #6b746d; font: 12px Ubuntu, Helvetica, Arial, sans-serif; }}
    .accent {{ fill: #6aa84f; }}
  </style>
  <rect class="bg" width="1280" height="210" rx="0"/>
  <rect class="panel" x="24" y="20" width="1232" height="166" rx="8"/>
  <rect class="accent" x="24" y="20" width="8" height="166" rx="4"/>
  <text class="title" x="52" y="60">{escape(org)} Pull Request Activity</text>
  <text class="subtle" x="52" y="84">author:{escape(username)} · org:{escape(org)} · private repository names are not exposed</text>
  {card_markup}
  <text class="foot" x="52" y="166">Generated {generated_at}. Requires PROFILE_GITHUB_TOKEN with access to {escape(org)} repositories.</text>
</svg>
"""


def metric_card(x: int, title: str, value: int, label: str) -> str:
    """构建单个指标卡片 SVG 片段。

    Args:
        x: 卡片左侧坐标。
        title: 指标标题。
        value: 指标数值。
        label: 指标说明。

    Returns:
        SVG 片段。
    """
    return textwrap.dedent(
        f"""
        <g transform="translate({x} 104)">
          <rect x="0" y="0" width="250" height="50" rx="6" fill="#f1f6f2" stroke="#d7e1d9"/>
          <text class="label" x="16" y="20">{escape(title)}</text>
          <text class="value" x="16" y="48">{value}</text>
          <text class="subtle" x="116" y="46">{escape(label)}</text>
        </g>
        """
    ).strip()


def build_placeholder_svg(title: str, message: str) -> str:
    """构建无法查询 API 时的占位 SVG。

    Args:
        title: 标题。
        message: 提示信息。

    Returns:
        SVG 字符串。
    """
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="160" viewBox="0 0 1280 160" role="img" aria-labelledby="title desc">
  <title id="title">{escape(title)}</title>
  <desc id="desc">{escape(message)}</desc>
  <style>
    .bg {{ fill: #f7faf8; }}
    .panel {{ fill: #ffffff; stroke: #cfd8d1; stroke-width: 1; }}
    .title {{ fill: #132018; font: 700 24px Ubuntu, Helvetica, Arial, sans-serif; }}
    .subtle {{ fill: #5f6f64; font: 14px Ubuntu, Helvetica, Arial, sans-serif; }}
  </style>
  <rect class="bg" width="1280" height="160"/>
  <rect class="panel" x="24" y="20" width="1232" height="116" rx="8"/>
  <text class="title" x="52" y="64">{escape(title)}</text>
  <text class="subtle" x="52" y="92">{escape(message)}</text>
</svg>
"""


def escape(value: object) -> str:
    """转义 SVG 文本内容。"""
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def write_svg(path: Path, content: str) -> None:
    """写入 SVG 文件。

    Args:
        path: 输出文件路径。
        content: SVG 内容。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")


if __name__ == "__main__":
    sys.exit(main())
