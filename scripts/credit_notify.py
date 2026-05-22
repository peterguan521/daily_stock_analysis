#!/usr/bin/env python3
"""
信用分析报告邮件发送器
直接读取 DSA 的分析结果文件，生成纯信用视角的报告并发送邮件。
"""
import os, smtplib, sys
from datetime import datetime
from email.header import Header
from email.mime.text import MIMEText
from email.utils import formataddr
from pathlib import Path

SMTP_CONFIGS = {
    "qq.com": {"server": "smtp.qq.com", "port": 465, "ssl": True},
}

def get_latest_report(reports_dir: str = "reports") -> str:
    p = Path(reports_dir)
    if not p.exists():
        return ""
    md_files = sorted(p.glob("*.md"), key=lambda f: f.stat().st_mtime, reverse=True)
    if not md_files:
        return ""
    return md_files[0].read_text(encoding="utf-8")

def extract_credit_sections(report_text: str) -> str:
    if not report_text:
        return ""
    lines = report_text.split("\n")
    filtered = []
    skip_section = False

    TRADING_HEADERS = [
        "技术面分析", "均线", "量能", "筹码", "市场展望",
        "走势分析", "数据透视", "作战计划", "狙击点位",
        "仓位策略", "检查清单", "技术指标",
    ]
    CREDIT_HEADERS = [
        "信用评分", "信用等级", "信用决策", "信用质量",
        "财务健康", "流动性", "偿债能力", "现金回收",
        "信用分析", "信用评估", "操作理由", "风险提示",
        "基本面", "行业", "DSO", "回款", "应收账款",
        "集中度", "争议",
    ]

    for line in lines:
        stripped = line.strip()
        if skip_section:
            if not stripped:
                continue
            if stripped.startswith("###") or stripped.startswith("##") or stripped.startswith("---"):
                skip_section = False
                if any(t in stripped for t in CREDIT_HEADERS):
                    filtered.append(line)
                else:
                    skip_section = True
                continue
            else:
                continue
        is_trading_line = any(
            ind in stripped for ind in [
                "MA5", "MA10", "MA20", "买入", "卖出", "建仓", "止损", "止盈",
                "目标位", "支撑位", "压力位", "放量", "缩量", "成交量", "换手率",
                "主力资金", "北向资金",
            ]
        )
        if is_trading_line and ("|" in stripped or "：" in stripped or ":" in stripped):
            continue
        filtered.append(line)
    return "\n".join(filtered)

def send_email(sender, password, receivers, content):
    domain = sender.split("@")[-1].lower()
    config = SMTP_CONFIGS.get(domain)
    if not config:
        print(f"不支持的邮箱域名: {domain}", file=sys.stderr)
        return False
    date_str = datetime.now().strftime("%Y-%m-%d")
    subject = f"客户信用评估日报 - {date_str}"
    msg = MIMEText(content, "plain", "utf-8")
    msg["Subject"] = Header(subject, "utf-8")
    msg["From"] = formataddr((str(Header("信用分析助手", "utf-8")), sender))
    msg["To"] = ", ".join(receivers)
    try:
        if config["ssl"]:
            server = smtplib.SMTP_SSL(config["server"], config["port"], timeout=30)
        else:
            server = smtplib.SMTP(config["server"], config["port"], timeout=30)
            server.starttls()
        server.login(sender, password)
        server.send_message(msg)
        server.quit()
        print(f"信用报告已发送到: {receivers}")
        return True
    except Exception as e:
        print(f"邮件发送失败: {e}", file=sys.stderr)
        return False

def main():
    sender = os.getenv("EMAIL_SENDER", "")
    password = os.getenv("EMAIL_PASSWORD", "")
    receivers = [r.strip() for r in os.getenv("EMAIL_RECEIVERS", "").split(",") if r.strip()]
    report_text = get_latest_report()
    today = datetime.now().strftime("%Y-%m-%d %H:%M")

    if report_text:
        credit_content = extract_credit_sections(report_text)
    else:
        credit_content = "（本次分析未生成详细报告）"

    content = f"""# 客户信用评估日报 - {today}

*本报告由 AI 自动生成，仅供内部参考，需人工复核后方可用于信用决策。*

{credit_content}

---
*⚠️ 声明：本报告基于公开财务数据和 AI 分析生成，不构成信用决策的唯一依据。*
"""
    print(f"信用报告内容长度: {len(content)} 字符")
    if send_email(sender, password, receivers, content):
        print("信用分析通知发送成功")
    else:
        print("信用分析通知发送失败", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
