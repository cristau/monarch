"""
Monarch Money 4-Week Spending Report
Sends an email summary every Sunday covering the previous 28 days.
Includes investment and debt tracking with period-over-period changes.

Setup:
1. pip install monarchmoney python-dotenv gql==3.5.0
2. Set environment variables (or use a .env file with python-dotenv)
3. Schedule via cron: 0 8 * * 0 python monarch_report.py
"""

import asyncio
import os
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import smtplib
from monarchmoney import MonarchMoney
from dotenv import load_dotenv

load_dotenv()

# =============================================================================
# Configuration - pulled from .env file
# =============================================================================
MONARCH_EMAIL = os.getenv("MONARCH_EMAIL")
MONARCH_PASSWORD = os.getenv("MONARCH_PASSWORD")
MONARCH_MFA_SECRET = os.getenv("MONARCH_MFA_SECRET")

# Email settings
SMTP_SERVER = os.getenv("SMTP_SERVER", "smtp.mail.me.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_EMAIL = os.getenv("SMTP_EMAIL")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")
RECIPIENT_EMAILS = os.getenv("RECIPIENT_EMAILS", "").split(",")


def get_date_range() -> tuple[datetime, datetime]:
    """Returns rolling 28-day range ending yesterday."""
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    end_date = today - timedelta(days=1)  # Yesterday
    start_date = end_date - timedelta(days=27)  # 28 days total
    return start_date, end_date


async def fetch_monarch_data(start_date: datetime, end_date: datetime) -> dict:
    """Fetch cashflow and account data from Monarch Money."""
    mm = MonarchMoney()
    
    # Always do fresh login for reliability in scheduled runs
    await mm.login(
        email=MONARCH_EMAIL,
        password=MONARCH_PASSWORD,
        mfa_secret_key=MONARCH_MFA_SECRET,
        save_session=False,
        use_saved_session=False
    )
    
    start_str = start_date.strftime("%Y-%m-%d")
    end_str = end_date.strftime("%Y-%m-%d")
    
    # Fetch cashflow summary and detailed breakdown
    cashflow_summary = await mm.get_cashflow_summary(
        start_date=start_str,
        end_date=end_str
    )
    
    cashflow = await mm.get_cashflow(
        start_date=start_str,
        end_date=end_str
    )
    
    # Fetch account snapshots for investments and debt tracking
    # Go back 56 days to get comparison period
    snapshot_start = (end_date - timedelta(days=56)).strftime("%Y-%m-%d")
    
    brokerage_snapshots = await mm.get_aggregate_snapshots(
        start_date=snapshot_start,
        end_date=end_str,
        account_type='brokerage'
    )
    
    loan_snapshots = await mm.get_aggregate_snapshots(
        start_date=snapshot_start,
        end_date=end_str,
        account_type='loan'
    )
    
    credit_snapshots = await mm.get_aggregate_snapshots(
        start_date=snapshot_start,
        end_date=end_str,
        account_type='credit'
    )
    
    return {
        "summary": cashflow_summary,
        "cashflow": cashflow,
        "brokerage_snapshots": brokerage_snapshots,
        "loan_snapshots": loan_snapshots,
        "credit_snapshots": credit_snapshots,
    }


def get_snapshot_values(snapshots: dict) -> tuple[float, float, str, str]:
    """
    Extract current value and comparison value from snapshots.
    Returns: (current_value, previous_value, comparison_label, comparison_date)
    """
    data = snapshots.get('aggregateSnapshots', [])
    if not data:
        return 0, 0, "N/A", ""
    
    # Current value is the last entry
    current = data[-1].get('balance', 0)
    
    # Try to get value from 28 days ago, otherwise use earliest available
    if len(data) >= 28:
        previous = data[-28].get('balance', 0)
        comparison_date = data[-28].get('date', '')
        label = "vs 4 weeks ago"
    else:
        previous = data[0].get('balance', 0)
        comparison_date = data[0].get('date', '')
        days_back = len(data)
        label = f"vs {days_back} days ago"
    
    return current, previous, label, comparison_date


