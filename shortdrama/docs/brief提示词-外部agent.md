# 给外部 agent 的 brief 提示词（简版）

把下面这段直接发给它，前面加一句你想拍什么。

---

给我写一份短剧 brief，存到 `projects/<项目名>/brief.json`（项目名用小写英文）。
字段：topic、pack、genre、episodes、target_duration、protagonist、second_character、
must_have、key_props、禁忌、tone、结局、空间数要求、audio_mode、分镜格式硬要求。

必须做到这六条：

1. 写出 **3 个不同的地点**，每个一句话说清长什么样、光是什么颜色。别全片一个房间。
2. 要求 **每集至少 4 个镜头是全景或空镜**（人在环境里，不是只有脸）。
3. 要求 **每个镜头人物都要做一件事**：走过去、抢过来、放下、推开、追出去。不许只站着说话。
4. 剧情最关键的 **那个动作单独占一个镜头，给 8 到 12 秒**，写清楚"开始前是什么样、结束后是什么样"。
5. 每个角色 **只穿一套衣服，颜色写死**（比如"绛紫绸衫"，不要写"深色衣服"），并写明"不许换装、不许穿别人的"。每个道具 **写多大**（比如"约 25 厘米见方"）。
6. 剧情用 **旁白** 讲：`audio_mode` 写 `narration-led`，台词少而短，一句不超过 18 个字。

类型包（`pack`）从这几个里挑：`shortdrama` 写实｜`chinese-style-short-drama` 国风｜
`half-narrated-live-action` 半解说真人｜`madfate-grim` 惊悚｜`wool-felt-story-short` 羊毛毡。

写完把文件路径给我，并逐条说这六条各写在哪一句。

跑的时候用这条命令（别去掉 `--stills-qc`，那是画面质检）：

```bash
PYTHONUTF8=1 .venv/Scripts/python.exe scripts/run_new_project.py <项目名> --stills-qc
```
