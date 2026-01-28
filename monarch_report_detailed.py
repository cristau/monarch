"""
Monarch Money 4-Week DETAILED Report
Sends a detailed email summary with period-over-period changes for all line items.

Setup:
1. pip install monarchmoney python-dotenv gql==3.5.0
2. Set environment variables (or use a .env file with python-dotenv)
3. Schedule via cron or GitHub Actions
"""

import asyncio
import os
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import smtplib
from monarchmoney import MonarchMoney
from dotenv import load_dotenv

# load_dotenv() # not needed for github actions

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


def get_previous_period_range(start_date: datetime, end_date: datetime) -> tuple[datetime, datetime]:
    """Returns the previous 28-day period for comparison."""
    prev_end = start_date - timedelta(days=1)
    prev_start = prev_end - timedelta(days=27)
    return prev_start, prev_end


async def fetch_monarch_data(start_date: datetime, end_date: datetime, prev_start: datetime, prev_end: datetime) -> dict:
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
    prev_start_str = prev_start.strftime("%Y-%m-%d")
    prev_end_str = prev_end.strftime("%Y-%m-%d")
    
    # Fetch cashflow for current period
    cashflow_summary = await mm.get_cashflow_summary(
        start_date=start_str,
        end_date=end_str
    )
    
    cashflow = await mm.get_cashflow(
        start_date=start_str,
        end_date=end_str
    )
    
    # Fetch cashflow for previous period (for category comparison)
    prev_cashflow = await mm.get_cashflow(
        start_date=prev_start_str,
        end_date=prev_end_str
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
    
    depository_snapshots = await mm.get_aggregate_snapshots(
        start_date=snapshot_start,
        end_date=end_str,
        account_type='depository'
    )
    
    real_estate_snapshots = await mm.get_aggregate_snapshots(
        start_date=snapshot_start,
        end_date=end_str,
        account_type='real_estate'
    )
    
    return {
        "summary": cashflow_summary,
        "cashflow": cashflow,
        "prev_cashflow": prev_cashflow,
        "brokerage_snapshots": brokerage_snapshots,
        "loan_snapshots": loan_snapshots,
        "credit_snapshots": credit_snapshots,
        "depository_snapshots": depository_snapshots,
        "real_estate_snapshots": real_estate_snapshots,
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


def format_change_simple(current: float, previous: float) -> tuple[str, str]:
    """Format change without percentage. Returns (change_str, css_class)."""
    if previous == 0 and current == 0:
        return "—", "neutral"
    
    change = current - previous
    
    if change > 0:
        return f"+${abs(change):,.2f}", "positive"
    elif change < 0:
        return f"-${abs(change):,.2f}", "negative"
    else:
        return "—", "neutral"


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


def format_category_change(current: float, previous: float) -> tuple[str, str]:
    """Format category spending change. Less spending = positive. Returns (change_str, css_class)."""
    change = current - previous
    
    if abs(change) < 0.01:
        return "—", "neutral"
    elif change > 0:
        # Spent more = negative
        return f"↑ ${abs(change):,.0f}", "negative"
    else:
        # Spent less = positive
        return f"↓ ${abs(change):,.0f}", "positive"


def build_email_body(data: dict, start_date: datetime, end_date: datetime, prev_start: datetime, prev_end: datetime) -> tuple[str, str]:
    """Build plain text and HTML email bodies from Monarch data."""
    
    summary = data.get("summary", {})
    cashflow = data.get("cashflow", {})
    prev_cashflow = data.get("prev_cashflow", {})
    
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
    cash_current, cash_previous, cash_label, cash_compare_date = get_snapshot_values(data.get("depository_snapshots", {}))
    real_estate_current, real_estate_previous, re_label, re_compare_date = get_snapshot_values(data.get("real_estate_snapshots", {}))
    
    # Format comparison date for display
    if inv_compare_date:
        try:
            compare_date_formatted = datetime.strptime(inv_compare_date, "%Y-%m-%d").strftime("%b %d")
        except:
            compare_date_formatted = inv_compare_date
    else:
        compare_date_formatted = "N/A"
    
    # Calculate Net Worth (assets - liabilities)
    total_assets = inv_current + cash_current + real_estate_current
    total_liabilities = abs(loan_current) + abs(credit_current)
    net_worth_current = total_assets - total_liabilities
    
    total_assets_previous = inv_previous + cash_previous + real_estate_previous
    total_liabilities_previous = abs(loan_previous) + abs(credit_previous)
    net_worth_previous = total_assets_previous - total_liabilities_previous
    
    # Format net worth change
    nw_change_str, nw_change_class = format_change(net_worth_current, net_worth_previous)
    
    # Total debt
    total_debt = abs(loan_current) + abs(credit_current)
    
    # Format all changes
    assets_change_str, assets_change_class = format_change(total_assets, total_assets_previous)
    inv_change_str, inv_change_class = format_change(inv_current, inv_previous)
    real_estate_change_str, real_estate_change_class = format_change(real_estate_current, real_estate_previous)
    cash_change_str, cash_change_class = format_change_simple(cash_current, cash_previous)
    
    debt_change_str, debt_change_class = format_debt_change(
        loan_current + credit_current, 
        loan_previous + credit_previous
    )
    mortgage_change_str, mortgage_change_class = format_debt_change(loan_current, loan_previous)
    credit_change_str, credit_change_class = format_debt_change(credit_current, credit_previous)
    
    # Get category breakdown with comparison to previous period
    categories_raw = cashflow.get("byCategory", []) or []
    prev_categories_raw = prev_cashflow.get("byCategory", []) or []
    
    # Build previous period lookup
    prev_category_totals = {}
    for cat in prev_categories_raw:
        cat_info = cat.get("groupBy", {}).get("category", {})
        cat_name = cat_info.get("name", "Unknown")
        cat_sum = cat.get("summary", {}).get("sum", 0)
        if cat_sum < 0:
            prev_category_totals[cat_name] = abs(cat_sum)
    
    # Current period with changes
    category_lines = []
    for cat in categories_raw:
        cat_info = cat.get("groupBy", {}).get("category", {})
        cat_name = cat_info.get("name", "Unknown")
        cat_group = cat_info.get("group", {})
        cat_type = cat_group.get("type", "")
        cat_sum = cat.get("summary", {}).get("sum", 0)
        if cat_sum < 0 and cat_type == "expense":
            current_amount = abs(cat_sum)
            prev_amount = prev_category_totals.get(cat_name, 0)
            change_str, change_class = format_category_change(current_amount, prev_amount)
            category_lines.append((cat_name, current_amount, change_str, change_class))
    
    category_lines.sort(key=lambda x: x[1], reverse=True)
    
    # Date range strings
    date_range = f"{start_date.strftime('%b %d')} - {end_date.strftime('%b %d, %Y')}"
    prev_date_range = f"{prev_start.strftime('%b %d')} - {prev_end.strftime('%b %d')}"
    
    # Plain text version
    plain_text = f"""
4-Week DETAILED Financial Report
{date_range}
{'=' * 50}

NET WORTH: {format_currency(net_worth_current, show_sign=True)}
Change: {nw_change_str} (compared to {compare_date_formatted})

ASSETS: {format_currency(total_assets)} ({assets_change_str})
  - Real Estate:  {format_currency(real_estate_current)} ({real_estate_change_str})
  - Investments:  {format_currency(inv_current)} ({inv_change_str})
  - Cash:         {format_currency(cash_current)} ({cash_change_str})

LIABILITIES: {format_currency(total_debt)} ({debt_change_str})
  - Mortgage:     {format_currency(abs(loan_current))} ({mortgage_change_str})
  - Credit Cards: {format_currency(abs(credit_current))} ({credit_change_str})

SPENDING BY CATEGORY (vs {prev_date_range})
-------------------------------------------
"""
    for cat_name, amount, change_str, _ in category_lines[:15]:
        plain_text += f"{cat_name:.<25} {format_currency(amount):>10}  {change_str}\n"
    
    plain_text += f"\n{'=' * 50}\nGenerated by Monarch Money Detailed Report"
    
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
        .date-range {{ color: #666; font-size: 14px; margin-bottom: 20px; }}
        .net-worth-hero {{ background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%); border-radius: 12px; padding: 25px; margin-bottom: 25px; text-align: center; }}
        .net-worth-hero .label {{ color: rgba(255,255,255,0.7); font-size: 12px; text-transform: uppercase; letter-spacing: 1px; }}
        .net-worth-hero .value {{ color: white; font-size: 36px; font-weight: 700; margin: 10px 0; }}
        .net-worth-hero .change {{ font-size: 14px; }}
        .net-worth-hero .change.positive {{ color: #4ade80; }}
        .net-worth-hero .change.negative {{ color: #f87171; }}
        .net-worth-hero .compare-date {{ color: rgba(255,255,255,0.5); font-size: 11px; margin-top: 8px; }}
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
        .line-item {{ display: flex; justify-content: space-between; align-items: center; padding: 8px 0; border-bottom: 1px solid rgba(0,0,0,0.05); }}
        .line-item:last-child {{ border-bottom: none; }}
        .line-item-name {{ color: #666; font-size: 13px; }}
        .line-item-value {{ font-weight: 500; font-size: 13px; }}
        .line-item-change {{ font-size: 11px; margin-left: 8px; }}
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
        .category-row {{ display: flex; justify-content: space-between; align-items: center; padding: 10px 0; border-bottom: 1px solid #eee; }}
        .category-row:last-child {{ border-bottom: none; }}
        .category-name {{ color: #333; flex: 1; }}
        .category-amount {{ font-weight: 500; color: #1a1a1a; min-width: 80px; text-align: right; }}
        .category-change {{ font-size: 12px; min-width: 70px; text-align: right; margin-left: 10px; }}
        .category-change.positive {{ color: #2e7d32; }}
        .category-change.negative {{ color: #c62828; }}
        .category-change.neutral {{ color: #999; }}
        .positive {{ color: #2e7d32; }}
        .negative {{ color: #c62828; }}
        .neutral {{ color: #666; }}
        .footer {{ text-align: center; color: #999; font-size: 12px; margin-top: 30px; }}
        .compare-note {{ font-size: 11px; color: #999; margin-bottom: 10px; }}
    </style>
</head>
<body>
    <div class="container">
        <h1>📊 4-Week Detailed Financial Report</h1>
        <div class="date-range">{date_range}</div>
        
        <div class="net-worth-hero">
            <div class="label">Net Worth</div>
            <div class="value">{format_currency(net_worth_current, show_sign=True)}</div>
            <div class="change {nw_change_class}">{nw_change_str}</div>
            <div class="compare-date">compared to {compare_date_formatted}</div>
        </div>
        
        <div class="section-title">📈 Assets & Liabilities</div>
        <div class="net-worth-grid">
            <div class="nw-card investments">
                <div class="nw-label">Total Assets</div>
                <div class="nw-value">{format_currency(total_assets)}</div>
                <div class="nw-change {assets_change_class}">{assets_change_str}</div>
                <div style="margin-top: 14px; padding-top: 12px; border-top: 1px solid rgba(0,0,0,0.1);">
                    <div class="line-item">
                        <span class="line-item-name">Real Estate</span>
                        <span>
                            <span class="line-item-value">{format_currency(real_estate_current)}</span>
                            <span class="line-item-change {real_estate_change_class}">{real_estate_change_str}</span>
                        </span>
                    </div>
                    <div class="line-item">
                        <span class="line-item-name">Investments</span>
                        <span>
                            <span class="line-item-value">{format_currency(inv_current)}</span>
                            <span class="line-item-change {inv_change_class}">{inv_change_str}</span>
                        </span>
                    </div>
                    <div class="line-item">
                        <span class="line-item-name">Cash</span>
                        <span>
                            <span class="line-item-value">{format_currency(cash_current)}</span>
                            <span class="line-item-change {cash_change_class}">{cash_change_str}</span>
                        </span>
                    </div>
                </div>
            </div>
            <div class="nw-card debt">
                <div class="nw-label">Total Liabilities</div>
                <div class="nw-value">{format_currency(total_debt)}</div>
                <div class="nw-change {debt_change_class}">{debt_change_str}</div>
                <div style="margin-top: 14px; padding-top: 12px; border-top: 1px solid rgba(0,0,0,0.1);">
                    <div class="line-item">
                        <span class="line-item-name">Mortgage</span>
                        <span>
                            <span class="line-item-value">{format_currency(abs(loan_current))}</span>
                            <span class="line-item-change {mortgage_change_class}">{mortgage_change_str}</span>
                        </span>
                    </div>
                    <div class="line-item">
                        <span class="line-item-name">Credit Cards</span>
                        <span>
                            <span class="line-item-value">{format_currency(abs(credit_current))}</span>
                            <span class="line-item-change {credit_change_class}">{credit_change_str}</span>
                        </span>
                    </div>
                </div>
            </div>
        </div>
        
        <div class="section-title">📊 Spending by Category</div>
        <div class="compare-note">vs previous period ({prev_date_range})</div>
        <div class="categories">
"""
    
    for cat_name, amount, change_str, change_class in category_lines[:15]:
        html += f"""
            <div class="category-row">
                <span class="category-name">{cat_name}</span>
                <span class="category-amount">{format_currency(amount)}</span>
                <span class="category-change {change_class}">{change_str}</span>
            </div>
"""
    
    html += """
        </div>
        <div class="footer">Generated by Monarch Money Detailed Report</div>
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
    print("Generating 4-week DETAILED Monarch Money report...")
    
    # Get date ranges
    start_date, end_date = get_date_range()
    prev_start, prev_end = get_previous_period_range(start_date, end_date)
    
    print(f"Current period: {start_date.strftime('%Y-%m-%d')} to {end_date.strftime('%Y-%m-%d')}")
    print(f"Previous period: {prev_start.strftime('%Y-%m-%d')} to {prev_end.strftime('%Y-%m-%d')}")
    
    # Fetch data from Monarch
    print("Fetching data from Monarch Money...")
    data = await fetch_monarch_data(start_date, end_date, prev_start, prev_end)
    
    # Build email content
    print("Building email...")
    plain_text, html = build_email_body(data, start_date, end_date, prev_start, prev_end)
    
    # Send email
    subject = f"📊 4-Week Detailed Report ({start_date.strftime('%b %d')} - {end_date.strftime('%b %d')})"
    send_email(subject, plain_text, html)
    
    print("Done!")


if __name__ == "__main__":
    asyncio.run(main())
