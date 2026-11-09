from __future__ import annotations

from pathlib import Path

from docx import Document
from docx.enum.section import WD_SECTION
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT, WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "CURRENT-STATUS-2026-11-09.docx"
UI = ROOT / "ui-dashboard.png"

NAVY = "0F172A"
BLUE = "2563EB"
TEAL = "0F766E"
MUTED = "64748B"
LIGHT = "F1F5F9"
PALE_BLUE = "EAF2FF"
PALE_GREEN = "E9F9EF"
PALE_AMBER = "FFF7E6"
WHITE = "FFFFFF"


def set_font(run, name="Microsoft YaHei", size=None, color=None, bold=None):
    run.font.name = name
    run._element.rPr.rFonts.set(qn("w:eastAsia"), name)
    if size is not None:
        run.font.size = Pt(size)
    if color is not None:
        run.font.color.rgb = RGBColor.from_string(color)
    if bold is not None:
        run.bold = bold


def shade(cell, fill):
    tc_pr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:fill"), fill)
    tc_pr.append(shd)


def cell_margins(cell, top=90, start=110, bottom=90, end=110):
    tc_pr = cell._tc.get_or_add_tcPr()
    mar = OxmlElement("w:tcMar")
    for tag, value in (("top", top), ("start", start), ("bottom", bottom), ("end", end)):
        node = OxmlElement(f"w:{tag}")
        node.set(qn("w:w"), str(value))
        node.set(qn("w:type"), "dxa")
        mar.append(node)
    tc_pr.append(mar)


def cell_text(cell, text, size=9.5, bold=False, color=NAVY, align=None, space_after=2):
    cell.text = ""
    p = cell.paragraphs[0]
    if align is not None:
        p.alignment = align
    p.paragraph_format.space_after = Pt(space_after)
    p.paragraph_format.line_spacing = 1.25
    run = p.add_run(text)
    set_font(run, size=size, color=color, bold=bold)
    return p


def style_table(table, header_fill=NAVY, header_color=WHITE, size=9.0, widths=None):
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.autofit = False
    if widths:
        for row in table.rows:
            for idx, width in enumerate(widths):
                row.cells[idx].width = Inches(width)
    # Repeat header row when a table crosses pages.
    tr_pr = table.rows[0]._tr.get_or_add_trPr()
    tbl_header = OxmlElement("w:tblHeader")
    tbl_header.set(qn("w:val"), "true")
    tr_pr.append(tbl_header)
    for row in table.rows:
        tr_pr = row._tr.get_or_add_trPr()
        cant_split = OxmlElement("w:cantSplit")
        tr_pr.append(cant_split)
    for idx, cell in enumerate(table.rows[0].cells):
        shade(cell, header_fill)
        cell_margins(cell)
        for p in cell.paragraphs:
            for run in p.runs:
                set_font(run, size=size, color=header_color, bold=True)
    for row in table.rows[1:]:
        for cell in row.cells:
            cell_margins(cell)
            for p in cell.paragraphs:
                p.paragraph_format.space_after = Pt(2)
                for run in p.runs:
                    set_font(run, size=size, color=NAVY)


def add_table(doc, headers, rows, widths, header_fill=NAVY, size=9.0, fills=None):
    table = doc.add_table(rows=1, cols=len(headers))
    table.style = "Table Grid"
    for idx, text in enumerate(headers):
        cell_text(table.rows[0].cells[idx], text, size=size, bold=True, color=WHITE)
    for row_idx, row in enumerate(rows):
        cells = table.add_row().cells
        for idx, val in enumerate(row):
            cell_text(cells[idx], str(val), size=size)
            if fills and fills[row_idx]:
                shade(cells[idx], fills[row_idx])
    style_table(table, header_fill=header_fill, size=size, widths=widths)
    return table


