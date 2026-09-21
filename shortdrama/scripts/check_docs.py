# -*- coding: utf-8 -*-
"""check_docs.py — 文档对账：数代码里的真实值，跟 README/AGENTS.md 写的数字对，对不上就报错。

背景：文档已漂移 4 处（图 8→9、模块 18→22、env 漏 14 个、craft"未接线"实为已接线）。
根因是手写快照没人对账。本脚本挂进质量门兜底：改结构后必跑，漂移当场露馅。

用法（在 shortdrama/ 目录下）：
    python scripts/check_docs.py             # 正常对账，exit 0=全对 / 1=有漂移
    python scripts/check_docs.py --self-test # 自测：注入假漂移，断言检测器真的会报错

校验项（只校验"机器可数"的事实；"说法类"漂移靠纪律）：
  1. 图数：v5/langgraph.json 的 graphs 键数 vs README 两处声明
  2. media 模块数：v5/media/*.py（不含 __init__）vs README 目录导览
  3. craft 技法名：实际目录名必须逐个出现在 README 与 AGENTS.md
     （外加：每份技法的 SKILL.md 必须写 inject-to frontmatter——静态前移运行时告警）
  4. env 覆盖：代码（v5+scripts+web）引用的 SHORTDRAMA_* 必须逐个被 README/AGENTS.md 提及；
     反向：文档提及的必须存在于代码（防死配置残留，白名单除外）
"""
import json
import re
import sys
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
# 已知死配置/文档特意保留的名字（代码里无引用是**设计内**的）
ENV_DOC_ONLY_OK = {"SHORTDRAMA_VIDEO_PROVIDER"}


def fail(msg):
    print("✗ " + msg)
    return False


def ok(msg):
    print("✓ " + msg)
    return True


def read(p):
    return pathlib.Path(p).read_text(encoding="utf-8")


def check_graphs():
    """图注册数：langgraph.json vs README 两处声明。"""
    graphs = json.loads(read(ROOT / "v5" / "langgraph.json"))["graphs"]
    n = len(graphs)
    readme = read(ROOT / "README.md")
    good = True
    m = re.search(r"图注册（(\d+) 图", readme)
    if not m:
        good = fail("README 找不到「图注册（N 图」锚点（目录导览）")
    elif int(m.group(1)) != n:
        good = fail("README 目录导览写 %s 图，langgraph.json 实际 %d 图" % (m.group(1), n))
    m = re.search(r"注册 \*\*(\d+) 张图\*\*", readme)
    if not m:
        good = fail("README 找不到「注册 **N 张图**」锚点（§11）")
    elif int(m.group(1)) != n:
        good = fail("README §11 写 %s 张图，langgraph.json 实际 %d 图" % (m.group(1), n))
    if good:
        ok("图数一致：%d 图（langgraph.json == README 两处）" % n)
    return good


def check_media_modules():
    """media 模块数：v5/media/*.py（不含 __init__）vs README 目录导览。"""
    mods = [p for p in (ROOT / "v5" / "media").glob("*.py") if p.name != "__init__.py"]
    n = len(mods)
    readme = read(ROOT / "README.md")
    m = re.search(r"media/\s*#\s*静态画面先行管线（(\d+) 个模块", readme)
    if not m:
        return fail("README 找不到「media/ …（N 个模块」锚点")
    if int(m.group(1)) != n:
        return fail("README 写 media %s 个模块，实际 %d 个" % (m.group(1), n))
    return ok("media 模块数一致：%d 个" % n)


def check_craft():
    """craft 技法：实际目录名逐个出现在 README+AGENTS；SKILL.md 必须有 inject-to。"""
    craft_dir = ROOT / "v5" / "skills" / "packs" / "craft"
    if not craft_dir.is_dir():
        return fail("craft 目录不存在：%s" % craft_dir)
    names = sorted(d.name for d in craft_dir.iterdir() if d.is_dir() and not d.name.startswith("."))
    readme = read(ROOT / "README.md")
    agents = read(ROOT / "AGENTS.md")
    good = True
    for n in names:
        if n not in readme:
            good = fail("技法 %r 未出现在 README（防「当前有 X」只写一个的漂移）" % n)
        if n not in agents:
            good = fail("技法 %r 未出现在 AGENTS.md" % n)
    for f in names:
        body = read(craft_dir / f / "SKILL.md") if (craft_dir / f / "SKILL.md").exists() else ""
        if not body:
            good = fail("技法 %r 缺 SKILL.md" % f)
        elif not re.search(r"^inject-to:", body, re.M):
            good = fail("技法 %r 的 SKILL.md 缺 inject-to frontmatter（运行时会不注入任何角色）" % f)
    if good:
        ok("craft 技法一致：%s（全部入文档、全部有 inject-to）" % " / ".join(names))
    return good


def code_env_names():
    """扫代码里引用的 SHORTDRAMA_*（v5+scripts+web，排除测试与缓存）。"""
    names = set()
    files = sorted(ROOT.glob("v5/**/*.py")) + sorted(ROOT.glob("scripts/*.py")) + sorted(ROOT.glob("web/**/*.py"))
    for p in files:
        if "tests_" in p.name or "__pycache__" in p.parts:
            continue
        if p.name == "check_docs.py":
            continue  # 排除自身：自测注入的假 env 名不能算"代码在用"
        names |= set(re.findall(r"SHORTDRAMA_[A-Z0-9_]+", p.read_text(encoding="utf-8", errors="ignore")))
    return names