def format_currency(amount: float, show_sign: bool = False) -> str:
    """Format a number as currency."""
    if show_sign and amount < 0:
        return f"-${abs(amount):,.2f}"
    elif show_sign and amount > 0:
        return f"+${abs(amount):,.2f}"
    return f"${abs(amount):,.2f}"


def format_change(current: float, previous: float) -> tuple[str, str]:
    """Format the change between two values. Returns (change_str, css_class)."""
    if previous == 0:
        return "N/A", "neutral"
    
    change = current - previous
    pct = ((current - previous) / abs(previous)) * 100
    
    if change >= 0:
        return f"+${abs(change):,.2f} ({pct:+.1f}%)", "positive"
    else:
        return f"-${abs(change):,.2f} ({pct:+.1f}%)", "negative"


def format_debt_change(current: float, previous: float) -> tuple[str, str]:
    """Format debt change (negative change is good). Returns (change_str, css_class)."""
    if previous == 0:
        return "N/A", "neutral"
    
    # For debt, current and previous are negative, so we work with absolutes
    current_abs = abs(current)
    previous_abs = abs(previous)
    change = previous_abs - current_abs  # Positive means debt decreased (good)
    
    if change >= 0:
        return f"↓ ${abs(change):,.2f}", "positive"
    else:
        return f"↑ ${abs(change):,.2f}", "negative"