def add_callout(doc, title, body, fill=PALE_BLUE, accent=BLUE):
    table = doc.add_table(rows=1, cols=1)
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    cell = table.rows[0].cells[0]
    shade(cell, fill)
    cell_margins(cell, top=120, start=160, bottom=120, end=160)
    cell.text = ""
    p = cell.paragraphs[0]
    p.paragraph_format.space_after = Pt(3)
    run = p.add_run(title)
    set_font(run, size=10.5, color=accent, bold=True)
    p2 = cell.add_paragraph()
    p2.paragraph_format.space_after = Pt(0)
    p2.paragraph_format.line_spacing = 1.35
    run2 = p2.add_run(body)
    set_font(run2, size=9.5, color="334155")
    doc.add_paragraph().paragraph_format.space_after = Pt(2)
    return table


def add_code(doc, text):
    table = doc.add_table(rows=1, cols=1)
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    tr_pr = table.rows[0]._tr.get_or_add_trPr()
    tr_pr.append(OxmlElement("w:cantSplit"))
    cell = table.rows[0].cells[0]
    shade(cell, LIGHT)
    cell_margins(cell, top=90, start=130, bottom=90, end=130)
    cell.text = ""
    lines = text.strip("\n").splitlines()
    for i, line in enumerate(lines):
        p = cell.paragraphs[0] if i == 0 else cell.add_paragraph()
        p.paragraph_format.space_after = Pt(0)
        p.paragraph_format.line_spacing = 1.15
        run = p.add_run(line)
        set_font(run, name="Consolas", size=8.5, color="1E293B")
    doc.add_paragraph().paragraph_format.space_after = Pt(2)


def add_caption(doc, text):
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_after = Pt(8)
    run = p.add_run(text)
    set_font(run, size=8.5, color=MUTED)


def add_page_number(paragraph):
    paragraph.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    run = paragraph.add_run("Agent Guild · 当前状态报告  ·  第 ")
    set_font(run, size=8, color=MUTED)
    fld_begin = OxmlElement("w:fldChar")
    fld_begin.set(qn("w:fldCharType"), "begin")
    instr = OxmlElement("w:instrText")
    instr.set(qn("xml:space"), "preserve")
    instr.text = "PAGE"
    fld_end = OxmlElement("w:fldChar")
    fld_end.set(qn("w:fldCharType"), "end")
    run._r.append(fld_begin)
    run._r.append(instr)
    run._r.append(fld_end)
    run2 = paragraph.add_run(" 页")
    set_font(run2, size=8, color=MUTED)


def heading(doc, text, level=1):
    p = doc.add_heading(level=level)
    p.paragraph_format.keep_with_next = True
    run = p.add_run(text)
    if level == 1:
        set_font(run, size=15, color=NAVY, bold=True)
        p.paragraph_format.space_before = Pt(14)
        p.paragraph_format.space_after = Pt(6)
    else:
        set_font(run, size=11.5, color=BLUE, bold=True)
        p.paragraph_format.space_before = Pt(9)
        p.paragraph_format.space_after = Pt(3)
    return p


def body(doc, text, size=10, color="334155", space_after=5):
    p = doc.add_paragraph()
    p.paragraph_format.line_spacing = 1.38
    p.paragraph_format.space_after = Pt(space_after)
    run = p.add_run(text)
    set_font(run, size=size, color=color)
    return p


def bullet(doc, text, color="334155"):
    p = doc.add_paragraph(style="List Bullet")
    p.paragraph_format.space_after = Pt(3)
    p.paragraph_format.line_spacing = 1.3
    run = p.add_run(text)
    set_font(run, size=9.8, color=color)
    return p