def check_envs(code_names=None):
    """env 双向对账：代码引用的必须被文档提及；文档提及的必须存在代码（白名单除外）。"""
    code_names = code_env_names() if code_names is None else code_names
    doc_names = set()
    for f in ("README.md", "AGENTS.md"):
        doc_names |= set(re.findall(r"SHORTDRAMA_[A-Z0-9_]+", read(ROOT / f)))
    good = True
    missing = sorted(code_names - doc_names)
    if missing:
        good = fail("%d 个 env 代码在用但文档没提（补进 README §6）：\n      %s"
                    % (len(missing), "\n      ".join(missing)))
    dead = sorted(n for n in (doc_names - code_names) if n not in ENV_DOC_ONLY_OK)
    if dead:
        good = fail("文档提及但代码无引用的 env（确认是死配置就从文档删掉，或进白名单）：\n      %s"
                    % "\n      ".join(dead))
    if good:
        ok("env 对账一致：代码 %d 个全部入文档，文档无死配置" % len(code_names))
    return good


def main():
    selftest = "--self-test" in sys.argv
    results = []
    if selftest:
        # 自测：注入三种假漂移，检测器必须全部抓到，否则检测器自身是坏的。
        results.append(("selftest.graphs", _selftest_graphs()))
        results.append(("selftest.craft", _selftest_craft()))
        results.append(("selftest.env", _selftest_env()))
        bad = [name for name, good in results if not good]
        if bad:
            print("\nSELF-TEST FAILED（检测器漏报）: %s" % bad)
            return 2
        print("\nself-test 通过：检测器对三类注入漂移全部报错。")
        return 0
    results.append(("graphs", check_graphs()))
    results.append(("media_modules", check_media_modules()))
    results.append(("craft", check_craft()))
    results.append(("envs", check_envs()))
    bad = [name for name, good in results if not good]
    print()
    if bad:
        print("✗ 文档对账失败：%s —— 修文档或修代码，二者必须一致（README 是权威快照，"
              "改结构必须同步）。" % ", ".join(bad))
        return 1
    print("✓ 文档对账全部通过。")
    return 0


# ---------- self-test 实现：临时改写 ROOT 指向副本目录 ----------
def _selftest_graphs():
    global ROOT
    import shutil
    import tempfile
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="checkdocs_selftest_"))
    try:
        shutil.copytree(ROOT / "v5", tmp / "v5", ignore=shutil.ignore_patterns("__pycache__"))
        (tmp / "README.md").write_text(read(ROOT / "README.md").replace("图注册（9 图", "图注册（8 图"), encoding="utf-8")
        saved = ROOT
        try:
            globals()["ROOT"] = tmp
            good = check_graphs()  # 注入了漂移，必须返回 False（报错）
        finally:
            globals()["ROOT"] = saved
        return not good
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _selftest_craft():
    # 注入一个不存在的技法名要求：改用直接调 fail 路径——把 README 换成缺技法的版本
    global ROOT
    import shutil
    import tempfile
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="checkdocs_selftest_"))
    try:
        shutil.copytree(ROOT / "v5", tmp / "v5", ignore=shutil.ignore_patterns("__pycache__"))
        readme = read(ROOT / "README.md")
        # 抹掉 README 里对第一个技法的提及 → check_craft 必须报错
        names = sorted(d.name for d in (ROOT / "v5" / "skills" / "packs" / "craft").iterdir()
                       if d.is_dir() and not d.name.startswith("."))
        (tmp / "README.md").write_text(readme.replace(names[0], "XXXX-REMOVED"), encoding="utf-8")
        (tmp / "AGENTS.md").write_text(read(ROOT / "AGENTS.md").replace(names[0], "XXXX-REMOVED"), encoding="utf-8")
        saved = ROOT
        try:
            globals()["ROOT"] = tmp
            good = check_craft()
        finally:
            globals()["ROOT"] = saved
        return not good
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _selftest_env():
    global ROOT
    import shutil
    import tempfile
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="checkdocs_selftest_"))
    try:
        for sub in ("v5", "scripts", "web"):
            if (ROOT / sub).exists():
                shutil.copytree(ROOT / sub, tmp / sub,
                                ignore=shutil.ignore_patterns("__pycache__"))
        # 注入一个代码里有、文档没有的假 env
        cfg = tmp / "v5" / "config.py"
        cfg.write_text(cfg.read_text(encoding="utf-8") + '\nFAKE = os.environ.get("SHORTDRAMA_FAKE_SELFTEST", "0")\n',
                       encoding="utf-8")
        (tmp / "README.md").write_text(read(ROOT / "README.md"), encoding="utf-8")
        (tmp / "AGENTS.md").write_text(read(ROOT / "AGENTS.md"), encoding="utf-8")
        saved = ROOT
        try:
            globals()["ROOT"] = tmp
            names = code_env_names()
            good = check_envs(names)
        finally:
            globals()["ROOT"] = saved
        return not good
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