def build_email_body(data: dict, start_date: datetime, end_date: datetime) -> tuple[str, str]:
    """Build plain text and HTML email bodies from Monarch data."""
    
    summary = data.get("summary", {})
    cashflow = data.get("cashflow", {})
    
    # Extract summary values
    summary_list = summary.get("summary", [])
    if summary_list and len(summary_list) > 0:
        summary_data = summary_list[0].get("summary", {})
    else:
        summary_data = {}
    
    income = summary_data.get("sumIncome", 0) or 0
    expenses = abs(summary_data.get("sumExpense", 0) or 0)
    savings = summary_data.get("savings", 0) or 0
    savings_rate = (summary_data.get("savingsRate", 0) or 0) * 100
    
    # Get investment and debt snapshots
    inv_current, inv_previous, inv_label, inv_compare_date = get_snapshot_values(data.get("brokerage_snapshots", {}))
    loan_current, loan_previous, loan_label, loan_compare_date = get_snapshot_values(data.get("loan_snapshots", {}))
    credit_current, credit_previous, credit_label, credit_compare_date = get_snapshot_values(data.get("credit_snapshots", {}))
    
    # Format comparison date for display
    if inv_compare_date:
        try:
            compare_date_formatted = datetime.strptime(inv_compare_date, "%Y-%m-%d").strftime("%b %d")
        except:
            compare_date_formatted = inv_compare_date
    else:
        compare_date_formatted = "N/A"
    
    # Total debt (loans + credit cards)
    total_debt = abs(loan_current) + abs(credit_current)
    total_debt_previous = abs(loan_previous) + abs(credit_previous)
    
    # Format changes
    inv_change_str, inv_change_class = format_change(inv_current, inv_previous)
    debt_change_str, debt_change_class = format_debt_change(
        loan_current + credit_current, 
        loan_previous + credit_previous
    )
    mortgage_change_str, mortgage_change_class = format_debt_change(loan_current, loan_previous)
    credit_change_str, credit_change_class = format_debt_change(credit_current, credit_previous)
    
    # Get category breakdown
    categories_raw = cashflow.get("byCategory", []) or []
    category_lines = []
    for cat in categories_raw:
        cat_info = cat.get("groupBy", {}).get("category", {})
        cat_name = cat_info.get("name", "Unknown")
        cat_group = cat_info.get("group", {})
        cat_type = cat_group.get("type", "")
        cat_sum = cat.get("summary", {}).get("sum", 0)
        if cat_sum < 0 and cat_type == "expense":
            category_lines.append((cat_name, abs(cat_sum)))
    category_lines.sort(key=lambda x: x[1], reverse=True)
    
    # Date range string
    date_range = f"{start_date.strftime('%b %d')} - {end_date.strftime('%b %d, %Y')}"
    
    # Plain text version
    plain_text = f"""
4-Week Financial Summary
{date_range}
{'=' * 50}

NET WORTH SNAPSHOT
------------------
Investments:    {format_currency(inv_current)}  ({inv_change_str} {inv_label})
Total Debt:     {format_currency(total_debt)}  ({debt_change_str} {loan_label})
  - Mortgage:   {format_currency(abs(loan_current))}
  - Credit:     {format_currency(abs(credit_current))}

CASHFLOW
--------
Income:       {format_currency(income)}
Expenses:     {format_currency(expenses)}
Net Savings:  {format_currency(savings, show_sign=True)}
Savings Rate: {savings_rate:.1f}%

SPENDING BY CATEGORY
--------------------
"""
    for cat_name, amount in category_lines[:15]:
        plain_text += f"{cat_name:.<30} {format_currency(amount):>10}\n"
    
    plain_text += f"\n{'=' * 50}\nGenerated by Monarch Money 4-Week Report"
    
    # HTML version
    savings_class = "positive" if savings >= 0 else "negative"
    savings_display = format_currency(savings, show_sign=True)
    
    html = f"""
<!DOCTYPE html>
<html>
<head>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px; background: #f5f5f5; }}
        .container {{ background: white; border-radius: 12px; padding: 30px; box-shadow: 0 2px 8px rgba(0,0,0,0.1); }}
        h1 {{ color: #1a1a1a; font-size: 24px; margin-bottom: 5px; }}
        .date-range {{ color: #666; font-size: 14px; margin-bottom: 30px; }}
        .section-title {{ font-size: 14px; font-weight: 600; color: #666; text-transform: uppercase; letter-spacing: 1px; margin: 25px 0 15px 0; padding-bottom: 8px; border-bottom: 2px solid #eee; }}
        .net-worth-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 15px; margin-bottom: 25px; }}
        .nw-card {{ background: #f8f9fa; border-radius: 8px; padding: 15px; }}
        .nw-card.investments {{ background: #e8f5e9; }}
        .nw-card.debt {{ background: #fff3e0; }}
        .nw-label {{ font-size: 12px; color: #666; text-transform: uppercase; letter-spacing: 0.5px; }}
        .nw-value {{ font-size: 22px; font-weight: 600; color: #1a1a1a; margin: 5px 0; }}
        .nw-change {{ font-size: 13px; }}
        .nw-change.positive {{ color: #2e7d32; }}
        .nw-change.negative {{ color: #c62828; }}
        .nw-change.neutral {{ color: #666; }}
        .debt-breakdown {{ font-size: 12px; color: #666; margin-top: 8px; }}
        .summary-grid {{ display: grid; grid-template-columns: repeat(2, 1fr); gap: 15px; margin-bottom: 25px; }}
        .summary-card {{ background: #f8f9fa; border-radius: 8px; padding: 15px; }}
        .summary-card.income {{ background: #e8f5e9; }}
        .summary-card.expenses {{ background: #ffebee; }}
        .summary-card.savings {{ background: #e3f2fd; }}
        .summary-card.rate {{ background: #fff3e0; }}
        .card-label {{ font-size: 12px; color: #666; text-transform: uppercase; letter-spacing: 0.5px; }}
        .card-value {{ font-size: 24px; font-weight: 600; color: #1a1a1a; margin-top: 5px; }}
        .card-value.positive {{ color: #2e7d32; }}
        .card-value.negative {{ color: #c62828; }}
        .category-row {{ display: flex; justify-content: space-between; padding: 10px 0; border-bottom: 1px solid #eee; }}
        .category-row:last-child {{ border-bottom: none; }}
        .category-name {{ color: #333; }}
        .category-amount {{ font-weight: 500; color: #1a1a1a; }}
        .footer {{ text-align: center; color: #999; font-size: 12px; margin-top: 30px; }}
    </style>
</head>
<body>
    <div class="container">
        <h1>💰 4-Week Financial Summary</h1>
        <div class="date-range">{date_range}</div>
        
        <div class="section-title">📈 Net Worth Snapshot</div>
        <div style="font-size: 12px; color: #888; margin-bottom: 12px;">Changes compared to {compare_date_formatted}</div>
        <div class="net-worth-grid">
            <div class="nw-card investments">
                <div class="nw-label">Investments</div>
                <div class="nw-value">{format_currency(inv_current)}</div>
                <div class="nw-change {inv_change_class}">{inv_change_str}</div>
            </div>
            <div class="nw-card debt">
                <div class="nw-label">Total Debt</div>
                <div class="nw-value">{format_currency(total_debt)}</div>
                <div class="nw-change {debt_change_class}">{debt_change_str}</div>
                <div class="debt-breakdown" style="margin-top: 12px; padding-top: 10px; border-top: 1px solid rgba(0,0,0,0.1);">
                    <div style="display: flex; justify-content: space-between; margin-bottom: 4px;">
                        <span>Mortgage:</span>
                        <span>{format_currency(abs(loan_current))} <span class="{mortgage_change_class}" style="font-size: 11px;">({mortgage_change_str})</span></span>
                    </div>
                    <div style="display: flex; justify-content: space-between;">
                        <span>Credit Cards:</span>
                        <span>{format_currency(abs(credit_current))} <span class="{credit_change_class}" style="font-size: 11px;">({credit_change_str})</span></span>
                    </div>
                </div>
            </div>
        </div>
        
        <div class="section-title">💵 Cashflow</div>
        <div class="summary-grid">
            <div class="summary-card income">
                <div class="card-label">Income</div>
                <div class="card-value positive">{format_currency(income)}</div>
            </div>
            <div class="summary-card expenses">
                <div class="card-label">Expenses</div>
                <div class="card-value negative">{format_currency(expenses)}</div>
            </div>
            <div class="summary-card savings">
                <div class="card-label">Net Savings</div>
                <div class="card-value {savings_class}">{savings_display}</div>
            </div>
            <div class="summary-card rate">
                <div class="card-label">Savings Rate</div>
                <div class="card-value">{savings_rate:.1f}%</div>
            </div>
        </div>
        
        <div class="section-title">📊 Spending by Category</div>
        <div class="categories">
"""
    
    for cat_name, amount in category_lines[:15]:
        html += f"""
            <div class="category-row">
                <span class="category-name">{cat_name}</span>
                <span class="category-amount">{format_currency(amount)}</span>
            </div>
"""
    
    html += """
        </div>
        <div class="footer">Generated by Monarch Money 4-Week Report</div>
    </div>
</body>
</html>
"""
    
    return plain_text, html