def build():
    doc = Document()
    section = doc.sections[0]
    sectionA4 = section
    sectionA4.page_width = Inches(8.27)
    sectionA4.page_height = Inches(11.69)
    sectionA4.left_margin = Inches(0.75)
    sectionA4.right_margin = Inches(0.75)
    sectionA4.top_margin = Inches(0.65)
    sectionA4.bottom_margin = Inches(0.65)

    normal = doc.styles["Normal"]
    normal.font.name = "Microsoft YaHei"
    normal._element.rPr.rFonts.set(qn("w:eastAsia"), "Microsoft YaHei")
    normal.font.size = Pt(10)

    header = sectionA4.header.paragraphs[0]
    header.text = ""
    run = header.add_run("AGENT GUILD  /  CURRENT STATUS")
    set_font(run, size=8, color=MUTED, bold=True)
    header.alignment = WD_ALIGN_PARAGRAPH.RIGHT

    add_page_number(sectionA4.footer.paragraphs[0])

    # Cover band
    cover = doc.add_table(rows=1, cols=1)
    cover.alignment = WD_TABLE_ALIGNMENT.CENTER
    cell = cover.rows[0].cells[0]
    shade(cell, NAVY)
    cell_margins(cell, top=430, start=300, bottom=380, end=300)
    cell.text = ""
    p = cell.paragraphs[0]
    p.paragraph_format.space_after = Pt(8)
    r = p.add_run("AGENT GUILD  /  B 内部试点")
    set_font(r, size=9, color="93C5FD", bold=True)

    p = cell.add_paragraph()
    p.paragraph_format.space_after = Pt(4)
    r = p.add_run("当前状态报告")
    set_font(r, size=30, color=WHITE, bold=True)

    p = cell.add_paragraph()
    p.paragraph_format.space_after = Pt(18)
    r = p.add_run("真实仓库 · 可审计合入 · OR 路由 · 多角色门禁")
    set_font(r, size=12, color="CBD5E1")

    p = cell.add_paragraph()
    p.paragraph_format.space_after = Pt(0)
    r = p.add_run("2026-11-09  ·  Phase 1 完成 / Phase 2-3 首批落地")
    set_font(r, size=9.5, color="94A3B8")

    doc.add_paragraph().paragraph_format.space_after = Pt(6)

    # KPI row
    kpi = doc.add_table(rows=1, cols=4)
    kpi.alignment = WD_TABLE_ALIGNMENT.CENTER
    values = [
        ("91", "单元测试全绿", PALE_GREEN, TEAL),
        ("2", "预约文件一次改完", PALE_BLUE, BLUE),
        ("1", "真实仓库试点闭环", PALE_BLUE, BLUE),
        ("0", "越界合入", PALE_AMBER, "B45309"),
    ]
    for idx, (num, cap, fill, color) in enumerate(values):
        c = kpi.rows[0].cells[idx]
        shade(c, fill)
        cell_margins(c, top=160, start=120, bottom=160, end=120)
        c.text = ""
        p = c.paragraphs[0]
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p.paragraph_format.space_after = Pt(1)
        r = p.add_run(num)
        set_font(r, size=24, color=color, bold=True)
        p2 = c.add_paragraph()
        p2.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p2.paragraph_format.space_after = Pt(0)
        r2 = p2.add_run(cap)
        set_font(r2, size=8, color=MUTED)

    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(10)
    p.paragraph_format.space_after = Pt(2)
    r = p.add_run("报告口径：本报告基于当前工作区、真实仓库试点证据、91 个测试和已构建的共享包；不含未经验证的推断。")
    set_font(r, size=8.5, color=MUTED)

    doc.add_page_break()

    # 0. 结论
    heading(doc, "0. 结论", 1)
    add_callout(
        doc,
        "已经跨过“演示”，还没有跨过“生产”。",
        "Agent Guild 现在能在一个真实多文件仓库上完成：预约 → worktree → 真实 agent → scope → Candidate → Evidence → 人工批准 → 真实 Git cherry-pick → 合并后检查 → 测试通过。"
        " 但身份、远程合入、依赖感知排期、多租户与生产审计仍未落地。",
    )
    body(doc, "一句话定位：多 agent 编程的调度台 + 安全合入流程，不是模型，也不是聊天客户端。")

    # 1. 是什么
    heading(doc, "1. 它解决什么问题", 1)
    body(doc, "如果让多个 AI agent 直接改同一个仓库，常见问题是：互相覆盖、越界修改、谎报完成、自行合入、出问题无法追溯。"
              "Agent Guild 把流程改成：协调器分配任务与文件范围，agent 在独立 worktree 内工作，产物是绑定 SHA 的 Candidate 与 Evidence，人类批准后才执行真实合入。")
    for text in [
        "Agent 不能直接改主仓库，也不能自行合入。",
        "Coordinator 是单一状态源：任务、租约、文件预约、生命周期、预算、审计。",
        "人类保留最终批准权；批准的是具体 Candidate SHA，不是移动分支。",
        "所有结果都有检查记录、审批记录和 Git 历史。",
    ]:
        bullet(doc, text)

    # 2. 能力与状态
    heading(doc, "2. 能力与状态", 1)
    add_table(
        doc,
        ["能力", "状态", "说明"],
        [
            ("协调内核", "完成", "SQLite、任务/写集/依赖、租约/TTL/fencing、文件预约、事件"),
            ("V1 执行闭环", "完成", "Execution、Candidate、Evidence、MergeAttempt、Workspace、lifecycle"),
            ("真实 Git 合入", "完成", "cherry-pick、冲突回滚、合并后检查"),
            ("Adapter", "完成", "DSH、Codex-DeepSeek、Codex-智谱、Codex App Server"),
            ("WebView2 控制台", "可用", "启动 Run、批准、合入、流水线/候选/用量可视化"),
            ("OR 路由", "首批完成", "cost/time/quality 单任务推荐；预算约束批量分配"),
            ("语义/能力标签", "首批完成", "AST diff、breaking 门禁、影响面/ACK、能力标签"),
            ("多用户/角色", "首批完成", "reviewer/admin、项目级 admin 门禁、多级审批链；不是认证"),
            ("Electron", "阻塞", "本机启动阶段 0xC0000005；WebView2 替代"),
            ("A2A / 动态组织", "未开始", "当前是 Coordinator 中介的间接协作"),
            ("文档内核 / 学习", "未开始", "报告是产物，不是运行时内核"),
        ],
        widths=[1.25, 0.95, 4.6],
        fills=[PALE_GREEN, PALE_GREEN, PALE_GREEN, PALE_GREEN, PALE_BLUE, PALE_BLUE, PALE_BLUE, PALE_BLUE, PALE_AMBER, LIGHT, LIGHT],
        size=8.8,
    )

    # 3. 架构
    heading(doc, "3. 当前架构", 1)
    add_code(
        doc,
        "Human / Admin\n"
        "   │  目标、审批、合入确认\n"
        "   ▼\n"
        "WebView2 控制台\n"
        "   │  JSON-RPC\n"
        "   ▼\n"
        "Coordinator (SQLite)\n"
        "   │  任务 / 预约 / 租约 / 生命周期 / 预算 / 审计\n"
        "   ▼\n"
        "Agent Adapter (DSH / Codex / App Server)\n"
        "   │  独立 worktree，只改预约文件\n"
        "   ▼\n"
        "Candidate + Evidence (SHA 绑定)\n"
        "   │  人工批准（可 reviewer → admin）\n"
        "   ▼\n"
        "Integration worktree\n"
        "   │  git cherry-pick + 合并后检查\n"
        "   ▼\n"
        "Accepted",
    )

    # 4. 实证
    heading(doc, "4. 已验证的证据", 1)
    add_callout(
        doc,
        "91 tests OK",
        "覆盖协调器、租约、预约、MILP/Pareto、验证器、V1 生命周期、真实 Git 合并、Adapter、OR 路由、审批角色与多级审批链。",
        fill=PALE_GREEN,
        accent=TEAL,
    )
    heading(doc, "4.1 真实仓库试点", 2)
    add_table(
        doc,
        ["项", "值"],
        [
            ("任务", "T-42d29c0b — Add mul and div with tests"),
            ("Agent", "Codex-DeepSeek"),
            ("修改文件", "src/calc/ops.py、tests/test_ops.py"),
            ("Candidate", "C-2c0b3836"),
            ("Candidate SHA", "4bb45b7e384bf0bfac8eea6c09165a0bcaddf04f"),
            ("合并结果", "96e5f737860a3068de7562bb9d01d766c21ef946"),
            ("合并后检查", "REAL_REPO_CHECK_OK"),
            ("integration 测试", "4 个单元测试全绿"),
            ("负向验证", "检查失败回滚测试通过"),
        ],
        widths=[1.35, 5.45],
        size=8.8,
    )
    body(doc, "基线检查是有意失败的（ImportError: div），agent 完成后才通过；这是一条“先红后绿”的真实门禁，不是脚本自证。")

    heading(doc, "4.2 UI 合入后检查", 2)
    body(doc, "WebView2 的「合入」会把检查命令传给 merge-candidate-git --check。实测 Candidate C-a47c2215 合入结果 51358a57…，check_exit=0，merge_attempt=succeeded。")

    heading(doc, "4.3 OR 与审批策略", 2)
    for text in [
        "Auto 推荐选中 DSH：任务 T-e282ee16，16.6s，Candidate C-4aa9f055，估算成本 $0.000766。",
        "批量分配（4 任务，预算 $0.01）：DSH $0.001 + Codex-DeepSeek $0.006；其余任务因剩余预算不足未分配。",
        "admin-only 项目：reviewer 被拒绝，admin 批准成功，merge_attempt=pending。",
        "多级链 reviewer,admin：reviewer 批准后仍为 awaiting_human，admin 批准后才 approved。",
    ]:
        bullet(doc, text)

    heading(doc, "4.4 其他证据", 2)
    body(doc, "此前还完成了 DSH 5 任务、Codex 1 任务、3-agent 并发、Codex App Server V1 端到端等试点；详细命令、SHA 与 usage 见 pilot/REAL-*.md、pilot/BATCH-DSH.md。")
    body(doc, "这些证据与本次真实仓库试点共同说明：协调、隔离、检查、审批、合入几条链路不是只在单元测试里成立。")

    heading(doc, "4.5 语义与组织 MVP", 2)
    body(doc, "语义级：Python AST 提取 added/removed/signature/body 变化；breaking change 必须 admin 批准；"
              "影响面按 write_set 与显式语义依赖返回，支持 ACK。组织级：Agent 能力标签落库，OR 支持 --require-tag 过滤。")
    body(doc, "全量测试：91 tests OK；语义/能力标签专项 6 tests。")
    bullet(doc, "限制：只支持 Python、无 merge queue、无 LLM 语义审查、能力标签尚未从历史自动校准。")

    # 5. UI
    heading(doc, "5. 控制台", 1)
    body(doc, "控制台提供任务流水线、候选状态环形图、Agent 用量、Adapter 状态、启动 Run、批准、合入，以及任务状态保护：有活跃候选时不再允许重复启动 Run。")
    doc.add_picture(str(UI), width=Inches(6.1))
    add_caption(doc, "图 1：Agent Guild WebView2 控制台（任务流水线 / 候选状态 / Agent 用量 / 候选卡片）")
    body(doc, "报告生成时 WebView2 进程仍在运行（PID 40884），数据库为 .tmp/agent-demo/db.sqlite。")

    # 6. 边界
    heading(doc, "6. 边界与风险", 1)
    add_table(
        doc,
        ["边界", "现状"],
        [
            ("部署", "单机、SQLite"),
            ("用户", "单操作者；角色是审计标签 + 门禁，不是认证"),
            ("合入", "合到 integration worktree，不 push 远程，不开 PR"),
            ("调度", "OR 贪心分配，未做依赖/文件冲突下的全局排期"),
            ("检查", "检查命令由任务/操作者提供，系统不保证检查本身正确"),
            ("仓库", "真实仓库试点是模板仓库，不是生产仓库"),
            ("桌面", "Electron 崩溃；WebView2 可用"),
            ("成本", "token 估算，不是最终账单"),
            ("A2A", "间接协作，无 agent 直接协商"),
            ("文档/学习", "未落地"),
        ],
        widths=[1.05, 5.75],
        size=8.8,
    )
    body(doc, "主要风险：agent 行为非确定，仍需人工 review；检查脚本写错会制造“假绿”；角色不是身份；多 agent 全局调度未验证；生产化所需的审计、权限、远程集成缺失。")

    # 7. 路线图
    heading(doc, "7. 路线图状态", 1)
    add_table(
        doc,
        ["Phase", "状态", "说明"],
        [
            ("Phase 1 真实仓库", "完成", "两文件修改、合并后检查、可重复报告"),
            ("Phase 2 checks/证据/角色", "大部分完成", "任务 checks、evidence gate、reviewer/admin、项目策略、多级链"),
            ("Phase 3 OR", "首批完成", "单任务推荐、批量分配、Auto；依赖感知排期未做"),
            ("Phase 4 A2A/组织", "未开始", "消息总线、协商、动态组队"),
            ("Phase 5 文档/学习", "未开始", "文档内核、失败模式、质量预测"),
        ],
        widths=[1.55, 1.0, 4.25],
        fills=[PALE_GREEN, PALE_GREEN, PALE_BLUE, LIGHT, LIGHT],
        size=8.8,
    )

    # 8. 运行
    heading(doc, "8. 如何运行", 1)
    heading(doc, "8.1 控制台", 2)
    add_code(
        doc,
        '$env:GUILD_DB="...\\.tmp\\agent-demo\\db.sqlite"\n'
        '$env:DEEPSEEK_API_KEY="sk-..."\n'
        "cd p0-coordinator\\webview2\n"
        ".\\bin\\AgentGuildWebView2.exe",
    )
    heading(doc, "8.2 真实仓库试点", 2)
    add_code(doc, "cd C:\\Users\\steve\\Desktop\\探索区\npython pilot\\seed_real_repo.py")
    heading(doc, "8.3 测试", 2)
    add_code(
        doc,
        "cd p0-coordinator\n"
        "python -m unittest \\\n"
        "  test_coordinator test_estimate test_plan_milp test_verifier \\\n"
        "  test_v1_core test_v1_worktree test_v1_lifecycle \\\n"
        "  test_v1_git_merge test_adapters test_or_router -q",
    )

    # 9. 工件
    heading(doc, "9. 工件与下一步", 1)
    for text in [
        "路线图：pilot/ROADMAP-V2.md",
        "真实仓库证据：pilot/REAL-REPO-V2.md",
        "OR 证据：pilot/OR-ROUTER.md",
        "角色与审批：pilot/ROLES.md",
        "控制台截图：pilot/ui-dashboard.png",
        "组织协调图：pilot/org-visual.png",
        "共享包：AgentGuild-成果共享-2026-11-08.zip",
    ]:
        bullet(doc, text)

    body(doc, "建议下一步：用你的真实小仓库替换试点模板；做依赖/文件冲突感知的批量排期；做真实身份与 N-of-M/TTL 审批；再进入 A2A、动态组织、文档内核和学习。")

    add_callout(
        doc,
        "一句话总结",
        "Agent Guild 现在是一个能真实跑、能审计、能合入、能选 agent、能做多级审批的内部原型；它已经跨过“演示”阶段，但还没有跨过“生产”门槛。",
        fill=PALE_AMBER,
        accent="B45309",
    )

    heading(doc, "附录：关键 SHA", 1)
    add_table(
        doc,
        ["证据", "值"],
        [
            ("真实仓库 Candidate", "4bb45b7e384bf0bfac8eea6c09165a0bcaddf04f"),
            ("真实仓库 Merge Result", "96e5f737860a3068de7562bb9d01d766c21ef946"),
            ("UI 合入检查 Candidate", "C-a47c2215"),
            ("UI 合入 Result", "51358a57bb40a6757e3d0167fab98ba5a8695962"),
            ("OR 实测 Candidate", "C-4aa9f055"),
        ],
        widths=[1.65, 5.15],
        size=8.6,
    )
    body(doc, "报告日期：2026-11-09；测试基线：85 tests OK。")

    # core properties
    doc.core_properties.title = "Agent Guild 当前状态报告"
    doc.core_properties.subject = "B 内部试点 · V2 当前状态"
    doc.core_properties.author = "Agent Guild"
    doc.core_properties.comments = "基于 2026-11-09 工作区证据"

    doc.save(OUT)
    print(f"saved {OUT}")


if __name__ == "__main__":
    build()