def send_email(subject: str, plain_text: str, html: str):
    """Send the email via SMTP."""
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = SMTP_EMAIL
    msg["To"] = ", ".join(RECIPIENT_EMAILS)
    
    msg.attach(MIMEText(plain_text, "plain"))
    msg.attach(MIMEText(html, "html"))
    
    with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
        server.starttls()
        server.login(SMTP_EMAIL, SMTP_PASSWORD)
        server.sendmail(SMTP_EMAIL, RECIPIENT_EMAILS, msg.as_string())
    
    print(f"Email sent to {RECIPIENT_EMAILS}")


async def main():
    """Main entry point."""
    print("Generating 4-week Monarch Money report...")
    
    # Get date range for rolling 28 days
    start_date, end_date = get_date_range()
    print(f"Date range: {start_date.strftime('%Y-%m-%d')} to {end_date.strftime('%Y-%m-%d')}")
    
    # Fetch data from Monarch
    print("Fetching data from Monarch Money...")
    data = await fetch_monarch_data(start_date, end_date)
    
    # Build email content
    print("Building email...")
    plain_text, html = build_email_body(data, start_date, end_date)
    
    # Send email
    subject = f"💰 4-Week Spending Report ({start_date.strftime('%b %d')} - {end_date.strftime('%b %d')})"
    send_email(subject, plain_text, html)
    
    print("Done!")


if __name__ == "__main__":
    asyncio.run(main())
